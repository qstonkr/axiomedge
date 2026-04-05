"""Search route helpers — sub-steps extracted from hub_search() in search.py."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ── 4.35  Identifier search ─────────────────────────────────────────────────


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
    _identifiers: list[str] = []
    # Numbers with commas (금액: 6,720,009)
    _identifiers.extend(re.findall(r"\d{1,3}(?:,\d{3})+", display_query))
    # JIRA keys (GRIT-12345, HANGBOT-999)
    _identifiers.extend(re.findall(r"[A-Z]+-\d{3,}", display_query))
    # Store codes (VL820, VI664)
    _identifiers.extend(re.findall(r"[A-Z]{2}\d{3}", display_query))
    # Error codes (E-4001)
    _identifiers.extend(re.findall(r"E-\d{4}", display_query))
    # File names with extensions
    _identifiers.extend(
        re.findall(
            r"[\w\-\.]+\.(?:xml|java|py|json|yaml|yml|properties|sql|csv|xlsx)",
            display_query,
        )
    )
    # CamelCase identifiers (PwdFailCntLimitCache, AssetBundlService)
    _identifiers.extend(re.findall(r"[A-Z][a-z]+(?:[A-Z][a-z]+){2,}", display_query))

    if not _identifiers or not all_chunks:
        return all_chunks

    _existing_ids = {c.get("chunk_id", "") for c in all_chunks}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            for ident in _identifiers[:3]:  # Max 3 identifiers
                for coll in collections:
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
                    if resp.status_code == 200:
                        for pt in resp.json().get("result", {}).get("points", []):
                            pid = str(pt["id"])
                            if pid not in _existing_ids:
                                pay = pt.get("payload", {})
                                all_chunks.append(
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
                                _existing_ids.add(pid)
        if _identifiers:
            _id_count = sum(1 for c in all_chunks if c.get("_identifier_match"))
            if _id_count:
                logger.info(
                    "Identifier search: %s → %d chunks injected",
                    _identifiers[:3],
                    _id_count,
                )
    except Exception as e:
        logger.debug("Identifier search failed: %s", e)

    return all_chunks


# ── 4.4  Keyword boost ──────────────────────────────────────────────────────


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
    if query_tokens and len(collections) > 1:
        keyword_chunks: list[dict] = []
        other_chunks: list[dict] = []
        for chunk in all_chunks:
            content_lower = chunk.get("content", "").lower()
            matched = sum(1 for t in query_tokens if t in content_lower)
            if matched > 0:
                ratio = matched / len(query_tokens)
                chunk["score"] = chunk.get("score", 0) + keyword_boost_weight * ratio
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
    else:
        # Single KB or no keywords: standard score-based cutoff with keyword boost
        for chunk in all_chunks:
            if query_tokens:
                content_lower = chunk.get("content", "").lower()
                matched = sum(1 for t in query_tokens if t in content_lower)
                if matched > 0:
                    chunk["score"] = chunk.get("score", 0) + 0.3 * (matched / len(query_tokens))
        all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
        all_chunks = all_chunks[:pool_size]

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

        async with httpx.AsyncClient(timeout=3.0) as dc:
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
    except Exception as e:
        logger.debug("Date-filtered search failed: %s", e)

    all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
    all_chunks = all_chunks[:pool_size]
    return all_chunks


# ── 4.46  Week-name search ──────────────────────────────────────────────────


async def week_name_search(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    collections: list[str],
    qdrant_url: str,
    pool_size: int,
) -> list[dict[str, Any]]:
    """Search document_name for week-specific strings in weekly-report KBs."""
    _week_filter_texts: list[str] = []
    _week_pattern_label = ""

    # --- Pattern A (original): "N월 N주차" ---
    _week_match = re.search(r"(\d{1,2})월\s*(\d{1,2})주차", display_query)
    if _week_match:
        _q_month = int(_week_match.group(1))
        _q_week_num = int(_week_match.group(2))
        _week_filter_texts = [
            f"{_q_month}월 {_q_week_num}주차",
            f"{_q_month:02d}월 {_q_week_num:02d}주차",
            f"{_q_month:02d}월 0{_q_week_num}주차",
            f"{_q_month}월 0{_q_week_num}주차",
        ]
        _week_pattern_label = f"A: {_q_month}월 {_q_week_num}주차"

    # --- Pattern E: "YYYY년 N주차" (no month) ---
    if not _week_filter_texts:
        _week_match_e = re.search(r"(\d{4})년\s*(\d{1,2})주차", display_query)
        if _week_match_e:
            _e_year = _week_match_e.group(1)
            _e_wk = int(_week_match_e.group(2))
            _week_filter_texts = [f"{_e_year}_{_e_wk:02d}"]
            _week_pattern_label = f"E: {_e_year}년 {_e_wk}주차"

    # --- Pattern D3: "YYYY-MM-DD" or "YYYY년 M월 D일" exact date ---
    if not _week_filter_texts:
        _week_match_d3a = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", display_query)
        _week_match_d3b = re.search(
            r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", display_query
        )
        _d3_match = _week_match_d3a or _week_match_d3b
        if _d3_match:
            _d3_y = _d3_match.group(1)
            _d3_m = int(_d3_match.group(2))
            _d3_d = int(_d3_match.group(3))
            _week_filter_texts = [f"{_d3_y}-{_d3_m:02d}-{_d3_d:02d}"]
            _week_pattern_label = f"D3: {_d3_y}-{_d3_m:02d}-{_d3_d:02d}"

    # --- Pattern D1: "M월 D일" (specific date, no year) ---
    _week_d1_month: int | None = None
    _week_d1_day: int | None = None
    if not _week_filter_texts:
        _week_match_d1 = re.search(r"(\d{1,2})월\s*(\d{1,2})일", display_query)
        if _week_match_d1:
            _week_d1_month = int(_week_match_d1.group(1))
            _week_d1_day = int(_week_match_d1.group(2))
            _week_filter_texts = [f"{_week_d1_month:02d}월"]
            _week_pattern_label = f"D1: {_week_d1_month}월 {_week_d1_day}일"

    if not _week_filter_texts or not all_chunks:
        return all_chunks

    _existing_docs_wk = {c.get("document_name", "") for c in all_chunks}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as wk_client:
            for coll in collections:
                _coll_name = f"kb_{coll.replace('-', '_')}"
                for _variant in _week_filter_texts:
                    resp = await wk_client.post(
                        f"{qdrant_url}/collections/{_coll_name}/points/scroll",
                        json={
                            "limit": 20,
                            "with_payload": True,
                            "with_vector": False,
                            "filter": {
                                "must": [
                                    {
                                        "key": "document_name",
                                        "match": {"text": _variant},
                                    },
                                ]
                            },
                        },
                    )
                    if resp.status_code != 200:
                        continue
                    for pt in resp.json().get("result", {}).get("points", []):
                        pay = pt.get("payload", {})
                        dn = pay.get("document_name", "")
                        if not dn or dn in _existing_docs_wk:
                            continue

                        # D1 post-filter: check if date falls within range
                        if _week_d1_month is not None and _week_d1_day is not None:
                            _range_m = re.search(
                                r"\((\d{2})/(\d{2})\s*~\s*(\d{2})/(\d{2})\)", dn
                            )
                            if _range_m:
                                _rm = int(_range_m.group(1))
                                _rd_s = int(_range_m.group(2))
                                _rm_e = int(_range_m.group(3))
                                _rd_e = int(_range_m.group(4))
                                _in_range = False
                                if _rm == _rm_e:
                                    _in_range = (
                                        _week_d1_month == _rm
                                        and _rd_s <= _week_d1_day <= _rd_e
                                    )
                                else:
                                    if _week_d1_month == _rm:
                                        _in_range = _week_d1_day >= _rd_s
                                    elif _week_d1_month == _rm_e:
                                        _in_range = _week_d1_day <= _rd_e
                                if not _in_range:
                                    continue
                            elif "주차" not in dn:
                                continue

                        all_chunks.append(
                            {
                                "chunk_id": str(pt["id"]),
                                "content": pay.get("content", ""),
                                "document_name": dn,
                                "source_uri": pay.get("source_uri", ""),
                                "metadata": pay,
                                "score": 1.1,
                                "kb_id": coll,
                                "_week_matched": True,
                            }
                        )
                        _existing_docs_wk.add(dn)
        _wk_injected = sum(1 for c in all_chunks if c.get("_week_matched"))
        if _wk_injected:
            logger.info(
                "Week-name search [%s] → %d chunks injected",
                _week_pattern_label,
                _wk_injected,
            )
            all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
            all_chunks = all_chunks[:pool_size]
    except Exception as e:
        logger.debug("Week-name search failed: %s", e)

    return all_chunks


# ── 6  Graph expansion ──────────────────────────────────────────────────────


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
        if hasattr(graph_expander, "expand_with_entities"):
            expansion = await asyncio.wait_for(
                graph_expander.expand_with_entities(
                    display_query,
                    all_chunks,
                    scope_kb_ids=collections,
                ),
                timeout=5.0,
            )
        else:
            expansion = await asyncio.wait_for(
                graph_expander.expand(
                    display_query,
                    all_chunks,
                    scope_kb_ids=collections,
                ),
                timeout=5.0,
            )
        logger.info(
            "Graph expansion: %d URIs, %d related, URIs=%s",
            len(expansion.expanded_source_uris),
            expansion.graph_related_count,
            list(expansion.expanded_source_uris)[:5],
        )
        if expansion.expanded_source_uris:
            # Boost existing chunks that match graph expansion
            all_chunks = graph_expander.boost_chunks(
                all_chunks, expansion.expanded_source_uris
            )

            # Inject graph-found documents NOT already in results
            existing_docs: set[str] = set()
            for c in all_chunks:
                dn = c.get("document_name", "")
                su = c.get("source_uri", "")
                if dn:
                    existing_docs.add(_uc.normalize("NFC", dn))
                if su:
                    existing_docs.add(_uc.normalize("NFC", su))
            new_docs = {
                d
                for d in expansion.expanded_source_uris
                if _uc.normalize("NFC", d) not in existing_docs
            }
            logger.info(
                "Graph injection: existing=%d, new=%d, new_docs=%s",
                len(existing_docs),
                len(new_docs),
                list(new_docs)[:3],
            )
            if new_docs:
                import httpx

                async with httpx.AsyncClient(timeout=3.0) as qc:
                    for doc_name in list(new_docs)[:5]:
                        for coll in collections:
                            try:
                                coll_name = f"kb_{coll.replace('-', '_')}"
                                dn_nfd = _uc.normalize("NFD", doc_name)
                                _match_filters = [
                                    {
                                        "must": [
                                            {
                                                "key": "document_name",
                                                "match": {"value": doc_name},
                                            }
                                        ]
                                    },
                                    {
                                        "must": [
                                            {
                                                "key": "document_name",
                                                "match": {"value": dn_nfd},
                                            }
                                        ]
                                    },
                                    {
                                        "must": [
                                            {
                                                "key": "source_uri",
                                                "match": {"text": doc_name},
                                            }
                                        ]
                                    },
                                ]
                                resp = None
                                for _filt in _match_filters:
                                    resp = await qc.post(
                                        f"{qdrant_url}/collections/{coll_name}/points/scroll",
                                        json={
                                            "limit": 2,
                                            "with_payload": True,
                                            "with_vector": False,
                                            "filter": _filt,
                                        },
                                    )
                                    if resp.status_code == 200:
                                        pts = (
                                            resp.json()
                                            .get("result", {})
                                            .get("points", [])
                                        )
                                        if pts:
                                            break
                                if resp and resp.status_code == 200:
                                    points = (
                                        resp.json()
                                        .get("result", {})
                                        .get("points", [])
                                    )
                                    for pt in points:
                                        pay = pt.get("payload", {})
                                        _inject_score = 0.35
                                        _dn = pay.get("document_name", "").lower()
                                        _q_lower = display_query.lower()
                                        _q_words = [
                                            w for w in _q_lower.split() if len(w) >= 2
                                        ]
                                        _match_count = sum(
                                            1 for w in _q_words if w in _dn
                                        )
                                        if _match_count >= 2:
                                            _inject_score = 0.75
                                        elif _match_count == 1:
                                            _inject_score = 0.55
                                        all_chunks.append(
                                            {
                                                "content": pay.get("content", ""),
                                                "document_name": pay.get(
                                                    "document_name", ""
                                                ),
                                                "source_uri": pay.get(
                                                    "source_uri", ""
                                                ),
                                                "metadata": pay,
                                                "score": _inject_score,
                                                "graph_injected": True,
                                                "graph_boosted": True,
                                            }
                                        )
                            except Exception as e:
                                logger.debug(
                                    "Graph chunk injection failed: %s", e
                                )

            all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
    except asyncio.TimeoutError:
        logger.warning("Graph expansion timed out (5s), skipping")
    except Exception as e:
        logger.warning("Graph expansion failed in search route: %s", e)

    return all_chunks
