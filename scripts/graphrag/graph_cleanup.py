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
    # Long placeholders from LLM
    "문서 작성자 또는 담당자 (명시되지 않음)",
    "미상 (문서에서 특정 개인 이름 없음)",
    "GS Retail 직원 (명시되지 않음)",
    "GS Retail 직원 (추정)",
    "GS Retail 직원 (추정 불가)",
    "점포명 (구체적인 점포명이 문서에 명시되지 않음)",
    "경영주 (주 손)", "OFC님", "B",
}

NON_PERSON_BLOCKLIST = {
    # Systems / platforms
    "JIRA", "Confluence", "Slack", "Teams", "Outlook", "Grafana",
    "Prometheus", "Jenkins", "ArgoCD", "GitLab", "GitHub",
    "Kubernetes", "Docker", "AWS", "Azure", "GCP",
    "SageMaker", "Qdrant", "Neo4j", "PostgreSQL", "Redis", "Kafka",
    "Elasticsearch", "OpenSearch", "Kibana", "Zabbix", "Datadog",
    # Korean company/service names misclassified as Person
    "피그마", "구글", "네이버", "카카오", "아마존", "마이크로소프트",
    "깃허브", "쿠팡", "배민", "토스", "당근마켓",
    # Geographic names misclassified as Person
    "신월동", "강남", "서울", "부산", "제주", "논산", "수지",
    "강서", "용산", "성남", "수원", "인천",
    # Abstract concepts misclassified as Person
    "리소스", "개인정보", "데이터", "시스템", "서버", "프로젝트",
    "휴가", "요청", "권한", "설정", "보안", "인증",
    "매출", "정산", "비용", "수수료", "계약", "점포",
    "과금", "리턴", "로드", "큐빅", "올해말",
    # Generic role terms (not specific persons)
    "관리자", "운영자", "담당자", "담당 부서",
    "클라이언트", "프로세스", "서비스",
    "모듈", "컴포넌트", "플랫폼", "애플리케이션", "앱",
    "법무법인", "외부인",
}

STORE_TO_SYSTEM_BLOCKLIST = {
    # Platforms / systems wrongly labeled as Store
    "JIRA", "Confluence", "Slack", "Teams", "Jenkins", "ArgoCD",
    "GitLab", "GitHub", "Grafana", "Prometheus", "Kibana",
    "Kubernetes", "Docker", "SageMaker", "Qdrant", "Neo4j",
    "PostgreSQL", "Redis", "Kafka", "Elasticsearch", "OpenSearch",
    "Zabbix", "Datadog", "AWS", "Azure", "GCP",
    # E-commerce platforms misclassified as Store
    "G마켓", "11번가", "쿠팡", "아마존", "이베이", "알리바바",
    "LF MALL", "E스토어-유토피아", "E스토어-이즈마인",
    "(주)성운프라자", "(주)지에스리테일 홈쇼핑",
    "CATV(방송)", "ELEVEN", "7-ELEVEN", "7-11",
    "CVS", "JBP매장",
}

STORE_PRODUCT_REMOVE = {
    # Products wrongly labeled as Store — these should be removed entirely
    "iPhone", "iPad", "Galaxy", "MacBook", "Windows", "Linux",
    "Chrome", "Firefox", "Safari", "Edge",
    # Korean food/product names misclassified as Store
    "숫불김밥110G", "그릴비엔나140G", "후랑크소시지80",
    "원스턴수퍼라이",
}

