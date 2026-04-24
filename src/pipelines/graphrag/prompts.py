# pyright: reportGeneralTypeIssues=false, reportAssignmentType=false
"""GraphRAG Prompt Templates and Schema Definitions."""

from __future__ import annotations

import re
from typing import Any

from .schema_resolver import SchemaResolver
from .schema_types import SchemaProfile


# =============================================================================
# Cypher Safety (inlined from cypher_safety.py)
# =============================================================================
_SAFE_CYPHER_LABEL = re.compile(r"^[A-Za-z_]\w*$")


def _is_safe_cypher_label(value: str) -> bool:
    """Return True if value is a safe Cypher label/relationship identifier."""
    return bool(_SAFE_CYPHER_LABEL.match(value))


# =============================================================================
# Schema Definition
# =============================================================================
ALLOWED_NODES = [
    "Person",      # 사람
    "Team",        # 팀/부서
    "System",      # 시스템/서비스
    "Document",    # 문서
    "Policy",      # 정책
    "Logic",       # 비즈니스 로직
    "Process",     # 프로세스/절차
    "Term",        # 용어
    "Project",     # 프로젝트
    "Role",        # 역할
    "Store",       # 점포/매장
    "Location",    # 지역/위치
    "Product",     # 상품/서비스
    "Event",       # 사건/활동
]

# KB-specific schema profiles
# ---------------------------------------------------------------------------
# Legacy facade — routes old dict-style access through SchemaResolver (YAML)
# ---------------------------------------------------------------------------


