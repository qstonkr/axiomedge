"""Search route helpers — sub-steps extracted from hub_search() in search.py."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from src.config_weights import weights as _w

logger = logging.getLogger(__name__)


# ── KB registry active_kb_ids cache ──
# hub_search 매 요청마다 `kb_registry.list_all()` 을 호출해서 active KB 를
# 필터링하면 search hot path 에 DB round-trip 이 한 번 더 발생. KB 활성/비활성
# 상태는 초 단위로 변하지 않으므로 짧은 TTL 메모리 캐시로 대부분 hit.
_KB_REGISTRY_CACHE_TTL_S = 60.0
_kb_registry_cache: dict[str, tuple[float, set[str]]] = {}


async def get_active_kb_ids(kb_registry) -> set[str]:
    """Return currently-active KB id set with TTL cache.

    Cache key 는 registry 인스턴스 id — app lifespan 동안 여러 registry 가
    생성될 일은 없지만 방어적으로 인스턴스 구분.
    """
    cache_key = str(id(kb_registry))
    now = time.monotonic()
    cached = _kb_registry_cache.get(cache_key)
    if cached and now - cached[0] < _KB_REGISTRY_CACHE_TTL_S:
        return cached[1]

    accessible_kbs = await kb_registry.list_all()
    active = {kb["kb_id"] for kb in accessible_kbs if kb.get("status") == "active"}
    _kb_registry_cache[cache_key] = (now, active)
    return active


# ── 4.35  Identifier search ─────────────────────────────────────────────────


def _extract_identifiers(display_query: str) -> list[str]:
    """Extract identifiers (numbers, JIRA keys, codes, filenames) from query."""
    identifiers: list[str] = []
    # Numbers with commas (금액: 6,720,009)
    identifiers.extend(re.findall(r"\d{1,3}(?:,\d{3})+", display_query))
    # JIRA keys (GRIT-12345, HANGBOT-999)
    identifiers.extend(re.findall(r"[A-Z]+-\d{3,}", display_query))
    # Store codes (VL820, VI664)
    identifiers.extend(re.findall(r"[A-Z]{2}\d{3}", display_query))
    # Error codes (E-4001)
    identifiers.extend(re.findall(r"E-\d{4}", display_query))
    # File names with extensions
    identifiers.extend(
        re.findall(
            r"[\w\-\.]+\.(?:xml|java|py|json|yaml|yml|properties|sql|csv|xlsx)",
            display_query,
        )
    )
    # CamelCase identifiers (PwdFailCntLimitCache, AssetBundlService)
    identifiers.extend(re.findall(r"[A-Z][a-z]+(?:[A-Z][a-z]+){2,}", display_query))
    return identifiers


async def _scroll_identifier_chunks(
    client: Any,
    ident: str,
    coll: str,
    qdrant_url: str,
    existing_ids: set[str],
) -> list[dict[str, Any]]:
    """Scroll Qdrant for chunks matching a single identifier in one collection."""
    new_chunks: list[dict[str, Any]] = []
    _coll_name = f"kb_{coll.replace('-', '_')}"
    resp = await client.post(
        f"{qdrant_url}/collections/{_coll_name}/points/scroll",
        json={
            "limit": 3,
            "with_payload": True,
            "with_vector": False,
            "filter": {
                "must": [
                    {"key": "content", "match": {"text": ident}},
                ]
            },
        },
    )
    if resp.status_code != 200:
        return new_chunks

    for pt in resp.json().get("result", {}).get("points", []):
        pid = str(pt["id"])
        if pid in existing_ids:
            continue
        pay = pt.get("payload", {})
        new_chunks.append(
            {
                "chunk_id": pid,
                "content": pay.get("content", ""),
                "score": 0.6,
                "kb_id": coll,
                "document_name": pay.get("document_name", ""),
                "source_uri": pay.get("source_uri", ""),
                "metadata": pay,
                "_identifier_match": True,
            }
        )
        existing_ids.add(pid)
    return new_chunks


async def identifier_search(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    collections: list[str],
    qdrant_url: str,
) -> list[dict[str, Any]]:
    """Search for specific identifiers (numbers, JIRA keys, codes, store names)
    that vector search often misses.  Appends new chunks in-place and returns
    the updated list.
    """
    _identifiers = _extract_identifiers(display_query)

    if not _identifiers or not all_chunks:
        return all_chunks

    _existing_ids = {c.get("chunk_id", "") for c in all_chunks}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_w.timeouts.httpx_search_scroll) as client:
            for ident in _identifiers[:3]:  # Max 3 identifiers
                for coll in collections:
                    new = await _scroll_identifier_chunks(
                        client, ident, coll, qdrant_url, _existing_ids,
                    )
                    all_chunks.extend(new)
        _id_count = sum(1 for c in all_chunks if c.get("_identifier_match"))
        if _id_count:
            logger.info(
                "Identifier search: %s → %d chunks injected",
                _identifiers[:3],
                _id_count,
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("Identifier search failed: %s", e)

    return all_chunks


# ── 4.4  Keyword boost ──────────────────────────────────────────────────────


def _apply_token_boost(
    chunk: dict[str, Any], query_tokens: list[str], weight: float,
) -> int:
    """Apply keyword boost to a chunk's score. Returns the number of matched tokens."""
    content_lower = chunk.get("content", "").lower()
    matched = sum(1 for t in query_tokens if t in content_lower)
    if matched > 0:
        chunk["score"] = chunk.get("score", 0) + weight * (matched / len(query_tokens))
    return matched