KB_ID_NORMALIZE = {
    "itops-general": "itops_general",
    "partner-talk": "partnertalk",
    "a-ari": "a_ari",
    "g-espa": "g_espa",
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
    """Remove Person nodes with placeholder names (exact + pattern match)."""
    names_list = list(PLACEHOLDER_NAMES)
    kb_clause = _kb_filter(kb_id)

    # Exact match + pattern match for long placeholders
    count_q = (
        "MATCH (n:Person) WHERE "
        f"(n.name IN $names OR n.name CONTAINS '명시되지' OR n.name CONTAINS '추정' "
        f"OR n.name CONTAINS '확인불가' OR n.name CONTAINS '특정할 수 없' "
        f"OR n.name STARTS WITH '문서 ' OR n.name STARTS WITH 'GS Retail 직원')"
        f"{kb_clause} RETURN count(n) AS cnt"
    )
    count = _run_query(session, count_q, {"names": names_list})[0]["cnt"]

    samples_q = (
        "MATCH (n:Person) WHERE "
        f"(n.name IN $names OR n.name CONTAINS '명시되지' OR n.name CONTAINS '추정' "
        f"OR n.name CONTAINS '확인불가' OR n.name CONTAINS '특정할 수 없' "
        f"OR n.name STARTS WITH '문서 ' OR n.name STARTS WITH 'GS Retail 직원')"
        f"{kb_clause} RETURN n.name AS name, n.kb_id AS kb_id LIMIT 5"
    )
    samples = _run_query(session, samples_q, {"names": names_list})

    deleted = 0
    if apply and count > 0:
        delete_q = (
            "MATCH (n:Person) WHERE "
            f"(n.name IN $names OR n.name CONTAINS '명시되지' OR n.name CONTAINS '추정' "
            f"OR n.name CONTAINS '확인불가' OR n.name CONTAINS '특정할 수 없' "
            f"OR n.name STARTS WITH '문서 ' OR n.name STARTS WITH 'GS Retail 직원')"
            f"{kb_clause} DETACH DELETE n RETURN count(*) AS deleted"
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


def _apply_rule(session, rule: dict) -> int:
    """Apply a single cleanup rule and return the number of nodes fixed."""
    action = rule["action"]
    match_clause = rule["match"]
    label = rule["label"]

    if action == "delete":
        action_q = f"{match_clause} DETACH DELETE n RETURN count(*) AS fixed"
        fixed = _run_query(session, action_q)[0]["fixed"]
        logger.info("Pattern %s: deleted %d", label, fixed)
        return fixed

    if action == "relabel":
        action_q = f"{match_clause} REMOVE n:Person RETURN count(*) AS fixed"
        fixed = _run_query(session, action_q)[0]["fixed"]
        logger.info("Pattern %s: relabeled %d", label, fixed)
        return fixed

    if action == "clean_paren":
        return _apply_clean_paren(session, match_clause, label)

    return 0


def _apply_clean_paren(session, match_clause: str, label: str) -> int:
    """Clean parenthesized org names from Person nodes."""
    action_q = (
        f"{match_clause} "
        "WITH n, apoc.text.regexGroups(n.name, '([가-힣]{2,4})\\\\(') AS groups "
        "WHERE size(groups) > 0 AND size(groups[0]) > 1 "
        "SET n.name = groups[0][1] "
        "RETURN count(*) AS fixed"
    )
    try:
        fixed = _run_query(session, action_q)[0]["fixed"]
        logger.info("Pattern %s: cleaned %d", label, fixed)
        return fixed
    except Exception:
        pass

    # Fallback without APOC: use Python-side regex
    fetch_q = f"{match_clause} RETURN id(n) AS nid, n.name AS name"
    rows = _run_query(session, fetch_q)
    cleaned = 0
    for row in rows:
        m = re.match(r"^([가-힣]{2,4})\s*\(", row["name"])
        if not m:
            continue
        _run_query(
            session,
            "MATCH (n) WHERE id(n) = $nid SET n.name = $new_name",
            {"nid": row["nid"], "new_name": m.group(1)},
        )
        cleaned += 1
    logger.info("Pattern %s: cleaned %d (fallback)", label, cleaned)
    return cleaned


def task_pattern_cleanup_persons(session, *, apply: bool, kb_id: str | None) -> dict:
    """Pattern-based cleanup of Person nodes (short names, roles, brackets, etc.)."""
    kb_clause = _kb_filter(kb_id)
    total_found = 0
    total_fixed = 0
    all_samples: list[str] = []

    sub_rules: list[dict] = [
        {
            "label": "short_name",
            "desc": "이름 2자 이하",
            "match": f"MATCH (n:Person) WHERE size(n.name) <= 2{kb_clause}",
            "action": "delete",
        },
        {
            "label": "role_suffix",
            "desc": "역할/직급 설명 (담당자, 관리자 등)",
            "match": (
                f"MATCH (n:Person) WHERE "
                f"(n.name ENDS WITH '담당자' OR n.name ENDS WITH '관리자' "
                f"OR n.name ENDS WITH '엔지니어' OR n.name ENDS WITH '개발자' "
                f"OR n.name ENDS WITH '운영자' OR n.name ENDS WITH '리더' "
                f"OR n.name ENDS WITH '매니저' OR n.name ENDS WITH '담당'){kb_clause}"
            ),
            "action": "delete",
        },
        {
            "label": "bracket_wrapped",
            "desc": "괄호로 시작하는 이름",
            "match": f"MATCH (n:Person) WHERE n.name STARTS WITH '['{kb_clause}",
            "action": "delete",
        },
        {
            "label": "company_name",
            "desc": "회사명 ((주)/(사) 포함)",
            "match": (
                f"MATCH (n:Person) WHERE "
                f"(n.name CONTAINS '(주)' OR n.name CONTAINS '(사)'){kb_clause}"
            ),
            "action": "relabel",
        },
        {
            "label": "server_infra",
            "desc": "서버/시스템 이름 (서버 또는 시스템으로 끝남)",
            "match": (
                f"MATCH (n:Person) WHERE "
                f"(n.name ENDS WITH '서버' OR n.name ENDS WITH '시스템'){kb_clause}"
            ),
            "action": "relabel",
        },
        {
            "label": "paren_org",
            "desc": "이름(팀명) → 이름만 추출",
            "match": (
                f"MATCH (n:Person) WHERE n.name =~ '.*[가-힣]{{2,4}}\\\\(.+\\\\).*'{kb_clause}"
            ),
            "action": "clean_paren",
        },
        {
            "label": "unknown_placeholder",
            "desc": "미기재/미상/미명시 등 포함",
            "match": (
                f"MATCH (n:Person) WHERE "
                f"(n.name CONTAINS '미기재' OR n.name CONTAINS '미상' "
                f"OR n.name CONTAINS '미명시'){kb_clause}"
            ),
            "action": "delete",
        },
    ]

    for rule in sub_rules:
        count_q = f"{rule['match']} RETURN count(n) AS cnt"
        count = _run_query(session, count_q)[0]["cnt"]
        total_found += count

        samples_q = f"{rule['match']} RETURN n.name AS name LIMIT 3"
        samples = _run_query(session, samples_q)
        for s in samples:
            all_samples.append(f"[{rule['label']}] {s['name']}")

        if apply and count > 0:
            total_fixed += _apply_rule(session, rule)

    return {
        "task": "pattern_cleanup_persons",
        "description": "패턴 기반 Person 노드 정리 (단축명, 역할, 괄호 등)",
        "found": total_found,
        "samples": all_samples[:10],
        "fixed": total_fixed,
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
# Reclassification — comprehensive type correction
# ============================================================================

# Company/brand suffixes that indicate Store, not Person
_COMPANY_SUFFIXES = ("카드", "생명", "보험", "은행", "증권", "캐피탈", "파트너스", "자산운용")
_COMPANY_EXACT_NAMES = {
    "흥국생명", "신한카드", "이니시스", "한화생명", "삼성생명", "교보생명",
    "KB손해보험", "DB손해보험", "메리츠화재", "하나은행", "국민은행",
    "우리은행", "신한은행", "농협은행", "기업은행", "산업은행",
    "한국투자증권", "미래에셋증권", "NH투자증권", "삼성증권",
    "현대캐피탈", "롯데캐피탈", "KB캐피탈",
}

# Tech/tool names that should be System
_SYSTEM_NAMES = {
    "블록체인", "레디스", "카카오톡", "셀러툴", "라인웍스", "피그마",
    "마이쇼핑", "티비허브", "암복화", "랜섬웨어", "팝빌", "인프라",
    "클라우드", "방화벽", "백업", "모니터링", "포스", "포스기", "POS",
    "VPN", "VDI", "ERP", "CRM", "SAP", "MDM", "DLP", "NAC",
    "Active Directory", "AD", "LDAP", "SSO", "MFA", "OTP",
    "네트워크", "와이파이", "WiFi", "LAN", "WAN",
}

# Location pattern: 2-4 Korean chars ending with location suffixes
_LOCATION_SUFFIX_RE = re.compile(r"^[가-힣]{2,4}[동구시도읍면로]$")

# Team/org pattern: ending with team suffixes
_TEAM_SUFFIXES = ("팀", "본부", "실", "부서", "센터", "사업부", "그룹", "파트")

# Placeholder patterns for Team nodes
_TEAM_PLACEHOLDER_PATTERNS = (
    "유관부서", "명시되지", "해당 문서", "작성자 또는", "(명시되지 않음)",
)

# Placeholder patterns for Store nodes (to delete)
_STORE_PLACEHOLDER_NAMES = {
    "점포명 (구체적인 점포명이 문서에 명시되지 않음)",
    "점포명 (구체적인...명시되지 않음)",
}


def _safe_set_label(session, node_eid: str, new_label: str) -> bool:
    """Set label on node, skip if constraint conflict."""
    try:
        session.run(
            f"MATCH (n) WHERE elementId(n) = $eid SET n:{new_label}",
            parameters={"eid": node_eid},
        )
        return True
    except Exception:
        return False


def _relabel_nodes(
    session, rows: list[dict], old_label: str, new_label: str
) -> int:
    """Relabel nodes by elementId, falling back to safe set on conflict."""
    fixed = 0
    for row in rows:
        try:
            session.run(
                f"MATCH (n) WHERE elementId(n) = $eid "
                f"REMOVE n:{old_label} SET n:{new_label}",
                parameters={"eid": row["eid"]},
            )
            fixed += 1
        except Exception:
            if _safe_set_label(session, row["eid"], new_label):
                fixed += 1
    return fixed


def _reclassify_nodes(
    session,
    *,
    apply: bool,
    kb_id: str | None,
    match_cypher: str,
    old_label: str,
    new_label: str | None,
    action: str,  # "relabel" | "delete"
    rule_name: str,
    params: dict | None = None,
) -> dict:
    """Generic reclassification helper. Returns {found, samples, fixed}."""
    kb_clause = _kb_filter(kb_id)
    full_match = f"{match_cypher}{kb_clause}"
    p = params or {}

    count_q = f"{full_match} RETURN count(n) AS cnt"
    count = _run_query(session, count_q, p)[0]["cnt"]

    samples_q = f"{full_match} RETURN n.name AS name LIMIT 5"
    samples = _run_query(session, samples_q, p)

    fixed = 0
    if apply and count > 0:
        if action == "delete":
            action_q = f"{full_match} DETACH DELETE n RETURN count(*) AS fixed"
            fixed = _run_query(session, action_q, p)[0]["fixed"]
            logger.info("[%s] Deleted %d nodes", rule_name, fixed)
        elif action == "relabel" and new_label:
            fetch_q = (
                f"{full_match} RETURN elementId(n) AS eid, n.name AS name"
            )
            rows = _run_query(session, fetch_q, p)
            fixed = _relabel_nodes(session, rows, old_label, new_label)
            logger.info("[%s] Relabeled %d → %s", rule_name, fixed, new_label)

    return {
        "found": count,
        "samples": [s["name"] for s in samples],
        "fixed": fixed,
    }


def task_reclassify_all(session, *, apply: bool, kb_id: str | None) -> dict:
    """Comprehensive reclassification: correct entity type labels instead of deleting."""
    total_found = 0
    total_fixed = 0
    all_samples: list[str] = []

    sub_results: list[tuple[str, dict]] = []

    # ------------------------------------------------------------------
    # A. Person → correct type
    # ------------------------------------------------------------------

    # A1. Person → Store (company/brand suffixes)
    suffix_conditions = " OR ".join(
        f"n.name ENDS WITH '{s}'" for s in _COMPANY_SUFFIXES
    )
    company_names = list(_COMPANY_EXACT_NAMES)
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            f"MATCH (n:Person) WHERE ({suffix_conditions}) OR n.name IN $names"
        ),
        old_label="Person", new_label="Store", action="relabel",
        rule_name="A1_person_to_store",
        params={"names": company_names},
    )
    sub_results.append(("Person→Store (회사/브랜드)", r))

    # A2. Person → System (tech/tools)
    system_names = list(_SYSTEM_NAMES)
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher="MATCH (n:Person) WHERE n.name IN $names",
        old_label="Person", new_label="System", action="relabel",
        rule_name="A2_person_to_system",
        params={"names": system_names},
    )
    sub_results.append(("Person→System (기술/도구)", r))

    # A3. Person → Location (geographic suffix pattern)
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            "MATCH (n:Person) WHERE n.name =~ '^[가-힣]{2,4}[동구시도읍면로]$'"
        ),
        old_label="Person", new_label="Location", action="relabel",
        rule_name="A3_person_to_location",
    )
    sub_results.append(("Person→Location (지역명)", r))

    # A4. Person → Team (team/org suffix)
    team_conditions = " OR ".join(
        f"n.name ENDS WITH '{s}'" for s in _TEAM_SUFFIXES
    )
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=f"MATCH (n:Person) WHERE {team_conditions}",
        old_label="Person", new_label="Team", action="relabel",
        rule_name="A4_person_to_team",
    )
    sub_results.append(("Person→Team (팀/부서)", r))

    # ------------------------------------------------------------------
    # B. Store → cleanup
    # ------------------------------------------------------------------

    # B1. Store placeholder names → delete
    store_ph_names = list(_STORE_PLACEHOLDER_NAMES)
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            "MATCH (n:Store) WHERE n.name IN $names "
            "OR n.name CONTAINS '명시되지 않음'"
        ),
        old_label="Store", new_label=None, action="delete",
        rule_name="B1_store_placeholder",
        params={"names": store_ph_names},
    )
    sub_results.append(("Store 플레이스홀더 삭제", r))

    # ------------------------------------------------------------------
    # C. System → correct type
    # ------------------------------------------------------------------

    # C1. System names that are actually Store (e.g., "CU 포항긱스점")
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            "MATCH (n:System) WHERE n.name =~ '.*[점포]$' "
            "AND NOT n.name ENDS WITH '시스템' AND NOT n.name ENDS WITH '서버'"
        ),
        old_label="System", new_label="Store", action="relabel",
        rule_name="C1_system_to_store",
    )
    sub_results.append(("System→Store (점포명)", r))

    # C2. System names that are actually Document
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            "MATCH (n:System) WHERE n.name CONTAINS 'FAQ' "
            "OR n.name CONTAINS '사례집' OR n.name CONTAINS '매뉴얼' "
            "OR n.name CONTAINS '가이드'"
        ),
        old_label="System", new_label="Document", action="relabel",
        rule_name="C2_system_to_document",
    )
    sub_results.append(("System→Document (문서명)", r))

    # C3. System names that are actually Location (e.g., "경쟁점건물")
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            "MATCH (n:System) WHERE n.name ENDS WITH '건물' "
            "OR n.name ENDS WITH '부지'"
        ),
        old_label="System", new_label="Location", action="relabel",
        rule_name="C3_system_to_location",
    )
    sub_results.append(("System→Location (건물/부지)", r))

    # ------------------------------------------------------------------
    # D. Team → cleanup placeholders
    # ------------------------------------------------------------------
    ph_conditions = " OR ".join(
        f"n.name CONTAINS '{p}'" for p in _TEAM_PLACEHOLDER_PATTERNS
    )
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=f"MATCH (n:Team) WHERE {ph_conditions}",
        old_label="Team", new_label=None, action="delete",
        rule_name="D1_team_placeholder",
    )
    sub_results.append(("Team 플레이스홀더 삭제", r))

    # ------------------------------------------------------------------
    # E. __Entity__ only → reclassify (nodes with no type label)
    # ------------------------------------------------------------------

    # E1. __Entity__ → Store (company suffixes)
    e_suffix_conditions = " OR ".join(
        f"n.name ENDS WITH '{s}'" for s in _COMPANY_SUFFIXES
    )
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            f"MATCH (n:__Entity__) WHERE size([l IN labels(n) "
            f"WHERE l <> '__Entity__']) = 0 AND ({e_suffix_conditions})"
        ),
        old_label="__Entity__", new_label="Store", action="relabel",
        rule_name="E1_entity_to_store",
    )
    sub_results.append(("__Entity__→Store (회사명)", r))

    # E2. __Entity__ → System (tech names)
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            "MATCH (n:__Entity__) WHERE size([l IN labels(n) "
            "WHERE l <> '__Entity__']) = 0 AND n.name IN $names"
        ),
        old_label="__Entity__", new_label="System", action="relabel",
        rule_name="E2_entity_to_system",
        params={"names": system_names},
    )
    sub_results.append(("__Entity__→System (기술명)", r))

    # E3. __Entity__ → Location (geographic pattern)
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            "MATCH (n:__Entity__) WHERE size([l IN labels(n) "
            "WHERE l <> '__Entity__']) = 0 "
            "AND n.name =~ '^[가-힣]{2,4}[동구시도읍면로]$'"
        ),
        old_label="__Entity__", new_label="Location", action="relabel",
        rule_name="E3_entity_to_location",
    )
    sub_results.append(("__Entity__→Location (지역명)", r))

    # E4. __Entity__ → Team (team suffixes)
    e_team_conditions = " OR ".join(
        f"n.name ENDS WITH '{s}'" for s in _TEAM_SUFFIXES
    )
    r = _reclassify_nodes(
        session, apply=apply, kb_id=kb_id,
        match_cypher=(
            f"MATCH (n:__Entity__) WHERE size([l IN labels(n) "
            f"WHERE l <> '__Entity__']) = 0 AND ({e_team_conditions})"
        ),
        old_label="__Entity__", new_label="Team", action="relabel",
        rule_name="E4_entity_to_team",
    )
    sub_results.append(("__Entity__→Team (팀/부서)", r))

    # ------------------------------------------------------------------
    # Aggregate results
    # ------------------------------------------------------------------
    for label, r in sub_results:
        total_found += r["found"]
        total_fixed += r["fixed"]
        if r["found"] > 0:
            for s in r["samples"][:3]:
                all_samples.append(f"[{label}] {s}")

    return {
        "task": "reclassify_all",
        "description": "전체 엔티티 타입 재분류 (삭제 대신 올바른 라벨 부여)",
        "found": total_found,
        "samples": all_samples[:15],
        "fixed": total_fixed,
    }


# ============================================================================
# Main
# ============================================================================

ALL_TASKS = [
    task_remove_placeholder_persons,
    task_remove_non_person,
    task_pattern_cleanup_persons,
    task_reclassify_store,
    task_reclassify_all,
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
        if fixed > 0:
            marker = "+"
        elif found == 0:
            marker = " "
        else:
            marker = "!"
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
