"""Graph quality cleanup — removes misclassified nodes, placeholders, test data.

Usage:
    uv run python scripts/graph_cleanup.py              # Dry run (report only)
    uv run python scripts/graph_cleanup.py --apply       # Apply fixes
    uv run python scripts/graph_cleanup.py --apply --kb itops_general  # Single KB
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import unicodedata

from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# ============================================================================
# Blocklists & patterns
# ============================================================================

PLACEHOLDER_NAMES = {
    "명시되지 않음", "미상", "unknown", "Unknown", "UNKNOWN",
    "N/A", "n/a", "없음", "미정", "해당없음", "해당 없음",
    "미입력", "미확인", "확인불가", "알수없음", "알 수 없음",
    "-", "--", "---", "TBD", "tbd", "null", "NULL", "None",
}

NON_PERSON_BLOCKLIST = {
    # Systems / platforms
    "JIRA", "Confluence", "Slack", "Teams", "Outlook", "Grafana",
    "Prometheus", "Jenkins", "ArgoCD", "GitLab", "GitHub",
    "Kubernetes", "Docker", "AWS", "Azure", "GCP",
    "SageMaker", "Qdrant", "Neo4j", "PostgreSQL", "Redis", "Kafka",
    "Elasticsearch", "OpenSearch", "Kibana", "Zabbix", "Datadog",
    # Generic non-person terms often misclassified
    "시스템", "서버", "클라이언트", "프로세스", "서비스",
    "모듈", "컴포넌트", "플랫폼", "애플리케이션", "앱",
    "관리자", "운영자", "담당자", "담당 부서",
}

STORE_TO_SYSTEM_BLOCKLIST = {
    # Platforms / systems wrongly labeled as Store
    "JIRA", "Confluence", "Slack", "Teams", "Jenkins", "ArgoCD",
    "GitLab", "GitHub", "Grafana", "Prometheus", "Kibana",
    "Kubernetes", "Docker", "SageMaker", "Qdrant", "Neo4j",
    "PostgreSQL", "Redis", "Kafka", "Elasticsearch", "OpenSearch",
    "Zabbix", "Datadog", "AWS", "Azure", "GCP",
}

STORE_PRODUCT_REMOVE = {
    # Products wrongly labeled as Store — these should be removed entirely
    "iPhone", "iPad", "Galaxy", "MacBook", "Windows", "Linux",
    "Chrome", "Firefox", "Safari", "Edge",
}

KB_ID_NORMALIZE = {
    "itops-general": "itops_general",
    "partner-talk": "partnertalk",
}

# OCR corruption patterns
RE_REPEATED_CHAR = re.compile(r"(.)\1{4,}")  # same char 5+ times
RE_LONE_JAMO = re.compile(r"[\u1100-\u11FF\u3130-\u318F]{3,}")  # 3+ lone jamo


# ============================================================================
# Task runners
# ============================================================================

def _kb_filter(kb_id: str | None) -> str:
    """Return a Cypher WHERE clause fragment for optional KB filtering."""
    if kb_id:
        return f" AND n.kb_id = '{kb_id}'"
    return ""


def _run_query(session, cypher: str, params: dict | None = None) -> list[dict]:
    """Execute a Cypher query and return list of record dicts."""
    result = session.run(cypher, parameters=params or {})
    return [dict(record) for record in result]


def task_remove_placeholder_persons(session, *, apply: bool, kb_id: str | None) -> dict:
    """Remove Person nodes with placeholder names."""
    names_list = list(PLACEHOLDER_NAMES)
    kb_clause = _kb_filter(kb_id)

    count_q = (
        f"MATCH (n:Person) WHERE n.name IN $names{kb_clause} RETURN count(n) AS cnt"
    )
    count = _run_query(session, count_q, {"names": names_list})[0]["cnt"]

    samples_q = (
        f"MATCH (n:Person) WHERE n.name IN $names{kb_clause} "
        "RETURN n.name AS name, n.kb_id AS kb_id LIMIT 5"
    )
    samples = _run_query(session, samples_q, {"names": names_list})

    deleted = 0
    if apply and count > 0:
        delete_q = (
            f"MATCH (n:Person) WHERE n.name IN $names{kb_clause} DETACH DELETE n "
            "RETURN count(*) AS deleted"
        )
        deleted = _run_query(session, delete_q, {"names": names_list})[0]["deleted"]
        logger.info("Deleted %d placeholder Person nodes", deleted)

    return {
        "task": "placeholder_persons",
        "description": "플레이스홀더 Person 노드 제거",
        "found": count,
        "samples": [s["name"] for s in samples],
        "fixed": deleted,
    }


def task_remove_non_person(session, *, apply: bool, kb_id: str | None) -> dict:
    """Remove entities in the non-person blocklist from Person label."""
    names_list = list(NON_PERSON_BLOCKLIST)
    kb_clause = _kb_filter(kb_id)

    count_q = (
        f"MATCH (n:Person) WHERE n.name IN $names{kb_clause} RETURN count(n) AS cnt"
    )
    count = _run_query(session, count_q, {"names": names_list})[0]["cnt"]

    samples_q = (
        f"MATCH (n:Person) WHERE n.name IN $names{kb_clause} "
        "RETURN n.name AS name, labels(n) AS labels LIMIT 5"
    )
    samples = _run_query(session, samples_q, {"names": names_list})

    fixed = 0
    if apply and count > 0:
        # Remove Person label but keep node if it has other labels
        relabel_q = (
            f"MATCH (n:Person) WHERE n.name IN $names{kb_clause} "
            "WITH n, [l IN labels(n) WHERE l <> 'Person' AND l <> '__Entity__'] AS other "
            "FOREACH (_ IN CASE WHEN size(other) > 0 THEN [1] ELSE [] END | REMOVE n:Person) "
            "WITH n, [l IN labels(n) WHERE l <> '__Entity__'] AS remaining "
            "FOREACH (_ IN CASE WHEN size(remaining) = 0 THEN [1] ELSE [] END | DETACH DELETE n) "
            "RETURN count(*) AS fixed"
        )
        fixed = _run_query(session, relabel_q, {"names": names_list})[0]["fixed"]
        logger.info("Fixed %d non-person nodes with Person label", fixed)

    return {
        "task": "non_person_blocklist",
        "description": "Person이 아닌 엔티티에서 Person 라벨 제거",
        "found": count,
        "samples": [f"{s['name']} {s['labels']}" for s in samples],
        "fixed": fixed,
    }


def task_reclassify_store(session, *, apply: bool, kb_id: str | None) -> dict:
    """Reclassify Store nodes: platforms→System, products→remove."""
    kb_clause = _kb_filter(kb_id)

    # Platforms to System
    sys_names = list(STORE_TO_SYSTEM_BLOCKLIST)
    sys_count_q = (
        f"MATCH (n:Store) WHERE n.name IN $names{kb_clause} RETURN count(n) AS cnt"
    )
    sys_count = _run_query(session, sys_count_q, {"names": sys_names})[0]["cnt"]

    # Products to remove
    prod_names = list(STORE_PRODUCT_REMOVE)
    prod_count_q = (
        f"MATCH (n:Store) WHERE n.name IN $names{kb_clause} RETURN count(n) AS cnt"
    )
    prod_count = _run_query(session, prod_count_q, {"names": prod_names})[0]["cnt"]

    total = sys_count + prod_count
    samples_q = (
        f"MATCH (n:Store) WHERE n.name IN $all_names{kb_clause} "
        "RETURN n.name AS name LIMIT 5"
    )
    all_names = sys_names + prod_names
    samples = _run_query(session, samples_q, {"all_names": all_names})

    fixed = 0
    if apply and sys_count > 0:
        reclassify_q = (
            f"MATCH (n:Store) WHERE n.name IN $names{kb_clause} "
            "REMOVE n:Store SET n:System "
            "RETURN count(*) AS fixed"
        )
        fixed += _run_query(session, reclassify_q, {"names": sys_names})[0]["fixed"]
        logger.info("Reclassified %d Store→System", fixed)

    if apply and prod_count > 0:
        remove_q = (
            f"MATCH (n:Store) WHERE n.name IN $names{kb_clause} "
            "DETACH DELETE n RETURN count(*) AS deleted"
        )
        deleted = _run_query(session, remove_q, {"names": prod_names})[0]["deleted"]
        fixed += deleted
        logger.info("Removed %d product nodes from Store", deleted)

    return {
        "task": "store_reclassify",
        "description": "Store 라벨 오분류 수정 (플랫폼→System, 제품→삭제)",
        "found": total,
        "samples": [s["name"] for s in samples],
        "fixed": fixed,
    }


def task_normalize_kb_ids(session, *, apply: bool, kb_id: str | None) -> dict:
    """Normalize inconsistent kb_id values (e.g. itops-general → itops_general)."""
    total_found = 0
    total_fixed = 0
    all_samples: list[str] = []

    for old_id, new_id in KB_ID_NORMALIZE.items():
        if kb_id and kb_id not in (old_id, new_id):
            continue

        count_q = "MATCH (n) WHERE n.kb_id = $old_id RETURN count(n) AS cnt"
        count = _run_query(session, count_q, {"old_id": old_id})[0]["cnt"]
        total_found += count

        if count > 0:
            all_samples.append(f"{old_id}→{new_id} ({count}건)")

        if apply and count > 0:
            fix_q = (
                "MATCH (n) WHERE n.kb_id = $old_id "
                "SET n.kb_id = $new_id "
                "RETURN count(*) AS fixed"
            )
            fixed = _run_query(session, fix_q, {"old_id": old_id, "new_id": new_id})[0]["fixed"]
            total_fixed += fixed
            logger.info("Normalized kb_id %s→%s: %d nodes", old_id, new_id, fixed)

    return {
        "task": "normalize_kb_ids",
        "description": "KB ID 정규화 (하이픈→언더스코어)",
        "found": total_found,
        "samples": all_samples[:5],
        "fixed": total_fixed,
    }


def task_remove_test_nodes(session, *, apply: bool, kb_id: str | None) -> dict:
    """Remove nodes with test kb_ids."""
    if kb_id and not kb_id.startswith("test"):
        return {
            "task": "test_nodes",
            "description": "테스트 노드 제거 (kb_id가 'test'로 시작)",
            "found": 0,
            "samples": [],
            "fixed": 0,
        }

    count_q = "MATCH (n) WHERE n.kb_id STARTS WITH 'test' RETURN count(n) AS cnt"
    count = _run_query(session, count_q)[0]["cnt"]

    samples_q = (
        "MATCH (n) WHERE n.kb_id STARTS WITH 'test' "
        "RETURN DISTINCT n.kb_id AS kb_id LIMIT 5"
    )
    samples = _run_query(session, samples_q)

    deleted = 0
    if apply and count > 0:
        delete_q = (
            "MATCH (n) WHERE n.kb_id STARTS WITH 'test' "
            "DETACH DELETE n RETURN count(*) AS deleted"
        )
        deleted = _run_query(session, delete_q)[0]["deleted"]
        logger.info("Deleted %d test nodes", deleted)

    return {
        "task": "test_nodes",
        "description": "테스트 노드 제거 (kb_id가 'test'로 시작)",
        "found": count,
        "samples": [s["kb_id"] for s in samples],
        "fixed": deleted,
    }


def task_find_ocr_corrupted(session, *, apply: bool, kb_id: str | None) -> dict:
    """Find OCR-corrupted entity names (report only, never auto-fix)."""
    kb_clause = _kb_filter(kb_id)

    # Get all entity names
    names_q = (
        f"MATCH (n) WHERE n.name IS NOT NULL{kb_clause} "
        "RETURN n.name AS name, "
        "[l IN labels(n) WHERE l <> '__Entity__'][0] AS label "
        "LIMIT 10000"
    )
    all_names = _run_query(session, names_q)

    corrupted: list[dict] = []
    for row in all_names:
        name = row["name"]
        if not name or len(name) < 2:
            continue
        # Check repeated chars
        if RE_REPEATED_CHAR.search(name):
            corrupted.append({"name": name, "label": row["label"], "reason": "반복 문자"})
            continue
        # Check lone jamo
        if RE_LONE_JAMO.search(name):
            corrupted.append({"name": name, "label": row["label"], "reason": "낱자음/모음"})
            continue
        # Check high ratio of non-standard unicode categories
        if len(name) >= 3:
            weird = sum(1 for c in name if unicodedata.category(c).startswith(("C", "S")))
            if weird / len(name) > 0.5:
                corrupted.append({"name": name, "label": row["label"], "reason": "비표준 유니코드"})

    return {
        "task": "ocr_corrupted",
        "description": "OCR 손상 의심 엔티티명 (수동 확인 필요)",
        "found": len(corrupted),
        "samples": [f"[{c['label']}] {c['name']} ({c['reason']})" for c in corrupted[:5]],
        "fixed": 0,  # Never auto-fix
    }


# ============================================================================
# Main
# ============================================================================

ALL_TASKS = [
    task_remove_placeholder_persons,
    task_remove_non_person,
    task_reclassify_store,
    task_normalize_kb_ids,
    task_remove_test_nodes,
    task_find_ocr_corrupted,
]


def run_cleanup(*, apply: bool = False, kb_id: str | None = None) -> list[dict]:
    """Run all cleanup tasks and return results."""
    auth = (NEO4J_USER, NEO4J_PASSWORD) if NEO4J_PASSWORD else None
    driver = GraphDatabase.driver(NEO4J_URI, auth=auth)

    results: list[dict] = []
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            for task_fn in ALL_TASKS:
                try:
                    result = task_fn(session, apply=apply, kb_id=kb_id)
                    results.append(result)
                    status = "APPLIED" if apply and result["fixed"] > 0 else "REPORT"
                    logger.info(
                        "[%s] %s: found=%d fixed=%d",
                        status, result["task"], result["found"], result["fixed"],
                    )
                except Exception as e:
                    logger.error("Task %s failed: %s", task_fn.__name__, e)
                    results.append({
                        "task": task_fn.__name__,
                        "description": "오류 발생",
                        "found": 0,
                        "samples": [],
                        "fixed": 0,
                        "error": str(e),
                    })
    finally:
        driver.close()

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Graph quality cleanup")
    parser.add_argument("--apply", action="store_true", help="Apply fixes (default: dry run)")
    parser.add_argument("--kb", type=str, default=None, help="Filter by KB ID")
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY RUN"
    logger.info("=== Graph Cleanup [%s] ===", mode)
    if args.kb:
        logger.info("KB filter: %s", args.kb)

    results = run_cleanup(apply=args.apply, kb_id=args.kb)

    # Summary
    print("\n" + "=" * 60)
    print(f"  Graph Cleanup Summary ({mode})")
    print("=" * 60)
    total_found = 0
    total_fixed = 0
    for r in results:
        found = r["found"]
        fixed = r["fixed"]
        total_found += found
        total_fixed += fixed
        marker = "+" if fixed > 0 else (" " if found == 0 else "!")
        print(f"  [{marker}] {r['description']}: {found}건 발견, {fixed}건 수정")
        for s in r.get("samples", []):
            print(f"      - {s}")
        if r.get("error"):
            print(f"      ERROR: {r['error']}")
    print("-" * 60)
    print(f"  합계: {total_found}건 발견, {total_fixed}건 수정")
    print("=" * 60)

    if total_found > 0 and not args.apply:
        print("\n  실제 수정하려면 --apply 플래그를 추가하세요.")

    sys.exit(0 if total_found == 0 or args.apply else 1)


if __name__ == "__main__":
    main()