def keyword_boost(
    all_chunks: list[dict[str, Any]],
    query_tokens: list[str],
    collections: list[str],
    pool_size: int,
    keyword_boost_weight: float,
) -> list[dict[str, Any]]:
    """Keyword-match priority + KB diversity selection.

    When multiple KBs are searched, keyword-matching chunks are prioritised
    so they always enter the reranking pool.
    """
    if not query_tokens or len(collections) <= 1:
        # Single KB or no keywords: standard score-based cutoff with keyword boost
        for chunk in all_chunks:
            if query_tokens:
                _apply_token_boost(chunk, query_tokens, 0.3)
        all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
        return all_chunks[:pool_size]

    keyword_chunks: list[dict] = []
    other_chunks: list[dict] = []
    for chunk in all_chunks:
        matched = _apply_token_boost(chunk, query_tokens, keyword_boost_weight)
        if matched > 0:
            chunk["_keyword_matched"] = True
            keyword_chunks.append(chunk)
        else:
            other_chunks.append(chunk)

    keyword_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
    other_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
    all_chunks = keyword_chunks + other_chunks
    all_chunks = all_chunks[:pool_size]
    logger.info(
        "Keyword selection: %d keyword-matched + %d other = %d pool",
        len(keyword_chunks),
        min(len(other_chunks), pool_size - len(keyword_chunks)),
        len(all_chunks),
    )
    return all_chunks


# ── 4.42  Document diversity ────────────────────────────────────────────────


def document_diversity(
    all_chunks: list[dict[str, Any]],
    pool_size: int,
    max_chunks_per_doc: int = 5,
) -> list[dict[str, Any]]:
    """Prevent a single document from dominating top-K.

    Excess chunks beyond *max_chunks_per_doc* per document are pushed to the
    back of the pool.
    """
    doc_counts: dict[str, int] = {}
    diverse: list[dict] = []
    overflow: list[dict] = []
    for chunk in all_chunks:
        dn = chunk.get("document_name", "")
        doc_counts[dn] = doc_counts.get(dn, 0) + 1
        if doc_counts[dn] <= max_chunks_per_doc:
            diverse.append(chunk)
        else:
            overflow.append(chunk)
    return (diverse + overflow)[:pool_size]


# ── 4.45  Date-filtered search ──────────────────────────────────────────────


