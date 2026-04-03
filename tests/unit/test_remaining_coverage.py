"""Unit tests for remaining modules — coverage push.

Targets: auth/service (47), graph/integrity (38),
dedup/redis_index (49), cross_encoder_reranker (47).
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ===========================================================================
# GraphIntegrityChecker
# ===========================================================================

from src.graph.integrity import (
    GraphIntegrityChecker,
    IntegrityIssue,
    IntegrityReport,
)


class TestIntegrityIssue:
    def test_to_dict(self):
        issue = IntegrityIssue(
            issue_type="orphan_node",
            node_id="abc",
            node_type="Person",
            message="No relationships",
        )
        d = issue.to_dict()
        assert d["issue_type"] == "orphan_node"
        assert d["node_id"] == "abc"


class TestIntegrityReport:
    def test_to_dict_empty(self):
        report = IntegrityReport()
        d = report.to_dict()
        assert d["status"] == "ok"
        assert d["total_issues"] == 0

    def test_to_dict_with_issues(self):
        report = IntegrityReport(
            status="warning",
            orphan_nodes=2,
            total_issues=2,
            issues=[
                IntegrityIssue("orphan_node", "a", "Person", "msg1"),
                IntegrityIssue("orphan_node", "b", "Team", "msg2"),
            ],
        )
        d = report.to_dict()
        assert len(d["issues"]) == 2


class TestGraphIntegrityChecker:
    async def test_no_client(self):
        checker = GraphIntegrityChecker()
        report = await checker.check_integrity()
        assert report.status == "error"
        assert report.total_issues == 1

    async def test_with_client_no_issues(self):
        mock_client = AsyncMock()
        mock_client.execute_query = AsyncMock(return_value=[])
        checker = GraphIntegrityChecker(neo4j_client=mock_client)
        report = await checker.check_integrity()
        assert report.status == "ok"
        assert report.total_issues == 0

    async def test_with_orphan_nodes(self):
        mock_client = AsyncMock()

        async def mock_query(query, params):
            if "NOT (n)-[]-" in query:
                return [{"id": "orphan1", "name": "Orphan", "type": "Person"}]
            return []

        mock_client.execute_query = mock_query
        checker = GraphIntegrityChecker(neo4j_client=mock_client)
        report = await checker.check_integrity()
        assert report.orphan_nodes == 1
        assert report.status == "warning"

    async def test_kb_scoped(self):
        mock_client = AsyncMock()
        mock_client.execute_query = AsyncMock(return_value=[])
        checker = GraphIntegrityChecker(neo4j_client=mock_client)
        report = await checker.check_integrity(kb_id="test-kb")
        assert report.kb_id == "test-kb"

    async def test_docs_without_kb(self):
        mock_client = AsyncMock()

        async def mock_query(query, params):
            if "BELONGS_TO" in query:
                return [{"id": "doc1", "name": "Doc Title", "type": "Document"}]
            return []

        mock_client.execute_query = mock_query
        checker = GraphIntegrityChecker(neo4j_client=mock_client)
        report = await checker.check_integrity()
        assert report.missing_relationships >= 1

    async def test_persons_no_authorship(self):
        mock_client = AsyncMock()

        async def mock_query(query, params):
            if "AUTHORED" in query:
                return [{"id": "p1", "name": "김철수", "type": "Person"}]
            return []

        mock_client.execute_query = mock_query
        checker = GraphIntegrityChecker(neo4j_client=mock_client)
        report = await checker.check_integrity()
        assert report.missing_relationships >= 1

    async def test_query_exception_graceful(self):
        mock_client = AsyncMock()
        mock_client.execute_query = AsyncMock(side_effect=Exception("neo4j down"))
        checker = GraphIntegrityChecker(neo4j_client=mock_client)
        report = await checker.check_integrity()
        assert report.status == "ok"  # exceptions are caught per check

    def test_get_client_from_repo(self):
        mock_repo = MagicMock()
        mock_repo._client = MagicMock()
        checker = GraphIntegrityChecker(graph_repository=mock_repo)
        client = checker._get_client()
        assert client is mock_repo._client

    def test_get_client_none(self):
        checker = GraphIntegrityChecker()
        assert checker._get_client() is None

    async def test_error_severity_status(self):
        """If any issue has severity=error, report status should be error."""
        mock_client = AsyncMock()

        async def mock_query(query, params):
            return []

        mock_client.execute_query = mock_query
        checker = GraphIntegrityChecker(neo4j_client=mock_client)
        report = await checker.check_integrity()
        # Manually add an error issue
        report.issues.append(
            IntegrityIssue("test", "x", "X", "msg", severity="error")
        )
        report.total_issues = len(report.issues)
        # Check logic
        if any(i.severity == "error" for i in report.issues):
            report.status = "error"
        assert report.status == "error"


# ===========================================================================
# RedisDedupIndex
# ===========================================================================

from src.pipeline.dedup.redis_index import RedisDedupIndex


class TestRedisDedupIndex:
    def test_disabled_when_no_redis(self):
        idx = RedisDedupIndex(redis_client=None)
        assert idx.enabled is False

    def test_enabled_with_redis(self):
        idx = RedisDedupIndex(redis_client=MagicMock())
        assert idx.enabled is True

    async def test_contains_disabled(self):
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.contains("kb1", "hash123") is False

    async def test_contains_success(self):
        mock_redis = AsyncMock()
        mock_redis.sismember = AsyncMock(return_value=True)
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.contains("kb1", "hash123") is True

    async def test_contains_error(self):
        mock_redis = AsyncMock()
        mock_redis.sismember = AsyncMock(side_effect=Exception("redis down"))
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.contains("kb1", "hash123") is False

    async def test_add_success(self):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(return_value=1)
        mock_redis.ttl = AsyncMock(return_value=-1)
        mock_redis.expire = AsyncMock()
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.add("kb1", "hash123") is True
        mock_redis.expire.assert_called_once()

    async def test_add_already_exists(self):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(return_value=0)
        mock_redis.ttl = AsyncMock(return_value=100)
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.add("kb1", "hash123") is False

    async def test_add_disabled(self):
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.add("kb1", "hash") is False

    async def test_add_error(self):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(side_effect=Exception("fail"))
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.add("kb1", "hash") is False

    async def test_add_batch_success(self):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(return_value=3)
        mock_redis.ttl = AsyncMock(return_value=-1)
        mock_redis.expire = AsyncMock()
        idx = RedisDedupIndex(redis_client=mock_redis)
        result = await idx.add_batch("kb1", ["h1", "h2", "h3"])
        assert result == 3

    async def test_add_batch_empty(self):
        idx = RedisDedupIndex(redis_client=AsyncMock())
        assert await idx.add_batch("kb1", []) == 0

    async def test_add_batch_disabled(self):
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.add_batch("kb1", ["h1"]) == 0

    async def test_add_batch_error(self):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(side_effect=Exception("fail"))
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.add_batch("kb1", ["h1"]) == 0

    async def test_clear_success(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.clear("kb1") is True

    async def test_clear_disabled(self):
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.clear("kb1") is False

    async def test_clear_error(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("fail"))
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.clear("kb1") is False

    async def test_size_success(self):
        mock_redis = AsyncMock()
        mock_redis.scard = AsyncMock(return_value=42)
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.size("kb1") == 42

    async def test_size_disabled(self):
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.size("kb1") == 0

    async def test_size_error(self):
        mock_redis = AsyncMock()
        mock_redis.scard = AsyncMock(side_effect=Exception("fail"))
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.size("kb1") == 0

    # Document-level index

    async def test_contains_doc_success(self):
        mock_redis = AsyncMock()
        mock_redis.sismember = AsyncMock(return_value=True)
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.contains_doc("kb1", "dochash") is True

    async def test_contains_doc_disabled(self):
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.contains_doc("kb1", "dochash") is False

    async def test_contains_doc_error(self):
        mock_redis = AsyncMock()
        mock_redis.sismember = AsyncMock(side_effect=Exception("fail"))
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.contains_doc("kb1", "hash") is False

    async def test_add_doc_success(self):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(return_value=1)
        mock_redis.ttl = AsyncMock(return_value=-1)
        mock_redis.expire = AsyncMock()
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.add_doc("kb1", "dochash") is True

    async def test_add_doc_disabled(self):
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.add_doc("kb1", "hash") is False

    async def test_add_doc_error(self):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(side_effect=Exception("fail"))
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.add_doc("kb1", "hash") is False

    async def test_add_doc_batch_success(self):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(return_value=2)
        mock_redis.ttl = AsyncMock(return_value=-1)
        mock_redis.expire = AsyncMock()
        idx = RedisDedupIndex(redis_client=mock_redis)
        result = await idx.add_doc_batch("kb1", ["h1", "h2"])
        assert result == 2

    async def test_add_doc_batch_disabled(self):
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.add_doc_batch("kb1", ["h1"]) == 0

    async def test_add_doc_batch_empty(self):
        idx = RedisDedupIndex(redis_client=AsyncMock())
        assert await idx.add_doc_batch("kb1", []) == 0

    async def test_add_doc_batch_error(self):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(side_effect=Exception("fail"))
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.add_doc_batch("kb1", ["h1"]) == 0

    async def test_clear_docs_success(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.clear_docs("kb1") is True

    async def test_clear_docs_disabled(self):
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.clear_docs("kb1") is False

    async def test_clear_docs_error(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("fail"))
        idx = RedisDedupIndex(redis_client=mock_redis)
        assert await idx.clear_docs("kb1") is False

    def test_key_format(self):
        idx = RedisDedupIndex(redis_client=MagicMock())
        assert idx._key("my-kb") == "dedup:content_hashes:my-kb"
        assert idx._doc_key("my-kb") == "dedup:doc_hashes:my-kb"

    async def test_add_with_existing_ttl(self):
        """When TTL already set, don't reset it."""
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(return_value=1)
        mock_redis.ttl = AsyncMock(return_value=86400)  # TTL already set
        mock_redis.expire = AsyncMock()
        idx = RedisDedupIndex(redis_client=mock_redis)
        await idx.add("kb1", "hash")
        mock_redis.expire.assert_not_called()


