"""Enrich existing KB data: Person→Document relations + doc_date metadata.

Scans Qdrant chunks, extracts Person names via KiwiPy, creates
MENTIONED_IN relations in Neo4j, and adds doc_date to Qdrant payload.

No re-embedding or re-ingestion needed.

Usage:
    uv run python scripts/enrich_metadata.py           # all KBs
    uv run python scripts/enrich_metadata.py g-espa     # specific KB
"""

from __future__ import annotations

import logging
import re
import sys
from collections import defaultdict

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

QDRANT_URL = "http://localhost:6333"
NEO4J_URL = "http://localhost:7474"
NEO4J_AUTH = ("neo4j", "")
ALL_KBS = ["a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"]

# KiwiPy singleton
_kiwi = None


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    return _kiwi


# ---------------------------------------------------------------------------
# 1. Person extraction via KiwiPy
# ---------------------------------------------------------------------------

# Korean name pattern: 2-4 char Korean + optional M/매니저 suffix
_KOREAN_NAME_RE = re.compile(r"^[가-힣]{2,4}(M|매니저|매니져)?$")

# Noise names to skip: titles, geography, table headers, common terms
_NAME_BLACKLIST = frozenset({
    # 직급/직책
    "담당자", "매니저", "매니져", "팀장", "부장", "과장", "대리", "사원",
    "본부장", "센터장", "실장", "파트장", "그룹장", "차장", "주임", "인턴",
    # 지명/국가
    "대한민국", "서울", "부산", "인천", "대구", "광주", "대전", "울산", "세종",
    "제주", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남",
    # 표/문서 헤더
    "비고", "메타", "합계", "소계", "총계", "항목", "구분", "내용", "결과",
    "상태", "대상", "현황", "요약", "기타", "참고", "완료", "진행", "예정",
    # 일반 명사 (KiwiPy가 NNP로 잘못 태깅)
    "자점만", "비탁", "우리", "본사", "본부", "지점", "점포", "매장",
    "시스템", "서비스", "프로젝트", "프로세스", "플랫폼", "데이터",
    "이슈사항", "대시보드", "업그레이드", "테스트", "개발팀", "운영팀",
})


# Table cell pattern: "| 이름M |" or "| 이름 |" in markdown tables
_TABLE_NAME_RE = re.compile(r"\|\s*([가-힣]{2,4})M?\s*\|")

# Context pattern: "담당자: 이름" or "작성자: 이름" etc.
_CONTEXT_NAME_RE = re.compile(
    r"(?:담당자|작성자|보고자|요청자|승인자|검토자|발표자|OFC)\s*[:\s]\s*([가-힣]{2,4})M?"
)


def extract_persons_from_text(text: str) -> set[str]:
    """Extract Korean person names using KiwiPy NNP + regex patterns.

    Two-tier extraction:
    1. KiwiPy NNP tag (morphological analysis)
    2. Regex fallback for table cells and context patterns
    """
    kiwi = _get_kiwi()
    persons = set()
    sample = text[:3000]

    # Tier 1: KiwiPy NNP
    try:
        tokens = kiwi.tokenize(sample)
        for tok in tokens:
            if tok.tag == "NNP" and _KOREAN_NAME_RE.match(tok.form):
                name = tok.form.rstrip("M")
                if name not in _NAME_BLACKLIST and len(name) >= 2:
                    persons.add(name)
    except Exception:
        pass

    # Tier 2: Regex for table cells (| 유경희 |, | 김재경M |)
    for m in _TABLE_NAME_RE.finditer(sample):
        name = m.group(1)
        if name not in _NAME_BLACKLIST and len(name) >= 2:
            persons.add(name)

    # Tier 3: Context patterns (담당자: 유경희)
    for m in _CONTEXT_NAME_RE.finditer(sample):
        name = m.group(1)
        if name not in _NAME_BLACKLIST and len(name) >= 2:
            persons.add(name)

    return persons


# ---------------------------------------------------------------------------
# 2. Document name → date extraction
# ---------------------------------------------------------------------------

# Patterns: "2024_04", "2024-04", "2024.04", "202404", "2024년 4월"
_DATE_PATTERNS = [
    re.compile(r"(20\d{2})[_\-./](0[1-9]|1[0-2])"),           # 2024_04, 2024-04
    re.compile(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])"),  # 20240430
    re.compile(r"(20\d{2})년\s*(1?[0-9])월"),                   # 2024년 4월
    re.compile(r"(20\d{2})[_\-.](\d{1,2})[_\-.](\d{1,2})"),   # 2024-4-30
    re.compile(r"(\d{1,2})월\s*(\d{1,2})주"),                   # 4월 3주차 (no year)
]


def extract_date_from_docname(doc_name: str) -> str:
    """Extract date string (YYYY-MM format) from document name."""
    if not doc_name:
        return ""

    # Try each pattern
    for i, pattern in enumerate(_DATE_PATTERNS):
        m = pattern.search(doc_name)
        if m:
            groups = m.groups()
            if i == 0:  # 2024_04
                return f"{groups[0]}-{groups[1]}"
            elif i == 1:  # 20240430
                return f"{groups[0]}-{groups[1]}"
            elif i == 2:  # 2024년 4월
                return f"{groups[0]}-{int(groups[1]):02d}"
            elif i == 3:  # 2024-4-30
                return f"{groups[0]}-{int(groups[1]):02d}"
    return ""


# ---------------------------------------------------------------------------
# 3. Qdrant scanning
# ---------------------------------------------------------------------------


