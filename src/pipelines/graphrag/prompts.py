# pyright: reportGeneralTypeIssues=false, reportAssignmentType=false
"""GraphRAG Prompt Templates and Schema Definitions."""

from __future__ import annotations

import re
from typing import Any


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
KB_SCHEMA_PROFILES: dict[str, dict[str, list[str]]] = {
    "a-ari": {
        "nodes": ["Store", "Process", "Product", "Person", "Policy", "Term", "Location"],
        "relationships": ["OPERATES", "FOLLOWS", "SELLS", "MANAGES", "APPLIES_TO", "LOCATED_IN", "PART_OF"],
        "prompt_focus": "점포, 절차/프로세스, 상품, 정책/규정, 용어",
    },
    "g-espa": {
        "nodes": ["Store", "Person", "Process", "Event", "Product", "Location", "Team", "Term"],
        "relationships": ["MANAGES", "OPERATES", "PARTICIPATES_IN", "LOCATED_IN", "RESPONSIBLE_FOR", "RELATED_TO", "SELLS", "PART_OF"],
        "prompt_focus": "점포(GS25/CU), 경영주/OFC(사람), ESPA활동/개선활동, 상품카테고리, 지역/상권, 매출성과, 경쟁점",
    },
    "drp": {
        "nodes": ["Store", "Person", "Policy", "Event", "Location", "Team"],
        "relationships": ["MANAGES", "APPLIES_TO", "PARTICIPATES_IN", "LOCATED_IN", "RESPONSIBLE_FOR", "RELATED_TO"],
        "prompt_focus": "점포, 당사자(사람), 정책/규정, 분쟁사건, 지역",
    },
    "hax": {
        "nodes": ["System", "Team", "Person", "Process", "Project", "Term", "Document"],
        "relationships": ["MANAGES", "MEMBER_OF", "IMPLEMENTS", "OWNS", "RESPONSIBLE_FOR", "DEFINES", "PART_OF"],
        "prompt_focus": "시스템, 팀/부서, 담당자, 프로세스, 프로젝트, 용어",
    },
    "itops_general": {
        "nodes": ["System", "Team", "Person", "Process", "Project", "Term", "Document", "Policy", "Logic"],
        "relationships": ["MANAGES", "MEMBER_OF", "IMPLEMENTS", "OWNS", "RESPONSIBLE_FOR", "DEFINES", "PART_OF", "FOLLOWS", "APPLIES_TO"],
        "prompt_focus": "시스템, 팀/부서, 담당자, 프로세스, 프로젝트, 용어, 정책/규정, 비즈니스로직, 업무절차",
    },
    "partnertalk": {
        "nodes": ["Person", "Product", "Store", "Process", "Term", "Event"],
        "relationships": ["SELLS", "MANAGES", "APPLIES_TO", "RELATED_TO", "FOLLOWS"],
        "prompt_focus": "협력사(사람/회사), 상품, 점포, 문의절차, 용어",
    },
}

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
    """Get schema profile for a KB.

    Normalizes hyphens/underscores so that e.g. ``a_ari`` and ``a-ari``
    both resolve to the same profile.
    """
    # Direct lookup first (fast path)
    if kb_id in KB_SCHEMA_PROFILES:
        return KB_SCHEMA_PROFILES[kb_id]
    # Normalize: replace underscores with hyphens and try again
    normalized = kb_id.replace("_", "-")
    if normalized in KB_SCHEMA_PROFILES:
        return KB_SCHEMA_PROFILES[normalized]
    # Reverse: replace hyphens with underscores
    normalized = kb_id.replace("-", "_")
    if normalized in KB_SCHEMA_PROFILES:
        return KB_SCHEMA_PROFILES[normalized]
    return DEFAULT_SCHEMA_PROFILE


def build_extraction_prompt(_document: str, kb_id: str | None = None) -> str:
    """Build KB-specific extraction prompt."""
    schema = get_kb_schema(kb_id) if kb_id else DEFAULT_SCHEMA_PROFILE
    focus = schema.get("prompt_focus", "사람, 팀, 시스템")
    nodes = schema.get("nodes", ALLOWED_NODES)

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

    # Use string concatenation to keep .format() compatibility
    return (
        "다음 문서에서 엔티티와 관계를 추출하세요.\n"
        "문서에 명시된 정보만 추출하고, 추측하지 마세요.\n\n"
        f"추출 대상: {focus}\n\n"
        "문서: {document}\n\n"
        "아래 JSON 형식으로만 출력하세요:\n"
        f'{{{{"nodes":[{nodes_example}],"relationships":[{{{{"source":"엔티티A","type":"RELATED_TO","target":"엔티티B"}}}}]}}}}\n\n'
        "JSON:"
    )