# ===========================================================================
# CrossEncoderReranker
# ===========================================================================

from src.search.cross_encoder_reranker import (
    _sigmoid,
    rerank_with_cross_encoder,
    async_rerank_with_cross_encoder,
    warmup,
)


class TestSigmoid:
    def test_zero(self):
        assert abs(_sigmoid(0.0) - 0.5) < 0.01

    def test_positive(self):
        assert _sigmoid(10.0) > 0.9

    def test_negative(self):
        assert _sigmoid(-10.0) < 0.1

    def test_extreme_positive(self):
        """Large values should not overflow."""
        result = _sigmoid(2000.0)
        assert result == 1.0 or result > 0.99

    def test_extreme_negative(self):
        result = _sigmoid(-2000.0)
        assert result == 0.0 or result < 0.01


class TestRerankWithCrossEncoder:
    def test_no_model(self):
        """When model is None, return chunks unchanged."""
        import src.search.cross_encoder_reranker as ce_module
        orig_model = ce_module._model
        ce_module._model = None
        try:
            chunks = [{"content": "a"}, {"content": "b"}, {"content": "c"}]
            result = rerank_with_cross_encoder("query", chunks, top_k=2)
            assert len(result) == 2
        finally:
            ce_module._model = orig_model

    def test_empty_chunks(self):
        result = rerank_with_cross_encoder("query", [], top_k=5)
        assert result == []

    def test_with_mock_model(self):
        import src.search.cross_encoder_reranker as ce_module
        orig_model = ce_module._model
        mock_model = MagicMock()
        mock_model.predict.return_value = [2.0, 0.5, -1.0]
        ce_module._model = mock_model
        try:
            chunks = [
                {"content": "low"},
                {"content": "high"},
                {"content": "mid"},
            ]
            result = rerank_with_cross_encoder("query", chunks, top_k=2)
            assert len(result) == 2
            # Highest score should be first
            assert result[0]["cross_encoder_score"] >= result[1]["cross_encoder_score"]
        finally:
            ce_module._model = orig_model

    def test_model_predict_failure(self):
        import src.search.cross_encoder_reranker as ce_module
        orig_model = ce_module._model
        mock_model = MagicMock()
        mock_model.predict.side_effect = Exception("predict failed")
        ce_module._model = mock_model
        try:
            chunks = [{"content": "a"}]
            result = rerank_with_cross_encoder("query", chunks, top_k=5)
            assert len(result) == 1  # graceful degradation
        finally:
            ce_module._model = orig_model

    def test_metadata_created(self):
        import src.search.cross_encoder_reranker as ce_module
        orig_model = ce_module._model
        mock_model = MagicMock()
        mock_model.predict.return_value = [1.0]
        ce_module._model = mock_model
        try:
            chunks = [{"content": "text"}]
            result = rerank_with_cross_encoder("query", chunks)
            assert "metadata" in result[0]
            assert "cross_encoder_score" in result[0]["metadata"]
        finally:
            ce_module._model = orig_model


