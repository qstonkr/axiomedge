"""Tests for LegalGraphExtractor — rule-based graph construction for 법령.

These tests never touch Neo4j: they instantiate the extractor, call
``extract_from_document`` and inspect the returned ExtractionResult.
``save_to_neo4j`` is exercised separately via monkeypatched driver.
"""

from __future__ import annotations

import pytest

from src.domain.models import RawDocument
from src.pipeline.legal_graph import LegalGraphExtractor
from src.pipeline.legal_graph.extractor import _CROSS_REF_RE, _law_slug


# ---------------------------------------------------------------------------
# Regex sanity
# ---------------------------------------------------------------------------


class TestCrossRefRegex:
    def test_law_only(self):
        matches = list(_CROSS_REF_RE.finditer("「응급의료에 관한 법률」 에 따라"))
        assert len(matches) == 1
        m = matches[0]
        assert m.group(1) == "응급의료에 관한 법률"
        assert m.group(2) is None

    def test_law_article(self):
        m = _CROSS_REF_RE.search("「응급의료에 관한 법률」 제2조")
        assert m is not None
        assert m.group(1) == "응급의료에 관한 법률"
        assert m.group(2) == "2"

    def test_law_article_clause_item(self):
        m = _CROSS_REF_RE.search("「응급의료에 관한 법률」 제2조제1항제3호")
        assert m is not None
        assert m.group(2) == "2"
        assert m.group(4) == "1"
        assert m.group(5) == "3"

    def test_sub_article(self):
        m = _CROSS_REF_RE.search("「소방기본법」 제4조의2")
        assert m is not None
        assert m.group(2) == "4"
        assert m.group(3) == "2"

    def test_multiple_refs(self):
        text = "「A법」 제1조 및 「B법」 제2조제1호"
        matches = list(_CROSS_REF_RE.finditer(text))
        assert len(matches) == 2
        assert matches[0].group(1) == "A법"
        assert matches[1].group(1) == "B법"


class TestLawSlug:
    def test_drops_quotes_and_whitespace(self):
        assert _law_slug("응급의료에 관한 법률") == "응급의료에관한법률"
        assert _law_slug("「응급의료에 관한 법률」") == "응급의료에관한법률"


# ---------------------------------------------------------------------------
# Extraction end-to-end
# ---------------------------------------------------------------------------


def _legal_raw(
    *,
    content: str,
    law_name: str = "119구조ㆍ구급에 관한 법률",
    law_id: str = "011349",
    law_mst: int = 266637,
    ministries: list[str] | None = None,
    parent_law_slug: str = "",
    file_kind: str = "law",
) -> RawDocument:
    return RawDocument(
        doc_id=f"git:legalize-kr:kr/{law_name}/{file_kind}.md",
        title=law_name,
        content=content,
        source_uri="https://github.com/legalize-kr/legalize-kr/blob/main/...",
        author="",
        metadata={
            "_is_legal_document": True,
            "law_name": law_name,
            "law_id": law_id,
            "law_mst": law_mst,
            "law_type": "법률" if file_kind == "law" else "시행령",
            "ministries": ministries or ["소방청"],
            "ministry": (ministries or ["소방청"])[0],
            "promulgation_date": "2024-12-03",
            "enforcement_date": "2025-06-04",
            "law_status": "시행",
            "law_file_kind": file_kind,
            "parent_law_slug": parent_law_slug,
        },
    )


@pytest.mark.asyncio
async def test_extract_articles_and_ministry():
    content = """# 119구조ㆍ구급에 관한 법률

## 제1장 총칙

##### 제1조 (목적)

이 법은 화재, 재난ㆍ재해 및 테러에서 구조ㆍ구급을 규정한다.

##### 제2조 (정의)

이 법에서 사용하는 용어의 뜻은 다음과 같다.
"""
    raw = _legal_raw(content=content)
    extractor = LegalGraphExtractor()

    result = await extractor.extract_from_document(raw, kb_id="legalize-kr")

    node_types = {n.type for n in result.nodes}
    assert "Law" in node_types
    assert "LegalArticle" in node_types
    assert "Ministry" in node_types

    # Law node carries frontmatter metadata
    law_nodes = [n for n in result.nodes if n.type == "Law"]
    assert len(law_nodes) == 1
    law_node = law_nodes[0]
    assert law_node.properties["name"] == "119구조ㆍ구급에 관한 법률"
    assert law_node.properties["law_id"] == "011349"
    assert law_node.properties["promulgation_date"] == "2024-12-03"

    # Two article nodes, each with PART_OF → law
    article_nodes = [n for n in result.nodes if n.type == "LegalArticle"]
    assert len(article_nodes) == 2
    article_names = sorted(n.properties["name"] for n in article_nodes)
    assert article_names == ["제1조", "제2조"]

    part_of = [r for r in result.relationships if r.type == "PART_OF"]
    assert len(part_of) == 2

    # Ministry edge
    oversees = [r for r in result.relationships if r.type == "OVERSEES"]
    assert len(oversees) == 1
    assert oversees[0].source == "소방청"