class _LegacyKBSchemaProfilesProxy:
    """Dict-compatible view over YAML-backed KB schemas.

    Exists so callers doing ``KB_SCHEMA_PROFILES["g-espa"]`` keep working
    after the underlying storage moved from hardcoded Python dict to YAML
    (see ``deploy/config/graph_schemas/<kb_id>.yaml``). Builds lazily on
    first access; callers that need a refresh can set ``_cache = None``.
    Tests do exactly that.
    """

    _cache: dict[str, dict[str, Any]] | None = None

    def _build(self) -> dict[str, dict[str, Any]]:
        from .schema_resolver import _SCHEMA_DIR  # path override-aware

        out: dict[str, dict[str, Any]] = {}
        if not _SCHEMA_DIR.exists():
            return out
        for path in sorted(_SCHEMA_DIR.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            kb_id = path.stem
            schema = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
            out[kb_id] = {
                "nodes": list(schema.nodes),
                "relationships": list(schema.relationships),
                "prompt_focus": schema.prompt_focus,
            }
        return out

    def _ensure(self) -> dict[str, dict[str, Any]]:
        if self._cache is None:
            self._cache = self._build()
        return self._cache

    def __getitem__(self, key: str) -> dict[str, Any]:
        return self._ensure()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._ensure()

    def get(self, key: str, default: Any = None) -> Any:
        return self._ensure().get(key, default)

    def __iter__(self):
        return iter(self._ensure())

    def keys(self):
        return self._ensure().keys()

    def items(self):
        return self._ensure().items()

    def values(self):
        return self._ensure().values()


KB_SCHEMA_PROFILES = _LegacyKBSchemaProfilesProxy()

ALLOWED_RELATIONSHIPS = [
    "MEMBER_OF",        # 소속
    "MANAGES",          # 관리
    "OWNS",             # 소유
    "RESPONSIBLE_FOR",  # 책임
    "PARTICIPATES_IN",  # 참여
    "DEFINES",          # 정의
    "IMPLEMENTS",       # 구현
    "PART_OF",          # 포함
    "RELATED_TO",       # 관련
    "EXTRACTED_FROM",   # 추출 출처
    "LOCATED_IN",       # 위치
    "OPERATES",         # 운영
    "FOLLOWS",          # 절차 순서
    "APPLIES_TO",       # 적용 대상
    "SELLS",            # 판매
]

# 이력 관계 매핑 (현재 -> 과거)
HISTORY_RELATIONSHIP_MAP = {
    "MEMBER_OF": "WAS_MEMBER_OF",
    "MANAGES": "PREVIOUSLY_MANAGED",
    "OWNS": "PREVIOUSLY_OWNED",
    "RESPONSIBLE_FOR": "WAS_RESPONSIBLE_FOR",
    "PARTICIPATES_IN": "PREVIOUSLY_PARTICIPATED_IN",
    "DEFINES": "PREVIOUSLY_DEFINED",
    "IMPLEMENTS": "PREVIOUSLY_IMPLEMENTED",
    "PART_OF": "WAS_PART_OF",
}


# =============================================================================
# Korean Optimized Prompt (Simple & Effective)
# =============================================================================
KOREAN_EXTRACTION_PROMPT = """다음 문서에서 엔티티와 관계를 추출하세요.
문서에 명시된 정보만 추출하고, 추측하지 마세요.

추출 대상:
- Person(사람), Team(팀/부서), System(시스템)
- Store(점포/매장), Location(지역), Process(절차/프로세스)
- Product(상품), Event(활동/사건), Policy(정책/규정)

문서: {document}

아래 JSON 형식으로만 출력하세요:
{{"nodes":[{{"id":"이름","type":"Person"}},{{"id":"팀명","type":"Team"}},{{"id":"시스템명","type":"System"}},{{"id":"점포명","type":"Store"}},{{"id":"절차명","type":"Process"}}],"relationships":[{{"source":"사람","type":"MEMBER_OF","target":"팀"}},{{"source":"점포","type":"PART_OF","target":"지역"}},{{"source":"사람","type":"MANAGES","target":"점포"}}]}}

JSON:"""

# Default schema for unknown KBs
DEFAULT_SCHEMA_PROFILE = {
    "nodes": ALLOWED_NODES,
    "relationships": ALLOWED_RELATIONSHIPS,
    "prompt_focus": "사람, 팀, 시스템, 점포, 절차, 지역, 상품, 정책",
}


def get_kb_schema(kb_id: str) -> dict[str, Any]:
    """Legacy API — routes through SchemaResolver (YAML-backed).

    Normalizes hyphens/underscores so that e.g. ``a_ari`` and ``a-ari``
    both resolve. Returns ``DEFAULT_SCHEMA_PROFILE``-shaped dict for
    backward compatibility; new code should prefer
    ``SchemaResolver.resolve()`` directly.
    """
    schema = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
    # Generic fallback means no matching YAML; normalize hyphens/underscores
    # as the old implementation did so callers using "a_ari" still find "a-ari".
    if "generic" in schema.source_layers or "D:_generic" in schema.source_layers:
        for variant in (kb_id.replace("_", "-"), kb_id.replace("-", "_")):
            if variant == kb_id:
                continue
            alt = SchemaResolver.resolve(kb_id=variant, source_type=None)
            if "generic" not in alt.source_layers and "D:_generic" not in alt.source_layers:
                schema = alt
                break
    if "generic" in schema.source_layers or "D:_generic" in schema.source_layers:
        return DEFAULT_SCHEMA_PROFILE
    return {
        "nodes": list(schema.nodes),
        "relationships": list(schema.relationships),
        "prompt_focus": schema.prompt_focus,
    }


def build_extraction_prompt(
    _document: str,
    kb_id: str | SchemaProfile | None = None,
) -> str:
    """Build KB-specific extraction prompt (template with ``{document}``).

    The returned string preserves the ``{document}`` placeholder so the LLM
    client can ``.format(document=...)`` at call time (existing contract).
    ``_document`` is kept in the signature for backward compat — it is not
    used because the template is pre-format.

    ``kb_id`` may also be a pre-resolved ``SchemaProfile`` (new code path)
    or ``None`` (generic fallback).
    """
    if isinstance(kb_id, SchemaProfile):
        profile = kb_id
    elif isinstance(kb_id, str):
        profile = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
    else:
        profile = SchemaResolver.resolve(kb_id=None, source_type=None)
    focus = profile.prompt_focus or "사람, 팀, 시스템"
    nodes = list(profile.nodes) or ALLOWED_NODES
    rels = list(profile.relationships) or ALLOWED_RELATIONSHIPS

    # Build example nodes for prompt
    # NOTE: Result must be .format(document=...) compatible.
    # Use doubled braces {{}} for literal braces in the output.
    examples = []
    for n in nodes[:5]:
        label_map = {
            "Person": ("이름", "Person"),
            "Team": ("팀명", "Team"),
            "System": ("시스템명", "System"),
            "Store": ("점포명", "Store"),
            "Process": ("절차명", "Process"),
            "Location": ("지역명", "Location"),
            "Product": ("상품명", "Product"),
            "Event": ("활동명", "Event"),
            "Policy": ("정책명", "Policy"),
            "Term": ("용어명", "Term"),
            "Project": ("프로젝트명", "Project"),
        }
        id_label, type_label = label_map.get(n, ("이름", n))
        examples.append(f'{{{{"id":"{id_label}","type":"{type_label}"}}}}')

    nodes_example = ",".join(examples)
    nodes_list = ", ".join(nodes) if nodes else "(none)"
    rels_list = ", ".join(rels) if rels else "(none)"
    # Pick a representative rel for the JSON example (RELATED_TO if allowed,
    # otherwise the first allowed rel — keeps the prompt concrete)
    rel_example = "RELATED_TO" if "RELATED_TO" in rels else (rels[0] if rels else "RELATED_TO")

    # Use string concatenation to keep .format() compatibility
    return (
        "다음 문서에서 엔티티와 관계를 추출하세요.\n"
        "문서에 명시된 정보만 추출하고, 추측하지 마세요.\n\n"
        f"추출 대상: {focus}\n"
        f"허용된 Entity 타입 (목록 외 사용 금지): {nodes_list}\n"
        f"허용된 Relationship 타입 (목록 외 사용 금지): {rels_list}\n\n"
        "문서: {document}\n\n"
        "아래 JSON 형식으로만 출력하세요:\n"
        f'{{{{"nodes":[{nodes_example}],"relationships":[{{{{"source":"엔티티A","type":"{rel_example}","target":"엔티티B"}}}}]}}}}\n\n'
        "JSON:"
    )