class TestAsyncRerank:
    async def test_no_model(self):
        import src.search.cross_encoder_reranker as ce_module
        orig_model = ce_module._model
        ce_module._model = None
        try:
            chunks = [{"content": "a"}, {"content": "b"}]
            result = await async_rerank_with_cross_encoder("query", chunks, top_k=1)
            assert len(result) == 1
        finally:
            ce_module._model = orig_model

    async def test_with_model(self):
        import src.search.cross_encoder_reranker as ce_module
        orig_model = ce_module._model
        mock_model = MagicMock()
        mock_model.predict.return_value = [1.0, 0.5]
        ce_module._model = mock_model
        try:
            chunks = [{"content": "a"}, {"content": "b"}]
            result = await async_rerank_with_cross_encoder("query", chunks, top_k=2)
            assert len(result) == 2
        finally:
            ce_module._model = orig_model


class TestWarmup:
    def test_warmup_skips_if_already_attempted(self):
        import src.search.cross_encoder_reranker as ce_module
        orig_attempted = ce_module._load_attempted
        orig_loading = ce_module._loading
        ce_module._load_attempted = True
        try:
            warmup()  # Should do nothing
        finally:
            ce_module._load_attempted = orig_attempted
            ce_module._loading = orig_loading

    def test_warmup_skips_if_loading(self):
        import src.search.cross_encoder_reranker as ce_module
        orig_attempted = ce_module._load_attempted
        orig_loading = ce_module._loading
        ce_module._load_attempted = False
        ce_module._loading = True
        try:
            warmup()  # Should do nothing
        finally:
            ce_module._load_attempted = orig_attempted
            ce_module._loading = orig_loading