async def date_filter_search(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    collections: list[str],
    qdrant_url: str,
    pool_size: int,
) -> list[dict[str, Any]]:
    """Supplementary Qdrant scroll with doc_date filter for date-containing
    queries.
    """
    _date_match = re.search(r"(20\d{2})년\s*(\d{1,2})월", display_query)
    if not _date_match:
        _date_match = re.search(r"(20\d{2})[_\-](0[1-9]|1[0-2])", display_query)
    if not _date_match:
        return all_chunks

    _q_date = f"{_date_match.group(1)}-{int(_date_match.group(2)):02d}"
    _existing_docs = {c.get("document_name", "") for c in all_chunks}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_w.timeouts.httpx_search_scroll) as dc:
            for coll in collections:
                _coll_name = f"kb_{coll.replace('-', '_')}"
                resp = await dc.post(
                    f"{qdrant_url}/collections/{_coll_name}/points/scroll",
                    json={
                        "limit": 5,
                        "with_payload": True,
                        "with_vector": False,
                        "filter": {
                            "must": [{"key": "doc_date", "match": {"value": _q_date}}]
                        },
                    },
                )
                if resp.status_code == 200:
                    for pt in resp.json().get("result", {}).get("points", []):
                        pay = pt.get("payload", {})
                        dn = pay.get("document_name", "")
                        if dn and dn not in _existing_docs:
                            all_chunks.append(
                                {
                                    "chunk_id": str(pt["id"]),
                                    "content": pay.get("content", ""),
                                    "document_name": dn,
                                    "source_uri": pay.get("source_uri", ""),
                                    "metadata": pay,
                                    "score": 0.6,
                                    "kb_id": coll,
                                    "_date_filtered": True,
                                }
                            )
                            _existing_docs.add(dn)
        logger.info(
            "Date-filtered search: doc_date=%s, injected %d chunks",
            _q_date,
            sum(1 for c in all_chunks if c.get("_date_filtered")),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("Date-filtered search failed: %s", e)

    all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
    all_chunks = all_chunks[:pool_size]
    return all_chunks


# ── 4.46  Week-name search ──────────────────────────────────────────────────


def _parse_week_pattern(
    display_query: str,
) -> tuple[list[str], str, int | None, int | None]:
    """Parse week/date patterns from query. Returns (filter_texts, label, d1_month, d1_day)."""
    # Pattern A: "N월 N주차"
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})주차", display_query)
    if m:
        mo, wk = int(m.group(1)), int(m.group(2))
        return [
            f"{mo}월 {wk}주차", f"{mo:02d}월 {wk:02d}주차",
            f"{mo:02d}월 0{wk}주차", f"{mo}월 0{wk}주차",
        ], f"A: {mo}월 {wk}주차", None, None

    # Pattern E: "YYYY년 N주차"
    m = re.search(r"(\d{4})년\s*(\d{1,2})주차", display_query)
    if m:
        yr, wk = m.group(1), int(m.group(2))
        return [f"{yr}_{wk:02d}"], f"E: {yr}년 {wk}주차", None, None

    # Pattern D3: "YYYY-MM-DD" or "YYYY년 M월 D일"
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", display_query)
    if not m:
        m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", display_query)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return [f"{y}-{mo:02d}-{d:02d}"], f"D3: {y}-{mo:02d}-{d:02d}", None, None

    # Pattern D1: "M월 D일"
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", display_query)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        return [f"{mo:02d}월"], f"D1: {mo}월 {d}일", mo, d

    return [], "", None, None


def _d1_date_in_range(dn: str, d1_month: int, d1_day: int) -> bool:
    """Check if a D1 date falls within a document name's date range."""
    range_m = re.search(r"\((\d{2})/(\d{2})\s*~\s*(\d{2})/(\d{2})\)", dn)
    if not range_m:
        return "주차" in dn

    rm, rd_s = int(range_m.group(1)), int(range_m.group(2))
    rm_e, rd_e = int(range_m.group(3)), int(range_m.group(4))

    if rm == rm_e:
        return d1_month == rm and rd_s <= d1_day <= rd_e
    if d1_month == rm:
        return d1_day >= rd_s
    if d1_month == rm_e:
        return d1_day <= rd_e
    return False


def _process_week_point(
    pt: dict[str, Any],
    coll: str,
    existing_docs: set[str],
    d1_month: int | None,
    d1_day: int | None,
    all_chunks: list[dict[str, Any]],
) -> None:
    """Process a single Qdrant point for week-name search injection."""
    pay = pt.get("payload", {})
    dn = pay.get("document_name", "")
    if not dn or dn in existing_docs:
        return
    if d1_month is not None and d1_day is not None:
        if not _d1_date_in_range(dn, d1_month, d1_day):
            return
    all_chunks.append({
        "chunk_id": str(pt["id"]),
        "content": pay.get("content", ""),
        "document_name": dn,
        "source_uri": pay.get("source_uri", ""),
        "metadata": pay,
        "score": 1.1,
        "kb_id": coll,
        "_week_matched": True,
    })
    existing_docs.add(dn)


