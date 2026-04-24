"""LLM prompt + strict parser for schema discovery (Phase 3 bootstrap).

Spec §6.6.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


SCHEMA_DISCOVERY_PROMPT = """다음은 KB "{kb_id}" 의 샘플 문서 {n}개입니다.
이 도메인의 지식 그래프에 적합한 신규 entity/relationship 타입을 제안하세요.

### 이미 확정된 타입 (중복 제안 금지, 영문 label)
- Entity: {existing_nodes}
- Relationship: {existing_rels}

### 판단 기준
- 문서 2개 이상에 등장하는 개념만 제안
- 기존 타입으로 충분히 커버되면 신규 제안 금지
- Confidence 0.0~1.0:
    0.95 = 여러 문서에 일관되게 등장
    0.85 = 등장은 하나 약간 모호
    0.70 = 1~2 문서만 언급

### 샘플 문서
{docs}

### 출력 (JSON 만, 다른 텍스트 금지)
{{"new_node_types":[
  {{"label":"<CamelCase>","reason":"<한 문장>","confidence":0.92,
    "examples":["<원문 구절>"]}}
],"new_relation_types":[
  {{"label":"<SCREAMING_SNAKE>","source":"<Entity>","target":"<Entity>",
    "reason":"<한 문장>","confidence":0.9,"examples":["<원문 구절>"]}}
]}}
"""


# Node label: Cypher identifier starting with uppercase
_LABEL_NODE_RE = re.compile(r"^[A-Z][a-zA-Z0-9_]{0,63}$")
# Relationship label: SCREAMING_SNAKE (accepts digits/underscore)
_LABEL_REL_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


@dataclass(frozen=True)
class NodeCandidate:
    label: str
    confidence: float
    examples: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


@dataclass(frozen=True)
class RelationCandidate:
    label: str
    source: str
    target: str
    confidence: float
    examples: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


@dataclass(frozen=True)
class DiscoveryResponse:
    node_candidates: list[NodeCandidate]
    relation_candidates: list[RelationCandidate]


def parse_discovery_response(raw: str) -> DiscoveryResponse:
    """Strict parse. Strips code fences; silently drops malformed individual
    entries; raises ``ValueError`` on top-level JSON decode failure.
    """
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        if m:
            raw = m.group(1).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Discovery response not JSON: {exc}") from exc

    node_cands: list[NodeCandidate] = []
    for item in data.get("new_node_types") or []:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not isinstance(label, str) or not _LABEL_NODE_RE.match(label):
            continue
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        examples = tuple(str(e) for e in (item.get("examples") or []))
        node_cands.append(NodeCandidate(
            label=label,
            confidence=conf,
            examples=examples,
            reason=str(item.get("reason", "")),
        ))

    rel_cands: list[RelationCandidate] = []
    for item in data.get("new_relation_types") or []:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not isinstance(label, str) or not _LABEL_REL_RE.match(label):
            continue
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        examples = tuple(str(e) for e in (item.get("examples") or []))
        rel_cands.append(RelationCandidate(
            label=label,
            source=str(item.get("source", "")),
            target=str(item.get("target", "")),
            confidence=conf,
            examples=examples,
            reason=str(item.get("reason", "")),
        ))

    return DiscoveryResponse(
        node_candidates=node_cands,
        relation_candidates=rel_cands,
    )


__all__ = [
    "DiscoveryResponse",
    "NodeCandidate",
    "RelationCandidate",
    "SCHEMA_DISCOVERY_PROMPT",
    "parse_discovery_response",
]