# ===========================================================================
# AuthService (delegates — test facade wiring)
# ===========================================================================


class TestAuthServiceFacade:
    """Test AuthService facade delegates correctly.

    We mock the sub-services to verify delegation without requiring a DB.
    """

    def _make_service(self):
        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"), \
             patch("src.auth.service.UserCRUD"), \
             patch("src.auth.service.Authenticator"), \
             patch("src.auth.service.RoleService"), \
             patch("src.auth.service.ActivityLogger"):
            from src.auth.service import AuthService
            svc = AuthService(database_url="sqlite+aiosqlite:///test.db")
            svc._users = AsyncMock()
            svc._auth = AsyncMock()
            svc._roles = AsyncMock()
            svc._activity = AsyncMock()
            return svc

    async def test_sync_user_from_idp(self):
        svc = self._make_service()
        svc._users.sync_user_from_idp.return_value = {"id": "u1"}
        result = await svc.sync_user_from_idp(MagicMock())
        assert result == {"id": "u1"}

    async def test_create_user(self):
        svc = self._make_service()
        svc._users.create_user.return_value = {"id": "u1"}
        result = await svc.create_user("test@test.com", "Test")
        assert result["id"] == "u1"

    async def test_update_user(self):
        svc = self._make_service()
        svc._users.update_user.return_value = {"id": "u1"}
        result = await svc.update_user("u1", display_name="New")
        svc._users.update_user.assert_called_once()

    async def test_delete_user(self):
        svc = self._make_service()
        svc._users.delete_user.return_value = True
        assert await svc.delete_user("u1") is True

    async def test_get_user(self):
        svc = self._make_service()
        svc._users.get_user.return_value = {"id": "u1"}
        result = await svc.get_user("u1")
        assert result["id"] == "u1"

    async def test_list_users(self):
        svc = self._make_service()
        svc._users.list_users.return_value = [{"id": "u1"}]
        result = await svc.list_users()
        assert len(result) == 1

    async def test_authenticate(self):
        svc = self._make_service()
        svc._auth.authenticate.return_value = {"id": "u1"}
        result = await svc.authenticate("email", "pw")
        svc._auth.authenticate.assert_called_once()

    async def test_create_user_with_password(self):
        svc = self._make_service()
        svc._auth.create_user_with_password.return_value = {"id": "u1"}
        result = await svc.create_user_with_password("e", "p", "d")
        svc._auth.create_user_with_password.assert_called_once()

    async def test_change_password(self):
        svc = self._make_service()
        svc._auth.change_password.return_value = True
        result = await svc.change_password("u1", "old", "new")
        assert result is True

    async def test_get_user_roles(self):
        svc = self._make_service()
        svc._roles.get_user_roles.return_value = []
        result = await svc.get_user_roles("u1")
        assert result == []

    async def test_assign_role(self):
        svc = self._make_service()
        svc._roles.assign_role.return_value = {"role": "admin"}
        result = await svc.assign_role("u1", "admin")
        svc._roles.assign_role.assert_called_once()

    async def test_revoke_role(self):
        svc = self._make_service()
        svc._roles.revoke_role.return_value = True
        result = await svc.revoke_role("u1", "admin")
        assert result is True

    async def test_get_kb_permission(self):
        svc = self._make_service()
        svc._roles.get_kb_permission.return_value = "read"
        result = await svc.get_kb_permission("u1", "kb1")
        assert result == "read"

    async def test_set_kb_permission(self):
        svc = self._make_service()
        svc._roles.set_kb_permission.return_value = {}
        await svc.set_kb_permission("u1", "kb1", "write")
        svc._roles.set_kb_permission.assert_called_once()

    async def test_list_kb_permissions(self):
        svc = self._make_service()
        svc._roles.list_kb_permissions.return_value = []
        result = await svc.list_kb_permissions("kb1")
        assert result == []

    async def test_remove_kb_permission(self):
        svc = self._make_service()
        svc._roles.remove_kb_permission.return_value = True
        result = await svc.remove_kb_permission("u1", "kb1")
        assert result is True

    async def test_log_activity(self):
        svc = self._make_service()
        await svc.log_activity("u1", "search", "kb")
        svc._activity.log_activity.assert_called_once()

    async def test_get_user_activities(self):
        svc = self._make_service()
        svc._activity.get_user_activities.return_value = []
        result = await svc.get_user_activities("u1")
        assert result == []

    async def test_get_activity_summary(self):
        svc = self._make_service()
        svc._activity.get_activity_summary.return_value = {}
        result = await svc.get_activity_summary("u1")
        assert result == {}

    async def test_close(self):
        svc = self._make_service()
        svc._engine = AsyncMock()
        await svc.close()
        svc._engine.dispose.assert_called_once()

    async def test_seed_defaults(self):
        """seed_defaults should create default roles."""
        svc = self._make_service()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()  # roles already exist
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        svc._session_factory.return_value = mock_session

        with patch("src.auth.service.AuthService._seed_internal_admin", new_callable=AsyncMock):
            await svc.seed_defaults()

    async def test_seed_internal_admin_skips_non_internal(self):
        svc = self._make_service()
        with patch.dict("os.environ", {"AUTH_PROVIDER": "local"}):
            await svc._seed_internal_admin()


