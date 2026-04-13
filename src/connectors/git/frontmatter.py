"""YAML frontmatter parser for markdown documents.

Extracts the leading ``---\\n...\\n---`` block of a markdown file and returns
a ``(metadata_dict, body_text)`` pair. For Korean legal markdown from
legalize-kr, the frontmatter carries authoritative structured metadata
(공포일자, 소관부처, 법령ID 등) that should be promoted to the ingestion
payload instead of being embedded into the vector as raw YAML.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*\n?", re.DOTALL,
)

# Korean legal frontmatter → RawDocument.metadata key mapping.
# Keys on the right follow existing search/filter conventions:
#   - doc_date: consumed by the date filter in search pipeline
#   - knowledge_type: displayed as document category
#   - law_* : legal-specific fields used by the legal graph extractor
_LEGAL_KEY_MAP: dict[str, str] = {
    "제목": "law_name",
    "법령MST": "law_mst",
    "법령ID": "law_id",
    "법령구분": "law_type",
    "법령구분코드": "law_type_code",
    "소관부처": "ministry",
    "공포일자": "promulgation_date",
    "공포번호": "promulgation_number",
    "시행일자": "enforcement_date",
    "법령분야": "law_domain",
    "상태": "law_status",
    "출처": "law_source_url",
}


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Strip YAML frontmatter from markdown text.

    Returns:
        (metadata, body) — metadata is empty dict if no frontmatter present
        or if parsing fails; body is the text with the frontmatter block
        removed (leading whitespace/newlines trimmed).
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    yaml_block = match.group(1)
    try:
        data = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse frontmatter YAML: %s", exc)
        return {}, text[match.end():]

    if not isinstance(data, dict):
        return {}, text[match.end():]

    body = text[match.end():].lstrip("\n")
    return data, body


def promote_legal_metadata(frontmatter: dict[str, Any]) -> dict[str, Any]:
    """Convert Korean legal frontmatter keys to canonical metadata fields.

    - Renames Korean keys to English (법령ID → law_id).
    - Flattens single-item ``소관부처`` lists to a string while keeping the
      full list in ``ministries``.
    - Maps 공포일자 to the well-known ``doc_date`` field so the existing
      date filter in the search pipeline picks it up automatically.
    - Adds ``knowledge_type`` = law_type for display purposes.
    - Sets ``_is_legal_document`` = True so the pipeline can auto-select
      the legal chunker and graph extractor.
    """
    if not frontmatter:
        return {}

    promoted: dict[str, Any] = {}
    for k_ko, v in frontmatter.items():
        key = _LEGAL_KEY_MAP.get(k_ko)
        if key is None or v is None:
            continue
        promoted[key] = _coerce_value(v)

    ministry = promoted.get("ministry")
    if isinstance(ministry, list):
        cleaned = [str(x).strip() for x in ministry if str(x).strip()]
        promoted["ministries"] = cleaned
        promoted["ministry"] = cleaned[0] if cleaned else ""
    elif isinstance(ministry, str):
        promoted["ministries"] = [ministry.strip()] if ministry.strip() else []

    promulgation_date = promoted.get("promulgation_date")
    if promulgation_date:
        promoted["doc_date"] = str(promulgation_date)

    law_type = promoted.get("law_type")
    if law_type:
        promoted["knowledge_type"] = f"법령:{law_type}"

    if any(k in promoted for k in ("law_id", "law_mst", "law_type")):
        promoted["_is_legal_document"] = True

    return promoted


def _coerce_value(value: Any) -> Any:
    """Coerce YAML-parsed values into JSON-friendly primitives.

    PyYAML resolves ``2024-12-03`` to ``datetime.date``; Qdrant/JSON payloads
    cannot serialize those, so dates/datetimes become ISO strings. Lists and
    dicts are recursed so nested dates are also flattened.
    """
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, list):
        return [_coerce_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _coerce_value(v) for k, v in value.items()}
    return value
