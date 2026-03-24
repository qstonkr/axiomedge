"""Graph Label Utilities

Label sanitization, Korean translation, and formatting for knowledge graph UX v2.

Created: 2026-03-14
"""

import re

# ── Label Sanitization ──

_LEADING_DIGITS_RE = re.compile(r"^\d{5,}")
_FILE_EXT_RE = re.compile(r"\.(pptx|docx|pdf|xlsx|txt|hwp|hwpx)$", re.IGNORECASE)
_SLIDE_RE = re.compile(r"\s*[-–—]\s*[Ss]lide\s*\d+\s*$")


def sanitize_label(label: str, node_type: str = "") -> tuple[str, str]:
    """Metadata cleanup. Returns (clean_label, tooltip_extra).

    - Strip leading numeric IDs: "17729858auto-order..." -> "auto-order..."
    - Remove file extensions: ".pptx", ".docx", ".pdf", etc.
    - Separate slide info to tooltip: "... - Slide 3" -> tooltip
    """
    if not label:
        return ("", "")

    tooltip_extra = ""
    clean = label.strip()

    # Extract slide info before removing
    slide_match = _SLIDE_RE.search(clean)
    if slide_match:
        tooltip_extra = slide_match.group(0).strip().lstrip("-–—").strip()
        clean = _SLIDE_RE.sub("", clean).strip()

    # Remove leading numeric ID (5+ digits)
    clean = _LEADING_DIGITS_RE.sub("", clean).strip()

    # Remove file extensions
    clean = _FILE_EXT_RE.sub("", clean).strip()

    # Fallback to original if cleaning produced empty string
    if not clean:
        clean = label.strip()

    return (clean, tooltip_extra)


def truncate_label(label: str, max_len: int = 18) -> str:
    """Truncate to max_len chars with '...' suffix.

    Industry reference: Neo4j 12 chars, yFiles 15 chars.
    Korean characters are wider, so 18 is a reasonable default.
    """
    if len(label) <= max_len:
        return label
    return label[:max_len] + "..."


def format_node_label(label: str, node_type: str) -> str:
    """Add type prefix: '[Document] K8s Guide', '[Step] Order Entry'."""
    type_ko = NODE_TYPE_LABELS_KO.get(node_type, "")
    if type_ko:
        return f"[{type_ko}] {label}"
    return label


# ── Korean Translations ──

RELATION_LABELS_KO: dict[str, str] = {
    # Active (20)
    "MEMBER_OF": "소속",
    "MANAGES": "관리",
    "OWNS": "소유",
    "RESPONSIBLE_FOR": "담당",
    "PARTICIPATES_IN": "참여",
    "DEFINES": "정의",
    "IMPLEMENTS": "구현",
    "PART_OF": "포함",
    "RELATED_TO": "관련",
    "EXTRACTED_FROM": "출처",
    "BELONGS_TO": "소속",
    "MODIFIED_BY": "수정",
    "MENTIONS": "언급",
    "CREATED_BY": "작성",
    "HAS_ATTACHMENT": "첨부",
    "COVERS": "포함",
    "NEXT_STEP": "다음단계",
    "FLOWS_TO": "흐름",
    "CONNECTS_TO": "연결",
    "SAME_CONCEPT": "동의어",
    # History (8)
    "WAS_MEMBER_OF": "(전)소속",
    "PREVIOUSLY_MANAGED": "(전)관리",
    "PREVIOUSLY_OWNED": "(전)소유",
    "WAS_RESPONSIBLE_FOR": "(전)담당",
    "PREVIOUSLY_PARTICIPATED_IN": "(전)참여",
    "PREVIOUSLY_DEFINED": "(전)정의",
    "PREVIOUSLY_IMPLEMENTED": "(전)구현",
    "WAS_PART_OF": "(전)포함",
}

NODE_TYPE_LABELS_KO: dict[str, str] = {
    "Person": "사람",
    "Team": "팀",
    "System": "시스템",
    "Document": "문서",
    "Policy": "정책",
    "Logic": "로직",
    "Process": "프로세스",
    "Term": "용어",
    "Project": "프로젝트",
    "Role": "역할",
    "Attachment": "첨부",
    "Topic": "주제",
    "KnowledgeBase": "KB",
    "ProcessStep": "단계",
    "Entity": "엔티티",
}


def format_rel_label(rel_type: str) -> str:
    """Edge display label: MEMBER_OF -> '소속'."""
    return RELATION_LABELS_KO.get(rel_type, rel_type)


def format_rel_for_filter(rel_type: str) -> str:
    """Filter display: MEMBER_OF -> '소속 (MEMBER_OF)'."""
    ko = RELATION_LABELS_KO.get(rel_type, "")
    if ko:
        return f"{ko} ({rel_type})"
    return rel_type