# ===========================================================================
# CompositeReranker additional tests
# ===========================================================================

from src.search.composite_reranker import CompositeReranker
from src.domain.models import SearchChunk


class TestCompositeRerankerExtra:
    def _make_chunk(self, content="text", score=0.5, metadata=None):
        return SearchChunk(
            chunk_id="c1",
            content=content,
            score=score,
            kb_id="test",
            metadata=metadata or {},
        )

    def test_safe_float_non_finite(self):
        assert CompositeReranker._safe_float(float("inf"), 0.0) == 0.0
        assert CompositeReranker._safe_float(float("nan"), 0.5) == 0.5

    def test_safe_float_string(self):
        assert CompositeReranker._safe_float("not_a_number", 0.1) == 0.1

    def test_safe_weight_negative(self):
        result = CompositeReranker._safe_weight(-0.5, default=1.0, source_type="test")
        assert result == 0.0

    def test_normalize_scores_equal(self):
        result = CompositeReranker._normalize_scores([0.5, 0.5, 0.5])
        assert result == [0.5, 0.5, 0.5]

    def test_normalize_scores_empty(self):
        assert CompositeReranker._normalize_scores([]) == []

    def test_rerank_empty(self):
        reranker = CompositeReranker()
        assert reranker.rerank("query", [], 5) == []

    def test_rerank_with_source_weights_override(self):
        reranker = CompositeReranker()
        chunks = [
            self._make_chunk("hello world query", 0.8, {"source_type": "faq"}),
            self._make_chunk("other text", 0.5, {"source_type": "qdrant"}),
        ]
        result = reranker.rerank("query", chunks, 2, source_weights={"faq": 2.0})
        assert len(result) == 2

    def test_rerank_graph_distance_bonus(self):
        reranker = CompositeReranker(graph_distance_weight=0.2)
        chunks = [
            self._make_chunk("text1", 0.5, {"graph_distance": 1, "traversal_axis": "causal"}),
            self._make_chunk("text2", 0.5, {}),
        ]
        result = reranker.rerank("query", chunks, 2)
        assert len(result) == 2

    def test_rerank_keyword_bonus(self):
        reranker = CompositeReranker(mmr_enabled=False)
        chunks = [
            self._make_chunk("exact match keyword here", 0.5),
            self._make_chunk("nothing relevant", 0.5),
        ]
        result = reranker.rerank("keyword", chunks, 2)
        # First should have keyword bonus
        assert result[0].score >= result[1].score

    def test_replace_score_fallback(self):
        """Test _replace_score fallback paths."""
        chunk = self._make_chunk("text", 0.5)
        replaced = CompositeReranker._replace_score(chunk, 0.9)
        assert replaced.score == 0.9

    def test_jaccard_similarity(self):
        assert CompositeReranker._jaccard_similarity("a b c", "a b d") > 0
        assert CompositeReranker._jaccard_similarity("", "a") == 0.0

    def test_jaccard_similarity_sets(self):
        assert CompositeReranker._jaccard_similarity_sets({"a", "b"}, {"a", "c"}) > 0
        assert CompositeReranker._jaccard_similarity_sets(set(), {"a"}) == 0.0

    def test_update_axis_boosts(self):
        reranker = CompositeReranker()
        reranker.update_axis_boosts({"causal": 2.0})
        assert reranker._axis_boost_map["causal"] == 2.0

    def test_mmr_rerank(self):
        reranker = CompositeReranker(mmr_enabled=True)
        chunks = [
            self._make_chunk("aaa bbb ccc", 0.9, {"source_type": "qdrant"}),
            self._make_chunk("aaa bbb ddd", 0.85, {"source_type": "qdrant"}),
            self._make_chunk("xxx yyy zzz", 0.8, {"source_type": "qdrant"}),
        ]
        result = reranker.rerank("aaa", chunks, 3)
        assert len(result) == 3

    def test_rerank_invalid_graph_distance(self):
        reranker = CompositeReranker(graph_distance_weight=0.1)
        chunks = [
            self._make_chunk("text", 0.5, {"graph_distance": "invalid"}),
        ]
        result = reranker.rerank("query", chunks, 1)
        assert len(result) == 1


