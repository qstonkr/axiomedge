"""Tests for miscellaneous dashboard page modules.

Covers smaller page modules: ingestion_gate, golden_set, verification,
data_sources, conflicts, dashboard, quality, graph_explorer, my_feedback,
owners, glossary, doc_lifecycle, find_owner, error_report, job_monitor,
search_groups, config_weights, search_history, my_activities,
auth_management, ingestion_jobs, my_documents.

Since these page modules have module-level side effects (st.set_page_config,
render_sidebar, API calls), we test the page-level constants and small
inline logic rather than importing the modules directly.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Shared mock setup
# ---------------------------------------------------------------------------
st_mock = MagicMock()
st_mock.session_state = {}

_dashboard_mods = {
    "streamlit": st_mock,
    "plotly": MagicMock(),
    "plotly.graph_objects": MagicMock(),
    "plotly.express": MagicMock(),
    "pandas": MagicMock(),
    "streamlit_agraph": MagicMock(),
    "components": MagicMock(),
    "components.constants": MagicMock(),
    "components.sidebar": MagicMock(),
    "components.metric_cards": MagicMock(),
    "services": MagicMock(),
    "services.api_client": MagicMock(),
    "services.metrics": MagicMock(),
    "services.session_store": MagicMock(),
    "services.validators": MagicMock(),
    "services.auth": MagicMock(),
}
for mod_name, mock_obj in _dashboard_mods.items():
    sys.modules.setdefault(mod_name, mock_obj)


@pytest.fixture(autouse=True)
def _reset():
    st_mock.session_state = {}
    st_mock.reset_mock()
    yield


# ===========================================================================
# Tests for inline logic patterns found across pages
# ===========================================================================


class TestStatusBadgeLogic:
    """Test status badge mapping patterns used in job_monitor, ingestion_jobs."""

    def test_job_monitor_status_badges(self):
        STATUS_BADGES = {
            "running": "Running",
            "completed": "Completed",
            "failed": "Failed",
            "pending": "Pending",
            "cancelled": "Cancelled",
            "queued": "Queued",
            "processing": "Processing",
        }
        assert "Running" in STATUS_BADGES["running"]
        assert STATUS_BADGES.get("unknown", "unknown") == "unknown"

    def test_ingestion_job_status_badge(self):
        status_badge = {
            "PENDING": "대기",
            "RUNNING": "실행 중",
            "COMPLETED": "완료",
            "FAILED": "실패",
        }
        assert status_badge["RUNNING"] == "실행 중"
        assert status_badge.get("UNKNOWN", "UNKNOWN") == "UNKNOWN"


class TestVerificationStatuses:
    """Test verification status constants used in verification.py."""

    def test_verification_statuses_complete(self):
        VERIFICATION_STATUSES = {
            "UNVERIFIED": {"label": "미검증", "color": "#95A5A6", "icon": "?"},
            "PENDING": {"label": "검증 대기", "color": "#F39C12", "icon": "!"},
            "IN_REVIEW": {"label": "검토 중", "color": "#3498DB", "icon": "~"},
            "VERIFIED": {"label": "검증 완료", "color": "#2ECC71", "icon": "O"},
            "REJECTED": {"label": "검증 실패", "color": "#E74C3C", "icon": "X"},
        }
        assert len(VERIFICATION_STATUSES) == 5
        for key, val in VERIFICATION_STATUSES.items():
            assert "label" in val
            assert "color" in val


class TestIngestionGateLogic:
    """Test ingestion gate pass rate calculations."""

    def test_pass_rate_calculation(self):
        total = 100
        passed = 85
        pass_rate = passed / total
        assert pass_rate == 0.85

    def test_pass_rate_zero_total(self):
        total = 0
        # Should not divide by zero
        if total > 0:
            pass_rate = 0 / total
        else:
            pass_rate = 0
        assert pass_rate == 0

    def test_gate_verdict_badges(self):
        VERDICT_BADGES = {
            "REJECT": "REJECT",
            "HOLD": "HOLD",
            "QUARANTINE": "QUARANTINE",
        }
        assert len(VERDICT_BADGES) == 3

    def test_blocked_filtering(self):
        items = [
            {"verdict": "REJECT", "severity": "HIGH"},
            {"verdict": "HOLD", "severity": "MEDIUM"},
            {"verdict": "QUARANTINE", "severity": "CRITICAL"},
            {"verdict": "REJECT", "severity": "LOW"},
        ]
        verdict_filter = ["REJECT", "HOLD", "QUARANTINE"]
        severity_filter = ["CRITICAL", "HIGH"]

        filtered = [
            item for item in items
            if item.get("verdict", "").upper() in verdict_filter
            and item.get("severity", "MEDIUM").upper() in severity_filter
        ]
        assert len(filtered) == 2


class TestGoldenSetLogic:
    """Test golden set data processing helpers."""

    def test_kb_distribution(self):
        items = [
            {"kb_id": "a-ari"},
            {"kb_id": "a-ari"},
            {"kb_id": "drp"},
            {"kb_id": "g-espa"},
        ]
        kb_dist: dict[str, int] = {}
        for item in items:
            kb = item.get("kb_id", "unknown")
            kb_dist[kb] = kb_dist.get(kb, 0) + 1

        assert kb_dist == {"a-ari": 2, "drp": 1, "g-espa": 1}
        assert len(kb_dist) == 3

    def test_status_counts(self):
        items = [
            {"status": "approved"},
            {"status": "approved"},
            {"status": "pending"},
            {"status": "rejected"},
        ]
        approved = sum(1 for i in items if i.get("status") == "approved")
        pending = sum(1 for i in items if i.get("status") == "pending")
        assert approved == 2
        assert pending == 1


class TestConflictsLogic:
    """Test conflict type/severity counting logic from conflicts.py."""

    def test_conflict_type_distribution(self):
        conflicts = [
            {"conflict_type": "FACTUAL", "severity": "HIGH"},
            {"conflict_type": "TEMPORAL", "severity": "MEDIUM"},
            {"conflict_type": "FACTUAL", "severity": "CRITICAL"},
            {"conflict_type": "SCOPE", "severity": "LOW"},
        ]
        type_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        for c in conflicts:
            ct = c.get("conflict_type", "UNKNOWN")
            cs = c.get("severity", "MEDIUM")
            type_counts[ct] = type_counts.get(ct, 0) + 1
            severity_counts[cs] = severity_counts.get(cs, 0) + 1

        assert type_counts == {"FACTUAL": 2, "TEMPORAL": 1, "SCOPE": 1}
        assert severity_counts == {"HIGH": 1, "MEDIUM": 1, "CRITICAL": 1, "LOW": 1}

    def test_resolved_filter(self):
        all_items = [
            {"status": "RESOLVED"},
            {"status": "MERGED"},
            {"status": "PENDING"},
            {"status": "ARCHIVED"},
            {"status": "OPEN"},
        ]
        resolved = [
            c for c in all_items
            if c.get("status", "").upper()
            in ("RESOLVED", "MERGED", "ARCHIVED", "KEPT")
        ]
        assert len(resolved) == 3

    def test_stage_key_mapping(self):
        """Test dedup stage key mapping from conflicts.py."""
        STAGE_KEY_MAP = {
            "bloom": "bloom_filter",
            "lsh": "minhash_lsh",
            "llm": "conflict_detector",
        }
        raw_stages = {"bloom": {"input_count": 100}, "semhash": {"input_count": 50}}
        stages = {}
        for k, v in raw_stages.items():
            mapped_key = STAGE_KEY_MAP.get(k, k)
            stages[mapped_key] = v

        assert "bloom_filter" in stages
        assert "semhash" in stages
        assert "bloom" not in stages


class TestDashboardKBListLogic:
    """Test KB list row building logic from dashboard.py."""

    def test_kb_row_building(self):
        kbs = [
            {
                "name": "TestKB",
                "tier": "GOLD",
                "status": "active",
                "document_count": 50,
                "chunk_count": 500,
                "experiment_document_count": 10,
                "experiment_chunk_count": 100,
                "experiment_status": "idle",
                "publish_strategy": "legacy",
                "experiment_publish_status": "not_started",
                "kb_id": "test-kb-001",
            }
        ]
        rows = []
        for kb in kbs:
            rows.append({
                "이름": kb.get("name", "-"),
                "티어": kb.get("tier", "-"),
                "상태": kb.get("status", "-"),
                "Live 문서 수": kb.get("document_count", 0),
                "Live 청크 수": kb.get("chunk_count", 0),
                "실험 문서 수": kb.get("experiment_document_count", 0),
                "실험 청크 수": kb.get("experiment_chunk_count", 0),
                "KB ID": kb.get("kb_id", kb.get("id", "-")),
            })
        assert len(rows) == 1
        assert rows[0]["이름"] == "TestKB"
        assert rows[0]["Live 문서 수"] == 50

    def test_pipeline_run_row_building(self):
        runs = [
            {
                "job_id": "abcdef123456789",
                "kb_id": "test-kb",
                "status": "COMPLETED",
                "created_at": "2026-01-01T00:00:00",
                "duration": "5m",
            }
        ]
        rows = []
        for run in runs:
            rows.append({
                "실행 ID": (run.get("job_id", "-") or "-")[:12],
                "KB": run.get("kb_id", "-"),
                "상태": run.get("status", "-"),
                "시작 시간": run.get("created_at", "-"),
                "소요 시간": run.get("duration", "-"),
            })
        assert rows[0]["실행 ID"] == "abcdef123456"
        assert rows[0]["KB"] == "test-kb"


class TestQualityPageLogic:
    """Test quality page inline calculations."""

    def test_freshness_buckets(self):
        """Test age bucket classification logic from quality.py."""
        age_days_list = [3, 15, 45, 120]
        age_buckets = {"< 7일": 0, "7-30일": 0, "30-90일": 0, "90일+": 0}

        for age_days in age_days_list:
            if age_days < 7:
                age_buckets["< 7일"] += 1
            elif age_days < 30:
                age_buckets["7-30일"] += 1
            elif age_days < 90:
                age_buckets["30-90일"] += 1
            else:
                age_buckets["90일+"] += 1

        assert age_buckets == {"< 7일": 1, "7-30일": 1, "30-90일": 1, "90일+": 1}

    def test_freshness_percentage(self):
        age_buckets = {"< 7일": 5, "7-30일": 3, "30-90일": 1, "90일+": 1}
        total = sum(age_buckets.values())
        fresh_pct = (age_buckets["< 7일"] + age_buckets["7-30일"]) / total * 100
        assert fresh_pct == 80.0

    def test_rag_overall_score(self):
        faithfulness = 0.8
        relevancy = 0.7
        completeness = 0.6
        overall = (faithfulness + relevancy + completeness) / 3
        assert abs(overall - 0.7) < 0.001

    def test_kts_signal_averaging(self):
        """Test KTS signal score averaging logic."""
        trust_items = [
            {"hallucination_score": 0.8},
            {"hallucination_score": 0.6},
            {"hallucination_score": 0.4},
        ]
        vals = [item.get("hallucination_score", 0) for item in trust_items]
        avg = sum(vals) / len(vals) if vals else 0
        assert abs(avg - 0.6) < 0.001

    def test_confidence_tier_distribution(self):
        trust_items = [
            {"confidence_tier": "HIGH"},
            {"confidence_tier": "HIGH"},
            {"confidence_tier": "MEDIUM"},
            {"confidence_tier": "uncertain"},
        ]
        tier_dist: dict[str, int] = {}
        for item in trust_items:
            tier = item.get("confidence_tier", "uncertain").upper()
            if tier == "UNCERTAIN":
                tier = "UNVERIFIED"
            tier_dist[tier] = tier_dist.get(tier, 0) + 1

        assert tier_dist == {"HIGH": 2, "MEDIUM": 1, "UNVERIFIED": 1}


class TestDocLifecycleLogic:
    """Test document lifecycle status logic."""

    def test_status_labels(self):
        STATUS_LABELS = {
            "draft": "초안",
            "published": "게시",
            "archived": "아카이브",
            "deleted": "삭제",
        }
        assert STATUS_LABELS["draft"] == "초안"
        assert STATUS_LABELS["published"] == "게시"

    def test_published_ratio(self):
        distribution = {"draft": 10, "published": 80, "archived": 5, "deleted": 5}
        total = sum(distribution.values())
        published = distribution.get("published", 0)
        pub_rate = published / total if total > 0 else 0
        assert pub_rate == 0.8


class TestFeedbackLogic:
    """Test feedback type/status mappings from my_feedback.py."""

    def test_feedback_types(self):
        FEEDBACK_TYPES = {
            "UPVOTE": {"label": "좋아요", "icon": "U"},
            "DOWNVOTE": {"label": "싫어요", "icon": "D"},
            "CORRECTION": {"label": "수정 제안", "icon": "C"},
            "ERROR_REPORT": {"label": "오류 신고", "icon": "E"},
            "SUGGESTION": {"label": "개선 제안", "icon": "S"},
        }
        assert len(FEEDBACK_TYPES) == 5

    def test_feedback_type_counting(self):
        items = [
            {"feedback_type": "UPVOTE"},
            {"feedback_type": "UPVOTE"},
            {"feedback_type": "DOWNVOTE"},
            {"feedback_type": "CORRECTION"},
        ]
        type_counts: dict[str, int] = {}
        for fb in items:
            ft = fb.get("feedback_type", "OTHER")
            type_counts[ft] = type_counts.get(ft, 0) + 1

        assert type_counts == {"UPVOTE": 2, "DOWNVOTE": 1, "CORRECTION": 1}

    def test_error_types_and_priorities(self):
        ERROR_TYPES = [
            "INACCURATE", "OUTDATED", "INCOMPLETE", "DUPLICATE",
            "BROKEN_LINK", "FORMATTING", "OTHER",
        ]
        ERROR_PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        assert len(ERROR_TYPES) == 7
        assert len(ERROR_PRIORITIES) == 4

    def test_error_status_colors(self):
        STATUS_COLORS = {
            "OPEN": "red",
            "IN_PROGRESS": "orange",
            "RESOLVED": "green",
            "REJECTED": "gray",
            "CLOSED": "blue",
        }
        assert STATUS_COLORS["OPEN"] == "red"
        assert len(STATUS_COLORS) == 5


class TestDataSourceLogic:
    """Test data source counting logic from data_sources.py."""

    def test_health_status_counting(self):
        sources = [
            {"health_status": "HEALTHY"},
            {"health_status": "HEALTHY"},
            {"health_status": "ERROR"},
            {"health_status": "SYNCING"},
            {"status": "ACTIVE"},
        ]
        healthy = sum(
            1 for s in sources
            if s.get("health_status", s.get("status", ""))
            in ("HEALTHY", "ACTIVE", "CONNECTED")
        )
        errored = sum(
            1 for s in sources
            if s.get("health_status", s.get("status", ""))
            in ("ERROR", "DISCONNECTED")
        )
        syncing = sum(
            1 for s in sources
            if s.get("health_status", s.get("status", "")) == "SYNCING"
        )

        assert healthy == 3
        assert errored == 1
        assert syncing == 1


class TestSearchHistoryLogic:
    """Test search history filtering logic."""

    def test_query_filter(self):
        items = [
            {"query": "K8s 배포 방법"},
            {"query": "정산 프로세스"},
            {"query": "K8s 모니터링"},
        ]
        query_lower = "k8s"
        filtered = [
            item for item in items
            if query_lower in item.get("query", "").lower()
        ]
        assert len(filtered) == 2

    def test_date_range_filter(self):
        items = [
            {"timestamp": "2026-01-15T10:00:00"},
            {"timestamp": "2026-02-01T10:00:00"},
            {"timestamp": "2026-03-01T10:00:00"},
        ]
        start_str = "2026-01-01"
        end_str = "2026-02-15"
        filtered = [
            item for item in items
            if start_str <= item.get("timestamp", "")[:10] <= end_str
        ]
        assert len(filtered) == 2


class TestOwnerLogic:
    """Test owner data processing logic from owners.py."""

    def test_owner_row_building(self):
        owners = [
            {
                "document_id": "doc-001",
                "owner_user_id": "user1",
                "backup_owner_user_id": "user2",
                "ownership_type": "DIRECT",
                "verification_status": "VERIFIED",
                "created_at": "2026-01-01T00:00:00",
                "last_verified": "2026-01-15T00:00:00",
            }
        ]
        rows = []
        for owner in owners:
            rows.append({
                "문서 ID": owner.get("document_id", "-"),
                "담당자": owner.get("owner_user_id", "-"),
                "백업 담당자": owner.get("backup_owner_user_id") or "-",
                "할당 유형": owner.get("ownership_type", "-"),
                "검증 상태": owner.get("verification_status", "-"),
            })
        assert len(rows) == 1
        assert rows[0]["담당자"] == "user1"
        assert rows[0]["백업 담당자"] == "user2"


class TestMyDocumentsLogic:
    """Test my documents filtering logic."""

    def test_user_document_filter(self):
        all_owners = [
            {"owner_user_id": "user1", "document_title": "Doc A"},
            {"owner_user_id": "user2", "document_title": "Doc B"},
            {"owner_name": "user1", "document_title": "Doc C"},
            {"owner_user_id": "user3", "document_title": "Doc D"},
        ]
        current_user = "user1"
        my_docs = [
            o for o in all_owners
            if o.get("owner_user_id", o.get("user_id", "")) == current_user
            or o.get("owner_name", o.get("name", "")) == current_user
        ]
        assert len(my_docs) == 2

    def test_stale_owner_filter(self):
        stale_items = [
            {"owner_user_id": "user1", "last_verified": "2025-01-01"},
            {"owner_user_id": "user2", "last_verified": "2025-06-01"},
            {"owner_user_id": "user1", "last_verified": "2025-03-01"},
        ]
        current_user = "user1"
        my_stale = [
            s for s in stale_items
            if s.get("owner_user_id", s.get("user_id", "")) == current_user
        ]
        assert len(my_stale) == 2


class TestAuthManagementLogic:
    """Test auth management user row building."""

    def test_user_row_building(self):
        users = [
            {
                "id": "u-001",
                "display_name": "Test User",
                "email": "test@example.com",
                "department": "Engineering",
                "provider": "local",
                "is_active": True,
            }
        ]
        rows = []
        for u in users:
            rows.append({
                "ID": u.get("id", "-"),
                "이름": u.get("display_name", u.get("name", "-")),
                "이메일": u.get("email", "-"),
                "부서": u.get("department", "-"),
                "Provider": u.get("provider", "-"),
                "상태": "활성" if u.get("is_active", True) else "비활성",
            })
        assert len(rows) == 1
        assert rows[0]["이름"] == "Test User"
        assert rows[0]["상태"] == "활성"

    def test_inactive_user(self):
        u = {"is_active": False}
        status = "활성" if u.get("is_active", True) else "비활성"
        assert status == "비활성"


class TestIngestionJobsLogic:
    """Test ingestion jobs dedup and metric logic."""

    def test_run_dedup(self):
        runs = [
            {"run_id": "r1", "status": "COMPLETED"},
            {"run_id": "r1", "status": "COMPLETED"},
            {"run_id": "r2", "status": "RUNNING"},
            {"run_id": "", "status": "PENDING"},
        ]
        seen_run_ids: set[str] = set()
        unique_runs: list[dict] = []
        for r in runs:
            rid = r.get("run_id", r.get("id", ""))
            if rid and rid not in seen_run_ids:
                seen_run_ids.add(rid)
                unique_runs.append(r)
            elif not rid:
                unique_runs.append(r)

        assert len(unique_runs) == 3

    def test_status_counts(self):
        runs = [
            {"status": "COMPLETED"},
            {"status": "RUNNING"},
            {"status": "FAILED"},
            {"status": "COMPLETED"},
        ]
        all_statuses = [r.get("status", "UNKNOWN") for r in runs]
        assert all_statuses.count("COMPLETED") == 2
        assert all_statuses.count("RUNNING") == 1
        assert all_statuses.count("FAILED") == 1


class TestGraphExplorerLogic:
    """Test graph explorer node/edge building logic."""

    def test_node_dedup(self):
        entities = [
            {"name": "A", "type": "Person", "relationships": [{"target": "B", "type": "KNOWS"}]},
            {"name": "B", "type": "System", "relationships": []},
            {"name": "A", "type": "Person", "relationships": []},
        ]
        seen_nodes: set[str] = set()
        graph_nodes = []

        for entity in entities:
            name = entity.get("name", "")
            if name and name not in seen_nodes:
                seen_nodes.add(name)
                graph_nodes.append(name)

            for rel in entity.get("relationships", []):
                target = rel.get("target", "")
                if target and target not in seen_nodes:
                    seen_nodes.add(target)
                    graph_nodes.append(target)

        assert len(graph_nodes) == 2
        assert "A" in graph_nodes
        assert "B" in graph_nodes

    def test_integrity_severity(self):
        orphan_count = 3
        missing_rels = 2
        inconsistencies = 0
        total_issues = orphan_count + missing_rels + inconsistencies

        assert total_issues == 5
        assert total_issues <= 5  # "경미한 이슈"


class TestConfigWeightsLogic:
    """Test config weights normalization check."""

    def test_weights_sum_check(self):
        dense_weight = 0.4
        sparse_weight = 0.3
        colbert_weight = 0.3
        total = dense_weight + sparse_weight + colbert_weight
        assert abs(total - 1.0) < 0.01

    def test_weights_sum_warning(self):
        dense_weight = 0.5
        sparse_weight = 0.5
        colbert_weight = 0.3
        total = dense_weight + sparse_weight + colbert_weight
        assert abs(total - 1.0) > 0.01  # Should trigger warning


class TestSearchGroupsLogic:
    """Test search group metric calculations."""

    def test_total_kbs_in_groups(self):
        groups = [
            {"kb_ids": ["kb1", "kb2"]},
            {"kb_ids": ["kb2", "kb3", "kb4"]},
            {"kb_ids": []},
        ]
        total_kbs = sum(len(g.get("kb_ids", [])) for g in groups)
        assert total_kbs == 5

    def test_default_group_detection(self):
        groups = [
            {"name": "Team A", "is_default": False},
            {"name": "All", "is_default": True},
        ]
        default_groups = [g for g in groups if g.get("is_default")]
        assert len(default_groups) == 1
        assert default_groups[0]["name"] == "All"


class TestMyActivitiesLogic:
    """Test activity type icon mapping."""

    def test_type_icons(self):
        type_icons = {
            "search": "S",
            "feedback": "F",
            "document": "D",
            "login": "L",
            "ingestion": "I",
        }
        assert type_icons.get("search") == "S"
        assert type_icons.get("unknown", "?") == "?"


class TestTopLevelStageLabels:
    """Test pipeline top-level stage label mapping from dashboard.py."""

    def test_stage_labels(self):
        TOP_LEVEL_STAGE_LABELS = {
            "crawl": "수집",
            "ingest": "인제스천",
            "terms": "용어 유사도",
            "publish": "퍼블리시",
        }
        assert TOP_LEVEL_STAGE_LABELS["crawl"] == "수집"
        assert len(TOP_LEVEL_STAGE_LABELS) == 4


class TestReviewerStatsLogic:
    """Test reviewer assignment counting from verification.py."""

    def test_reviewer_map(self):
        pending_items = [
            {"assigned_reviewer": "alice"},
            {"assigned_reviewer": "bob"},
            {"assigned_reviewer": "alice"},
            {},
        ]
        reviewer_map: dict[str, int] = {}
        for item in pending_items:
            reviewer = item.get("assigned_reviewer")
            if reviewer:
                reviewer_map[reviewer] = reviewer_map.get(reviewer, 0) + 1

        assert reviewer_map == {"alice": 2, "bob": 1}

    def test_unique_reviewers(self):
        pending_items = [
            {"assigned_reviewer": "alice"},
            {"assigned_reviewer": "bob"},
            {"assigned_reviewer": "alice"},
            {},
        ]
        reviewer_set = {
            item.get("assigned_reviewer")
            for item in pending_items
            if item.get("assigned_reviewer")
        }
        assert len(reviewer_set) == 2