@pytest.mark.asyncio
async def test_extract_cross_references():
    content = """# 119구조ㆍ구급에 관한 법률

## 제1장 총칙

##### 제2조 (정의)

"응급환자"란 「응급의료에 관한 법률」 제2조제1호의 응급환자를 말한다.
"응급처치"란 「응급의료에 관한 법률」 제2조제3호의 응급처치를 말한다.
"지도의사"란 「응급의료에 관한 법률」 제52조의 지도의사를 말한다.
"""
    raw = _legal_raw(content=content)
    extractor = LegalGraphExtractor()
    result = await extractor.extract_from_document(raw, kb_id="legalize-kr")

    # External law node created as placeholder
    external_laws = [
        n for n in result.nodes
        if n.type == "Law" and n.properties.get("placeholder")
    ]
    assert any(n.id == "응급의료에관한법률" for n in external_laws)

    # External article nodes created
    external_articles = [
        n for n in result.nodes
        if n.type == "LegalArticle" and n.properties.get("placeholder")
    ]
    assert len(external_articles) >= 2  # 제2조, 제52조

    # REFERENCES edges from current law (not from current article)
    references = [r for r in result.relationships if r.type == "REFERENCES"]
    assert len(references) >= 2
    # All cross-refs originate from the current law slug
    assert all(r.source == "119구조ㆍ구급에관한법률" for r in references)

    # No duplicate edges
    edge_set = {(r.source, r.target, r.type) for r in references}
    assert len(edge_set) == len(references)

    # Item-scoped references (제N호) capture the item number
    article_refs = [r for r in references if r.properties.get("scope") == "article"]
    item_refs = [r for r in article_refs if "item" in r.properties]
    assert item_refs  # 제2조제1호 / 제2조제3호


@pytest.mark.asyncio
async def test_extract_implements_edge_for_decree():
    content = """# 119구조ㆍ구급에 관한 법률 시행령

## 제1장 총칙

##### 제1조 (목적)

이 영은 「119구조ㆍ구급에 관한 법률」에서 위임된 사항을 규정함을 목적으로 한다.
"""
    raw = _legal_raw(
        content=content,
        law_name="119구조ㆍ구급에 관한 법률 시행령",
        file_kind="decree",
        parent_law_slug="119구조ㆍ구급에관한법률",
    )
    extractor = LegalGraphExtractor()
    result = await extractor.extract_from_document(raw, kb_id="legalize-kr")

    implements = [r for r in result.relationships if r.type == "IMPLEMENTS"]
    assert len(implements) == 1
    impl = implements[0]
    assert impl.source == "119구조ㆍ구급에관한법률시행령"
    assert impl.target == "119구조ㆍ구급에관한법률"
    assert impl.properties["file_kind"] == "decree"


@pytest.mark.asyncio
async def test_self_references_ignored():
    content = """# 119구조ㆍ구급에 관한 법률

##### 제1조 (목적)

이 법의 목적은 「119구조ㆍ구급에 관한 법률」에 따라 구조ㆍ구급을 규정하는 것이다.
"""
    raw = _legal_raw(content=content)
    extractor = LegalGraphExtractor()
    result = await extractor.extract_from_document(raw, kb_id="legalize-kr")

    # The 「이 법률」 self-reference should NOT create a REFERENCES edge.
    references = [r for r in result.relationships if r.type == "REFERENCES"]
    assert len(references) == 0


@pytest.mark.asyncio
async def test_empty_law_name_returns_empty_result():
    raw = RawDocument(
        doc_id="git:foo:bar.md",
        title="",
        content="## 제1장",
        source_uri="",
        metadata={"_is_legal_document": True},
    )
    extractor = LegalGraphExtractor()
    result = await extractor.extract_from_document(raw, kb_id="test")
    assert result.node_count == 0
    assert result.relationship_count == 0