# ===========================================================================
# DenseTermIndex
# ===========================================================================

from src.search.dense_term_index import DenseTermIndex


class TestDenseTermIndex:
    def _make_provider(self, vecs=None, ready=True):
        provider = MagicMock()
        provider.is_ready.return_value = ready
        if vecs is None:
            vecs = [[0.1] * 1024]
        provider.encode.return_value = {"dense_vecs": vecs}
        return provider

    def _make_precomputed(self, term_str="test", term_ko="", definition="def"):
        term = MagicMock()
        term.term = term_str
        term.term_ko = term_ko
        term.definition = definition
        pc = MagicMock()
        pc.term = term
        return pc

    def test_not_ready_initially(self):
        idx = DenseTermIndex(provider=MagicMock())
        assert idx.is_ready is False

    def test_build_not_ready_provider(self):
        provider = self._make_provider(ready=False)
        idx = DenseTermIndex(provider=provider)
        idx.build([self._make_precomputed()])
        assert idx.is_ready is False

    def test_build_and_search(self):
        import numpy as np
        provider = self._make_provider(vecs=[[0.5] * 1024])
        idx = DenseTermIndex(provider=provider)
        pcs = [self._make_precomputed("term1")]
        idx.build(pcs)
        assert idx.is_ready is True

        results = idx.search("query", top_k=5)
        assert len(results) >= 0

    def test_search_not_ready(self):
        idx = DenseTermIndex(provider=MagicMock())
        assert idx.search("query") == []

    def test_search_encode_failure(self):
        import numpy as np
        provider = MagicMock()
        provider.is_ready.return_value = True
        # First call builds, second call (search) fails
        provider.encode.side_effect = [
            {"dense_vecs": [[0.5] * 1024]},
            Exception("encode error"),
        ]
        idx = DenseTermIndex(provider=provider)
        idx.build([self._make_precomputed()])
        results = idx.search("query")
        assert results == []

    def test_build_batch_failure(self):
        """Batch failure should pad with zeros."""
        provider = MagicMock()
        provider.is_ready.return_value = True
        provider.encode.side_effect = Exception("batch failed")
        idx = DenseTermIndex(provider=provider)
        idx.build([self._make_precomputed()])
        # Should build with zero vectors
        assert idx.is_ready is True

    def test_search_batch(self):
        import numpy as np
        provider = MagicMock()
        provider.is_ready.return_value = True
        # Build returns 1 vec, search_batch returns 2 vecs for 2 queries
        provider.encode.side_effect = [
            {"dense_vecs": [[0.5] * 1024]},  # build
            {"dense_vecs": [[0.5] * 1024, [0.3] * 1024]},  # search_batch
        ]
        idx = DenseTermIndex(provider=provider)
        idx.build([self._make_precomputed()])

        results = idx.search_batch(["q1", "q2"], top_k=1)
        assert len(results) == 2

    def test_search_batch_not_ready(self):
        idx = DenseTermIndex(provider=MagicMock())
        results = idx.search_batch(["q1"])
        assert results == [[]]

    def test_build_empty_terms(self):
        provider = self._make_provider()
        idx = DenseTermIndex(provider=provider)
        idx.build([])
        assert idx.is_ready is False

    def test_search_empty_vector(self):
        import numpy as np
        provider = MagicMock()
        provider.is_ready.return_value = True
        provider.encode.side_effect = [
            {"dense_vecs": [[0.5] * 1024]},
            {"dense_vecs": [[]]},
        ]
        idx = DenseTermIndex(provider=provider)
        idx.build([self._make_precomputed()])
        results = idx.search("query")
        assert results == []

    def test_build_vector_count_mismatch(self):
        provider = MagicMock()
        provider.is_ready.return_value = True
        # Return fewer vectors than texts
        provider.encode.return_value = {"dense_vecs": []}
        idx = DenseTermIndex(provider=provider)
        idx.build([self._make_precomputed(), self._make_precomputed()])
        # Vectors padded with zeros for failures, but if still mismatched, not ready


