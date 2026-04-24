"""Tree Context Expander — 트리 기반 형제 청크 확장 + 섹션 제목 검색.

검색 파이프라인에서 리랭킹 후 호출하여:
1. 히트된 청크의 같은 섹션 형제 청크를 Neo4j 트리에서 조회
2. 섹션 제목 fulltext 검색으로 벡터 미스 보완
3. 확장 청크에 score decay 적용하여 원본보다 항상 아래 유지

TREE_INDEX_ENABLED=false 이면 모든 함수가 빈 결과 반환.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from src.stores.neo4j.errors import NEO4J_FAILURE

logger = logging.getLogger(__name__)


class TreeGraphRepo(Protocol):
    """tree_context_expander가 필요로 하는 그래프 레포 인터페이스."""

    async def find_tree_siblings_batch(
        self, chunk_ids: list[str], *, window: int = 2,
    ) -> dict[str, list[dict[str, Any]]]: ...

    async def search_section_titles(
        self, query: str, *, kb_id: str | None = None, limit: int = 10,
    ) -> list[dict[str, Any]]: ...

    async def get_chunk_section_paths_batch(
        self, chunk_ids: list[str],
    ) -> dict[str, str]: ...


@dataclass
class ExpandedChunk:
    """확장된 청크 정보."""

    chunk_id: str
    chunk_index: int
    section_title: str
    section_path: str
    score: float
    source: Literal["sibling", "section_title_search"]
    source_chunk_id: str | None = None


async def expand_siblings(
    hit_chunk_ids: list[str],
    hit_scores: dict[str, float],
    graph_repo: TreeGraphRepo,
    *,
    window: int = 2,
    max_per_hit: int = 4,
    score_decay: float = 0.85,
    max_total_chars: int = 8000,
) -> list[ExpandedChunk]:
    """히트된 청크의 형제 청크를 트리에서 확장.

    Args:
        hit_chunk_ids: 리랭킹된 상위 청크 ID 목록
        hit_scores: {chunk_id: score} 매핑
        graph_repo: Neo4j 그래프 레포지토리
        window: 앞뒤 확장 범위
        max_per_hit: 히트당 최대 확장 수
        score_decay: 확장 청크 점수 감소율 (원본 × decay)
        max_total_chars: 확장 최대 총 문자 수 (토큰 예산)

    Returns:
        확장된 청크 리스트 (score 내림차순)
    """
    if not hit_chunk_ids:
        return []

    try:
        siblings_map = await graph_repo.find_tree_siblings_batch(
            hit_chunk_ids, window=window,
        )
    except NEO4J_FAILURE as e:
        logger.warning("Tree sibling expansion failed: %s", e)
        return []

    already_in = set(hit_chunk_ids)
    expanded: list[ExpandedChunk] = []
    total_chars = 0

    for source_id in hit_chunk_ids:
        siblings = siblings_map.get(source_id, [])
        source_score = hit_scores.get(source_id, 0.0)
        count = 0

        for sib in siblings:
            sib_id = sib.get("chunk_id", "")
            if sib_id in already_in:
                continue
            if count >= max_per_hit:
                break
            if total_chars >= max_total_chars:
                break

            expanded.append(ExpandedChunk(
                chunk_id=sib_id,
                chunk_index=sib.get("chunk_index", 0),
                section_title=sib.get("section_title", ""),
                section_path=sib.get("section_path", ""),
                score=source_score * score_decay,
                source="sibling",
                source_chunk_id=source_id,
            ))
            already_in.add(sib_id)
            count += 1
            # content 길이 정확히 모르므로 청크 평균 ~500자로 추정
            total_chars += 500

        if total_chars >= max_total_chars:
            break

    expanded.sort(key=lambda x: x.score, reverse=True)
    return expanded


async def search_by_section_titles(
    query: str,
    graph_repo: TreeGraphRepo,
    *,
    kb_id: str | None = None,
    existing_chunk_ids: set[str] | None = None,
    limit: int = 10,
    default_score: float = 0.3,
) -> list[ExpandedChunk]:
    """섹션 제목 fulltext 검색으로 벡터 미스 보완."""
    existing = existing_chunk_ids or set()

    try:
        results = await graph_repo.search_section_titles(
            query, kb_id=kb_id, limit=limit * 2,
        )
    except NEO4J_FAILURE as e:
        logger.warning("Section title search failed: %s", e)
        return []

    expanded: list[ExpandedChunk] = []
    seen: set[str] = set()

    for r in results:
        cid = r.get("chunk_id", "")
        if cid in existing or cid in seen:
            continue
        seen.add(cid)
        expanded.append(ExpandedChunk(
            chunk_id=cid,
            chunk_index=0,
            section_title=r.get("section_title", ""),
            section_path=r.get("section_path", ""),
            score=default_score,
            source="section_title_search",
        ))
        if len(expanded) >= limit:
            break

    return expanded


async def get_section_bonus_map(
    chunk_ids: list[str],
    graph_repo: TreeGraphRepo,
) -> dict[str, float]:
    """같은 섹션에 2+ 히트된 청크들의 보너스 맵 생성.

    Returns:
        {chunk_id: bonus} — 같은 top-level 섹션에 2개+ 히트 시 bonus > 0
    """
    if not chunk_ids:
        return {}

    try:
        paths = await graph_repo.get_chunk_section_paths_batch(chunk_ids)
    except NEO4J_FAILURE as e:
        logger.warning("Section bonus calculation failed: %s", e)
        return {}

    from src.search.section_utils import get_top_section

    section_groups: dict[str, list[str]] = {}
    for cid, path in paths.items():
        top_section = get_top_section(path)
        if top_section:
            section_groups.setdefault(top_section, []).append(cid)

    bonus_map: dict[str, float] = {}
    for _section, cids in section_groups.items():
        if len(cids) >= 2:
            for cid in cids:
                bonus_map[cid] = 1.0

    return bonus_map
