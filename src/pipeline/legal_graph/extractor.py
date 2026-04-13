"""Rule-based knowledge graph extractor for Korean legal markdown.

This extractor parses Korean legal documents (e.g. legalize-kr corpus)
using deterministic regular expressions and emits a GraphRAG-compatible
``ExtractionResult`` that can be written to Neo4j via the existing
:class:`GraphRAGExtractor.save_to_neo4j` persistence layer.

Node types produced:
    Law           — 법령 본문 단위 (법률/시행령/시행규칙)
    LegalArticle  — 조 단위 (제N조, 제N조의M)
    Ministry      — 소관 부처

Relationships produced:
    (LegalArticle)-[:PART_OF]->(Law)           — 조가 법령에 속함
    (Law)-[:IMPLEMENTS]->(Law)                 — 시행령/시행규칙 → 상위 법률
    (Ministry)-[:OVERSEES]->(Law)              — 소관 부처
    (LegalArticle)-[:REFERENCES]->(Law)        — 본문 내 「법령」 교차참조
    (LegalArticle)-[:REFERENCES]->(LegalArticle) — 「법령」 제N조 교차참조

All node/relationship labels are validated against
``_is_safe_cypher_label`` (alpha + underscore) so the shared Neo4j writer
is safe to reuse.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ...domain.models import RawDocument
from ..graphrag.extractor import GraphRAGExtractor
from ..graphrag.models import ExtractionResult, GraphNode, GraphRelationship

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches a Korean quoted law reference, optionally followed by article/clause
# markers. Examples:
#     「응급의료에 관한 법률」
#     「응급의료에 관한 법률」 제2조
#     「응급의료에 관한 법률」 제2조제1호
#     「응급의료에 관한 법률」 제52조제3항제1호
# The inner law title can contain any character except the closing 」.
_CROSS_REF_RE = re.compile(
    r"「([^」\n]{2,80})」"
    r"(?:\s*제\s*(\d+)\s*조(?:의\s*(\d+))?"
    r"(?:\s*제\s*(\d+)\s*항)?"
    r"(?:\s*제\s*(\d+)\s*호)?"
    r")?",
)

# Matches an article heading inside the current document body (after YAML
# frontmatter has been stripped by the git connector):
#     ##### 제1조 (목적)
#     ##### 제2조의2 (특례)
_ARTICLE_HEADER_RE = re.compile(
    r"^#{3,6}\s*제\s*(\d+)\s*조(?:의\s*(\d+))?\s*\(([^)]+)\)",
    re.MULTILINE,
)

# Amendment marker:  <개정 2016.1.27, 2020.10.20>
_AMENDMENT_RE = re.compile(r"<개정\s+([0-9.,\s]+)>")


@dataclass(frozen=True)
class _ArticleRef:
    """A parsed article reference: 제N조(의M)?제P항?제Q호?."""

    article: int
    sub_article: int | None = None  # 제N조의M
    clause: int | None = None       # 제P항
    item: int | None = None         # 제Q호

    def to_id(self, law_slug: str) -> str:
        base = f"{law_slug}:제{self.article}조"
        if self.sub_article is not None:
            base += f"의{self.sub_article}"
        if self.clause is not None:
            base += f"제{self.clause}항"
        if self.item is not None:
            base += f"제{self.item}호"
        return base

    def to_label(self) -> str:
        base = f"제{self.article}조"
        if self.sub_article is not None:
            base += f"의{self.sub_article}"
        if self.clause is not None:
            base += f"제{self.clause}항"
        if self.item is not None:
            base += f"제{self.item}호"
        return base


def _law_slug(name: str) -> str:
    """Normalize a Korean law title to a stable slug used as node id.

    Strips whitespace, drops trailing punctuation, and collapses internal
    whitespace. Intentionally preserves Korean characters — Neo4j ids can
    be any string, only labels must be ASCII.
    """
    cleaned = re.sub(r"\s+", "", name.strip())
    cleaned = cleaned.replace("「", "").replace("」", "")
    return cleaned


class LegalGraphExtractor(GraphRAGExtractor):
    """Rule-based extractor for Korean legal markdown.

    Inherits :class:`GraphRAGExtractor` purely so that ``save_to_neo4j``,
    ``_get_neo4j_driver`` and relationship history helpers can be reused
    unchanged. The LLM path (``_get_llm`` / prompts) is never exercised
    because we override the extraction step.
    """

    async def extract_from_document(
        self,
        raw: RawDocument,
        *,
        kb_id: str | None = None,
    ) -> ExtractionResult:
        """Extract a legal graph from a single document.

        Expects ``raw.metadata`` to contain the legal fields promoted by
        :func:`src.connectors.git.frontmatter.promote_legal_metadata`
        (``law_name``, ``law_id``, ``law_type``, ``ministries``,
        ``parent_law_slug``, ``law_file_kind``).
        """
        meta = raw.metadata or {}
        law_name = str(meta.get("law_name") or raw.title or "").strip()
        if not law_name:
            return ExtractionResult(
                source_document=raw.title,
                source_page_id=raw.doc_id,
                source_updated_at=raw.updated_at.isoformat() if raw.updated_at else None,
                kb_id=kb_id,
            )

        current_slug = _law_slug(law_name)
        nodes: dict[str, GraphNode] = {}
        relationships: list[GraphRelationship] = []

        # 1. Current law node
        law_properties = {
            "name": law_name,
            "law_id": meta.get("law_id"),
            "law_mst": meta.get("law_mst"),
            "law_type": meta.get("law_type"),
            "promulgation_date": meta.get("promulgation_date"),
            "enforcement_date": meta.get("enforcement_date"),
            "status": meta.get("law_status"),
            "source_url": meta.get("law_source_url"),
            "file_kind": meta.get("law_file_kind"),
        }
        nodes[current_slug] = GraphNode(
            id=current_slug, type="Law",
            properties={k: v for k, v in law_properties.items() if v is not None},
        )

        # 2. Ministry nodes + OVERSEES edges
        ministries = meta.get("ministries") or (
            [meta["ministry"]] if meta.get("ministry") else []
        )
        for ministry in ministries:
            ministry_id = str(ministry).strip()
            if not ministry_id:
                continue
            if ministry_id not in nodes:
                nodes[ministry_id] = GraphNode(
                    id=ministry_id, type="Ministry",
                    properties={"name": ministry_id},
                )
            relationships.append(GraphRelationship(
                source=ministry_id, target=current_slug, type="OVERSEES",
            ))

        # 3. Parent-law IMPLEMENTS edge (시행령 → 법률, etc.)
        parent_slug = str(meta.get("parent_law_slug") or "").strip()
        file_kind = str(meta.get("law_file_kind") or "").strip()
        if parent_slug and file_kind in ("decree", "rule") and parent_slug != current_slug:
            if parent_slug not in nodes:
                nodes[parent_slug] = GraphNode(
                    id=parent_slug, type="Law",
                    properties={"name": parent_slug, "placeholder": True},
                )
            relationships.append(GraphRelationship(
                source=current_slug, target=parent_slug, type="IMPLEMENTS",
                properties={"file_kind": file_kind},
            ))

        # 4. Articles of the current document + PART_OF edges
        article_id_by_number = self._extract_articles(
            raw.content, current_slug, nodes, relationships,
        )

        # 5. Cross-references from body text
        self._extract_cross_references(
            raw.content, current_slug, article_id_by_number, nodes, relationships,
        )

        return ExtractionResult(
            nodes=list(nodes.values()),
            relationships=relationships,
            source_document=raw.title,
            source_page_id=raw.doc_id,
            source_updated_at=raw.updated_at.isoformat() if raw.updated_at else None,
            kb_id=kb_id,
        )

    # ------------------------------------------------------------------
    # Regex helpers
    # ------------------------------------------------------------------

    def _extract_articles(
        self,
        text: str,
        law_slug: str,
        nodes: dict[str, GraphNode],
        relationships: list[GraphRelationship],
    ) -> dict[tuple[int, int | None], str]:
        """Extract ##### 제N조 headings as LegalArticle nodes.

        Returns a mapping ``(article_num, sub_article_num) -> node_id``
        so cross-references to articles of this law link to the actual
        article node (not a placeholder).
        """
        by_number: dict[tuple[int, int | None], str] = {}
        for match in _ARTICLE_HEADER_RE.finditer(text):
            article_num = int(match.group(1))
            sub_num_raw = match.group(2)
            sub_num = int(sub_num_raw) if sub_num_raw else None
            title = match.group(3).strip()

            ref = _ArticleRef(article=article_num, sub_article=sub_num)
            article_id = ref.to_id(law_slug)
            if article_id in nodes:
                continue

            nodes[article_id] = GraphNode(
                id=article_id, type="LegalArticle",
                properties={
                    "name": ref.to_label(),
                    "title": title,
                    "law_slug": law_slug,
                    "article_number": article_num,
                    **({"sub_article_number": sub_num} if sub_num is not None else {}),
                },
            )
            relationships.append(GraphRelationship(
                source=article_id, target=law_slug, type="PART_OF",
            ))
            by_number[(article_num, sub_num)] = article_id
        return by_number

    def _extract_cross_references(
        self,
        text: str,
        current_slug: str,
        article_id_by_number: dict[tuple[int, int | None], str],
        nodes: dict[str, GraphNode],
        relationships: list[GraphRelationship],
    ) -> None:
        """Extract 「법령」 제N조 cross-references and emit REFERENCES edges."""
        seen: set[tuple[str, str, str]] = set()

        for match in _CROSS_REF_RE.finditer(text):
            target_name = match.group(1).strip()
            target_slug = _law_slug(target_name)
            if not target_slug or target_slug == current_slug:
                # Self-references (「이 법」 style quotes) are handled
                # implicitly via PART_OF and add no value here.
                continue

            # Ensure target Law node exists (placeholder if unknown).
            if target_slug not in nodes:
                nodes[target_slug] = GraphNode(
                    id=target_slug, type="Law",
                    properties={"name": target_name, "placeholder": True},
                )

            article_num_raw = match.group(2)
            if not article_num_raw:
                edge = (current_slug, target_slug, "REFERENCES")
                if edge in seen:
                    continue
                seen.add(edge)
                relationships.append(GraphRelationship(
                    source=current_slug, target=target_slug, type="REFERENCES",
                    properties={"scope": "law"},
                ))
                continue

            article_num = int(article_num_raw)
            sub_num = int(match.group(3)) if match.group(3) else None
            clause = int(match.group(4)) if match.group(4) else None
            item = int(match.group(5)) if match.group(5) else None

            target_ref = _ArticleRef(
                article=article_num, sub_article=sub_num,
                clause=clause, item=item,
            )
            target_article_id = target_ref.to_id(target_slug)

            if target_article_id not in nodes:
                nodes[target_article_id] = GraphNode(
                    id=target_article_id, type="LegalArticle",
                    properties={
                        "name": target_ref.to_label(),
                        "law_slug": target_slug,
                        "article_number": article_num,
                        "placeholder": True,
                        **(
                            {"sub_article_number": sub_num}
                            if sub_num is not None else {}
                        ),
                    },
                )
                relationships.append(GraphRelationship(
                    source=target_article_id, target=target_slug, type="PART_OF",
                ))

            edge = (current_slug, target_article_id, "REFERENCES")
            if edge in seen:
                continue
            seen.add(edge)

            props: dict[str, Any] = {"scope": "article"}
            if clause is not None:
                props["clause"] = clause
            if item is not None:
                props["item"] = item

            relationships.append(GraphRelationship(
                source=current_slug, target=target_article_id,
                type="REFERENCES", properties=props,
            ))