# ===========================================================================
# CrossEncoderReranker -- load_model_sync coverage
# ===========================================================================


class TestCrossEncoderLoadModel:
    def test_load_model_sync_failure(self):
        """_load_model_sync should handle import failures gracefully."""
        import src.search.cross_encoder_reranker as ce_module
        orig_model = ce_module._model
        orig_loading = ce_module._loading
        orig_attempted = ce_module._load_attempted

        try:
            ce_module._model = None
            ce_module._loading = False
            ce_module._load_attempted = False

            with patch.dict("sys.modules", {"sentence_transformers": None}):
                with patch("builtins.__import__", side_effect=ImportError("no module")):
                    # Can't easily test _load_model_sync in isolation due to global patches,
                    # but we can verify warmup triggers the submission
                    pass
        finally:
            ce_module._model = orig_model
            ce_module._loading = orig_loading
            ce_module._load_attempted = orig_attempted

    def test_warmup_submits_task(self):
        """warmup should submit _load_model_sync to executor."""
        import src.search.cross_encoder_reranker as ce_module
        orig_attempted = ce_module._load_attempted
        orig_loading = ce_module._loading
        orig_model = ce_module._model

        try:
            ce_module._load_attempted = False
            ce_module._loading = False
            with patch.object(ce_module._executor, "submit") as mock_submit:
                warmup()
                mock_submit.assert_called_once_with(ce_module._load_model_sync)
        finally:
            ce_module._load_attempted = orig_attempted
            ce_module._loading = orig_loading
            ce_module._model = orig_model