def scroll_all_chunks(collection: str) -> list[dict]:
    """Scroll through all chunks in a Qdrant collection."""
    chunks = []
    offset = None

    while True:
        body = {
            "limit": 100,
            "with_payload": ["content", "document_name", "doc_id", "chunk_type"],
            "with_vector": False,
        }
        if offset:
            body["offset"] = offset

        resp = requests.post(
            f"{QDRANT_URL}/collections/{collection}/points/scroll",
            json=body, timeout=10,
        )
        if resp.status_code != 200:
            break

        data = resp.json().get("result", {})
        points = data.get("points", [])
        if not points:
            break

        for p in points:
            pay = p["payload"]
            chunks.append({
                "point_id": p["id"],
                "content": pay.get("content", ""),
                "document_name": pay.get("document_name", ""),
                "doc_id": pay.get("doc_id", ""),
                "chunk_type": pay.get("chunk_type", "body"),
            })

        offset = data.get("next_page_offset")
        if not offset:
            break

    return chunks


# ---------------------------------------------------------------------------
# 4. Neo4j: create MENTIONED_IN relations
# ---------------------------------------------------------------------------


def create_mentioned_in_relations(kb_id: str, person_doc_map: dict[str, set[str]]):
    """Create Person -[MENTIONED_IN]-> Document relations in Neo4j."""
    created = 0

    for person_name, doc_names in person_doc_map.items():
        for doc_name in doc_names:
            query = """
            MERGE (p:Person:__Entity__ {name: $person})
            MERGE (d:Document {name: $doc_name, kb_id: $kb_id})
            MERGE (p)-[r:MENTIONED_IN]->(d)
            ON CREATE SET r.created_at = datetime(), r.source = 'enrich_batch'
            RETURN type(r)
            """
            try:
                resp = requests.post(
                    f"{NEO4J_URL}/db/neo4j/tx/commit",
                    json={"statements": [{"statement": query, "parameters": {
                        "person": person_name,
                        "doc_name": doc_name,
                        "kb_id": kb_id,
                    }}]},
                    auth=NEO4J_AUTH,
                    timeout=5,
                )
                if resp.status_code == 200:
                    results = resp.json().get("results", [{}])
                    if results and results[0].get("data"):
                        created += 1
            except Exception as e:
                logger.warning(f"Neo4j write failed for {person_name}->{doc_name}: {e}")

    return created


# ---------------------------------------------------------------------------
# 5. Qdrant: add doc_date payload
# ---------------------------------------------------------------------------


def update_doc_dates(collection: str, doc_date_map: dict[str, str]):
    """Update Qdrant points with doc_date payload field."""
    updated = 0

    # Group point_ids by doc_date for batch update
    date_to_points: dict[str, list] = defaultdict(list)
    for point_id, doc_date in doc_date_map.items():
        if doc_date:
            date_to_points[doc_date].append(point_id)

    for doc_date, point_ids in date_to_points.items():
        # Batch update in chunks of 100
        for i in range(0, len(point_ids), 100):
            batch = point_ids[i:i + 100]
            try:
                resp = requests.post(
                    f"{QDRANT_URL}/collections/{collection}/points/payload",
                    json={
                        "payload": {"doc_date": doc_date},
                        "points": batch,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    updated += len(batch)
            except Exception as e:
                logger.warning(f"Qdrant payload update failed: {e}")

    return updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def enrich_kb(kb_id: str):
    """Enrich a single KB with Person relations and doc_date metadata."""
    collection = f"kb_{kb_id.replace('-', '_')}"
    logger.info(f"[{kb_id}] Scanning {collection}...")

    chunks = scroll_all_chunks(collection)
    logger.info(f"[{kb_id}] {len(chunks)} chunks loaded")

    if not chunks:
        logger.warning(f"[{kb_id}] No chunks found")
        return

    # 1. Extract persons per document
    person_doc_map: dict[str, set[str]] = defaultdict(set)
    doc_date_map: dict[str, str] = {}  # point_id -> date
    dates_found = 0

    for chunk in chunks:
        doc_name = chunk["document_name"]

        # Person extraction (skip title-only chunks)
        if chunk["chunk_type"] != "title":
            persons = extract_persons_from_text(chunk["content"])
            for person in persons:
                person_doc_map[person].add(doc_name)

        # Date extraction from document name
        doc_date = extract_date_from_docname(doc_name)
        if doc_date:
            doc_date_map[chunk["point_id"]] = doc_date
            dates_found += 1

    unique_persons = len(person_doc_map)
    total_relations = sum(len(docs) for docs in person_doc_map.values())
    unique_dates = len(set(doc_date_map.values()))

    logger.info(
        f"[{kb_id}] Extracted: {unique_persons} persons, "
        f"{total_relations} person-doc pairs, "
        f"{unique_dates} unique dates from {dates_found} chunks"
    )

    # 2. Create Neo4j relations
    neo4j_created = create_mentioned_in_relations(kb_id, person_doc_map)
    logger.info(f"[{kb_id}] Neo4j: {neo4j_created} MENTIONED_IN relations created")

    # 3. Update Qdrant payload
    qdrant_updated = update_doc_dates(collection, doc_date_map)
    logger.info(f"[{kb_id}] Qdrant: {qdrant_updated} chunks updated with doc_date")

    # Sample output
    if person_doc_map:
        sample = list(person_doc_map.items())[:3]
        for name, docs in sample:
            logger.info(f"  Person: {name} → {list(docs)[:3]}")


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else ALL_KBS

    for kb_id in targets:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"[START] {kb_id}")
        logger.info(f"{'=' * 60}")
        enrich_kb(kb_id)

    logger.info(f"\n{'=' * 60}")
    logger.info("ALL DONE")