async def _scroll_week_variants(
    wk_client: Any,
    qdrant_url: str,
    collections: list[str],
    filter_texts: list[str],
    existing_docs: set[str],
    d1_month: int | None,
    d1_day: int | None,
    all_chunks: list[dict[str, Any]],
) -> None:
    """Scroll Qdrant for week-name variants across collections."""
    for coll in collections:
        coll_name = f"kb_{coll.replace('-', '_')}"
        for variant in filter_texts:
            resp = await wk_client.post(
                f"{qdrant_url}/collections/{coll_name}/points/scroll",
                json={
                    "limit": 20, "with_payload": True, "with_vector": False,
                    "filter": {"must": [
                        {"key": "document_name", "match": {"text": variant}},
                    ]},
                },
            )
            if resp.status_code != 200:
                continue
            for pt in resp.json().get("result", {}).get("points", []):
                _process_week_point(pt, coll, existing_docs, d1_month, d1_day, all_chunks)


async def week_name_search(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    collections: list[str],
    qdrant_url: str,
    pool_size: int,
) -> list[dict[str, Any]]:
    """Search document_name for week-specific strings in weekly-report KBs."""
    filter_texts, pattern_label, d1_month, d1_day = _parse_week_pattern(display_query)

    if not filter_texts or not all_chunks:
        return all_chunks

    existing_docs = {c.get("document_name", "") for c in all_chunks}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_w.timeouts.httpx_search_scroll) as wk_client:
            await _scroll_week_variants(
                wk_client, qdrant_url, collections, filter_texts,
                existing_docs, d1_month, d1_day, all_chunks,
            )
        wk_injected = sum(1 for c in all_chunks if c.get("_week_matched"))
        if wk_injected:
            logger.info("Week-name search [%s] → %d chunks injected", pattern_label, wk_injected)
            all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
            all_chunks = all_chunks[:pool_size]
    except Exception as e:  # noqa: BLE001
        logger.debug("Week-name search failed: %s", e)

    return all_chunks


# ── 6  Graph expansion ──────────────────────────────────────────────────────


def _compute_inject_score(display_query: str, doc_name_lower: str) -> float:
    """Compute injection score based on query word matches in document name."""
    q_words = [w for w in display_query.lower().split() if len(w) >= 2]
    match_count = sum(1 for w in q_words if w in doc_name_lower)
    if match_count >= 2:
        return 0.75
    if match_count == 1:
        return 0.55
    return 0.35


def _collect_existing_docs(
    all_chunks: list[dict[str, Any]],
) -> set[str]:
    """Build set of existing document names/URIs (NFC-normalized)."""
    import unicodedata as _uc

    existing: set[str] = set()
    for c in all_chunks:
        for key in ("document_name", "source_uri"):
            val = c.get(key, "")
            if val:
                existing.add(_uc.normalize("NFC", val))
    return existing