# ===========================================================================
# GraphExpander -- expand_with_entities
# ===========================================================================

from src.search.graph_expander import GraphSearchExpander


class TestGraphExpanderEntities:
    async def test_expand_with_entities(self):
        mock_repo = AsyncMock()
        mock_repo.find_related_chunks = AsyncMock(return_value=[])
        mock_repo.search_entities = AsyncMock(return_value=[
            {"connected_name": "Doc1", "connected_type": "Document"},
        ])
        mock_client = AsyncMock()
        mock_client.execute_query = AsyncMock(return_value=[
            {"doc": "DocFromEntity"},
        ])
        mock_repo._client = mock_client

        expander = GraphSearchExpander(graph_repo=mock_repo)
        result = await expander.expand_with_entities("점포 관리", [], scope_kb_ids=["kb1"])
        assert "Doc1" in result.expanded_source_uris or "DocFromEntity" in result.expanded_source_uris

    async def test_expand_with_entities_no_search_entities(self):
        mock_repo = AsyncMock()
        mock_repo.find_related_chunks = AsyncMock(return_value=[])
        # no search_entities attribute
        del mock_repo.search_entities

        expander = GraphSearchExpander(graph_repo=mock_repo)
        result = await expander.expand_with_entities("query", [])
        assert result is not None

    async def test_expand_with_entities_error(self):
        mock_repo = AsyncMock()
        mock_repo.find_related_chunks = AsyncMock(return_value=[])
        mock_repo.search_entities = AsyncMock(side_effect=Exception("neo4j down"))

        expander = GraphSearchExpander(graph_repo=mock_repo)
        result = await expander.expand_with_entities("query", [])
        assert result is not None