async def _scroll_doc_with_filters(
    qc: Any,
    qdrant_url: str,
    coll_name: str,
    match_filters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Try multiple Qdrant filters in order, return points from first match."""
    for filt in match_filters:
        resp = await qc.post(
            f"{qdrant_url}/collections/{coll_name}/points/scroll",
            json={
                "limit": 2, "with_payload": True,
                "with_vector": False, "filter": filt,
            },
        )
        if resp.status_code != 200:
            continue
        pts = resp.json().get("result", {}).get("points", [])
        if pts:
            return pts
    return []


async def _inject_single_doc(
    qc: Any,
    doc_name: str,
    coll: str,
    qdrant_url: str,
    display_query: str,
    all_chunks: list[dict[str, Any]],
) -> None:
    """Inject chunks from a single graph-found document."""
    import unicodedata as _uc

    coll_name = f"kb_{coll.replace('-', '_')}"
    dn_nfd = _uc.normalize("NFD", doc_name)
    match_filters = [
        {"must": [{"key": "document_name", "match": {"value": doc_name}}]},
        {"must": [{"key": "document_name", "match": {"value": dn_nfd}}]},
        {"must": [{"key": "source_uri", "match": {"text": doc_name}}]},
    ]
    pts = await _scroll_doc_with_filters(qc, qdrant_url, coll_name, match_filters)
    for pt in pts:
        pay = pt.get("payload", {})
        all_chunks.append({
            "content": pay.get("content", ""),
            "document_name": pay.get("document_name", ""),
            "source_uri": pay.get("source_uri", ""),
            "metadata": pay,
            "score": _compute_inject_score(
                display_query, pay.get("document_name", "").lower(),
            ),
            "graph_injected": True,
            "graph_boosted": True,
        })


async def _inject_graph_docs(
    new_docs: set[str],
    collections: list[str],
    qdrant_url: str,
    display_query: str,
    all_chunks: list[dict[str, Any]],
) -> None:
    """Inject graph-found documents into chunks via Qdrant scroll."""
    import httpx

    async with httpx.AsyncClient(timeout=_w.timeouts.httpx_search_scroll) as qc:
        for doc_name in list(new_docs)[:5]:
            for coll in collections:
                try:
                    await _inject_single_doc(
                        qc, doc_name, coll, qdrant_url, display_query, all_chunks,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("Graph chunk injection failed: %s", e)


async def graph_expansion(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    collections: list[str],
    graph_expander: Any,
    qdrant_url: str,
) -> list[dict[str, Any]]:
    """Enrich results with structurally related content from Neo4j graph."""
    import asyncio
    import unicodedata as _uc

    try:
        expand_fn = (
            graph_expander.expand_with_entities
            if hasattr(graph_expander, "expand_with_entities")
            else graph_expander.expand
        )
        expansion = await asyncio.wait_for(
            expand_fn(display_query, all_chunks, scope_kb_ids=collections),
            timeout=5.0,
        )
        logger.info(
            "Graph expansion: %d URIs, %d related, URIs=%s",
            len(expansion.expanded_source_uris),
            expansion.graph_related_count,
            list(expansion.expanded_source_uris)[:5],
        )
        if not expansion.expanded_source_uris:
            return all_chunks

        all_chunks = graph_expander.boost_chunks(all_chunks, expansion.expanded_source_uris)

        existing_docs = _collect_existing_docs(all_chunks)
        new_docs = {
            d for d in expansion.expanded_source_uris
            if _uc.normalize("NFC", d) not in existing_docs
        }
        logger.info(
            "Graph injection: existing=%d, new=%d, new_docs=%s",
            len(existing_docs), len(new_docs), list(new_docs)[:3],
        )
        if new_docs:
            await _inject_graph_docs(new_docs, collections, qdrant_url, display_query, all_chunks)

        all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
    except asyncio.TimeoutError:
        logger.warning("Graph expansion timed out (5s), skipping")
    except Exception as e:  # noqa: BLE001
        logger.warning("Graph expansion failed in search route: %s", e)

    return all_chunks


# ── 5.5  Tree expansion helpers ────────────────────────────────────────────


async def retrieve_chunks_by_ids(
    qdrant_client: Any,
    collections: list[str],
    point_ids: list[Any],
    scores: dict[str, float],
    *,
    default_score: float = 0.3,
) -> list[dict[str, Any]]:
    """Qdrant에서 point_id 목록으로 청크를 로드하여 chunk dict 리스트로 반환.

    여러 collection에 대해 병렬로 조회하고 결과를 합침.
    """
    import asyncio

    if not qdrant_client or not point_ids:
        return []

    retrieve_coros = [
        asyncio.to_thread(
            qdrant_client.retrieve,
            collection_name=col, ids=point_ids, with_payload=True,
        )
        for col in collections
    ]
    results = await asyncio.gather(*retrieve_coros, return_exceptions=True)

    chunks: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, BaseException):
            logger.debug("Qdrant retrieve for tree expansion failed: %s", result)
            continue
        for pt in result:
            pid = str(pt.id)
            chunks.append({
                "chunk_id": pid,
                "content": pt.payload.get("content", ""),
                "metadata": pt.payload.get("metadata", {}),
                "score": scores.get(pid, default_score),
                "_tree_expanded": True,
            })
    return chunks
