"""Final push tests to reach 80% coverage.
Targets: enhanced_similarity_matcher, dedup/result_tracker, ownership repos,
search route internals, auth route internals."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# enhanced_similarity_matcher
# ===========================================================================
class TestStripParticles:
    def test_strip_long_particles(self):
        from src.search.enhanced_similarity_matcher import _strip_particles
        # "에서" only strips if remaining > len+2
        assert _strip_particles("데이터베이스에서") == "데이터베이스"
        assert _strip_particles("담당자까지") == "담당자"

    def test_strip_short_particles(self):
        from src.search.enhanced_similarity_matcher import _strip_particles
        assert _strip_particles("데이터를") == "데이터"
        assert _strip_particles("시스템의") == "시스템"

    def test_strip_no_particle(self):
        from src.search.enhanced_similarity_matcher import _strip_particles
        assert _strip_particles("서버") == "서버"
        assert _strip_particles("AB") == "AB"

    def test_strip_too_short_to_strip(self):
        from src.search.enhanced_similarity_matcher import _strip_particles
        # Won't strip if result would be too short
        assert _strip_particles("서버") == "서버"


class TestMatchDecision:
    def test_creation(self):
        from src.search.enhanced_similarity_matcher import MatchDecision
        d = MatchDecision(zone="AUTO_MATCH", score=0.95, match_type="exact")
        assert d.zone == "AUTO_MATCH"
        assert d.score == 0.95
        assert d.channel_scores == {}

    def test_with_channel_scores(self):
        from src.search.enhanced_similarity_matcher import MatchDecision
        d = MatchDecision(
            zone="REVIEW", score=0.7,
            channel_scores={"rapidfuzz": 0.8, "jaccard": 0.6}
        )
        assert d.channel_scores["rapidfuzz"] == 0.8


class TestEnhancedMatcherInit:
    def test_init_default(self):
        from src.search.enhanced_similarity_matcher import EnhancedSimilarityMatcher
        matcher = EnhancedSimilarityMatcher()
        assert matcher is not None
        assert matcher._precomputed == []
        assert matcher._loaded is False

    def test_decide_zone(self):
        from src.search.enhanced_similarity_matcher import EnhancedSimilarityMatcher
        assert EnhancedSimilarityMatcher._decide_zone(0.95) == "AUTO_MATCH"
        assert EnhancedSimilarityMatcher._decide_zone(0.5) == "REVIEW"
        assert EnhancedSimilarityMatcher._decide_zone(0.1) == "NEW_TERM"

    def test_jaccard_from_sets(self):
        from src.search.enhanced_similarity_matcher import EnhancedSimilarityMatcher
        assert EnhancedSimilarityMatcher._jaccard_from_sets({"a", "b"}, {"b", "c"}) == pytest.approx(1/3)
        assert EnhancedSimilarityMatcher._jaccard_from_sets(set(), set()) == 1.0  # Both empty = identical
        assert EnhancedSimilarityMatcher._jaccard_from_sets({"a"}, {"a"}) == 1.0

    def test_match_enhanced_empty(self):
        from src.search.enhanced_similarity_matcher import EnhancedSimilarityMatcher
        matcher = EnhancedSimilarityMatcher()

        async def _go():
            result = await matcher.match_enhanced("")
            assert result.zone == "NEW_TERM"

        _run(_go())

    def test_match_enhanced_not_loaded(self):
        from src.search.enhanced_similarity_matcher import EnhancedSimilarityMatcher
        matcher = EnhancedSimilarityMatcher()

        async def _go():
            result = await matcher.match_enhanced("test")
            assert result.zone == "NEW_TERM"

        _run(_go())

    def test_set_cross_encoder(self):
        from src.search.enhanced_similarity_matcher import EnhancedSimilarityMatcher
        matcher = EnhancedSimilarityMatcher()
        matcher.set_cross_encoder(MagicMock())
        assert matcher._cross_encoder is not None

    def test_set_embedding_adapter(self):
        from src.search.enhanced_similarity_matcher import EnhancedSimilarityMatcher
        matcher = EnhancedSimilarityMatcher()
        matcher.set_embedding_adapter(MagicMock())
        assert matcher._embedding_adapter is not None

    def test_init_dense_index(self):
        from src.search.enhanced_similarity_matcher import EnhancedSimilarityMatcher
        matcher = EnhancedSimilarityMatcher()
        matcher.init_dense_index(MagicMock())
        assert matcher._dense_index is not None


# ===========================================================================
# DedupResultTracker
# ===========================================================================
class TestDedupResultTracker:
    def test_init_disabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)
        assert tracker.enabled is False

    def test_init_enabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=MagicMock())
        assert tracker.enabled is True

    def test_track_result_disabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)

        async def _go():
            await tracker.track_result(MagicMock(), "kb1")  # Should be no-op

        _run(_go())

    def test_track_result_enabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        redis = AsyncMock()
        redis.xadd = AsyncMock()
        tracker = DedupResultTracker(redis_client=redis)

        result = MagicMock()
        result.doc_id = "doc1"
        result.status = "unique"
        result.duplicate_of = None
        result.similarity_score = 0.1
        result.stage_reached = 1
        result.processing_time_ms = 5.0
        result.resolution = "none"
        result.conflict_types = []

        async def _go():
            await tracker.track_result(result, "kb1", "test.pdf")
            redis.xadd.assert_awaited()

        _run(_go())

    def test_track_result_error(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        redis = AsyncMock()
        redis.xadd = AsyncMock(side_effect=RuntimeError("redis err"))
        tracker = DedupResultTracker(redis_client=redis)

        async def _go():
            # Should not raise (fire-and-forget)
            await tracker.track_result(MagicMock(), "kb1")

        _run(_go())

    def test_enum_val(self):
        from src.pipeline.dedup.result_tracker import _enum_val
        from enum import Enum

        class Status(Enum):
            OK = "ok"
            FAIL = "fail"

        assert _enum_val(Status.OK) == "ok"
        assert _enum_val("plain_string") == "plain_string"


# ===========================================================================
# DocumentOwnerRepository
# ===========================================================================
def _make_session_maker():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    maker = MagicMock()
    maker.return_value = session
    return maker, session


class TestDocumentOwnerRepository:
    def test_save_new(self):
        from src.database.repositories.ownership import DocumentOwnerRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        session.add = MagicMock()
        session.commit = AsyncMock()
        repo = DocumentOwnerRepository(maker)

        async def _go():
            await repo.save({"document_id": "d1", "kb_id": "kb1", "owner_user_id": "u1"})
            session.add.assert_called_once()

        _run(_go())

    def test_save_update(self):
        from src.database.repositories.ownership import DocumentOwnerRepository

        maker, session = _make_session_maker()
        existing = MagicMock()
        existing.document_id = "d1"
        existing.kb_id = "kb1"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()
        repo = DocumentOwnerRepository(maker)

        async def _go():
            await repo.save({"document_id": "d1", "kb_id": "kb1", "owner_user_id": "u2"})
            session.commit.assert_awaited()

        _run(_go())

    def test_get_by_document(self):
        from src.database.repositories.ownership import DocumentOwnerRepository

        maker, session = _make_session_maker()
        model = MagicMock()
        model.id = "o1"
        model.document_id = "d1"
        model.kb_id = "kb1"
        model.owner_user_id = "u1"
        model.owner_name = "User"
        model.owner_email = "u@u.com"
        model.owner_department = "IT"
        model.owner_team = "Dev"
        model.assignment_type = "manual"
        model.confidence_score = 0.9
        model.created_at = datetime.now(timezone.utc)
        model.updated_at = datetime.now(timezone.utc)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = model
        session.execute = AsyncMock(return_value=result_mock)
        repo = DocumentOwnerRepository(maker)

        async def _go():
            result = await repo.get_by_document("d1", "kb1")
            assert result is not None
            assert result["owner_user_id"] == "u1"

        _run(_go())

    def test_get_by_document_not_found(self):
        from src.database.repositories.ownership import DocumentOwnerRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        repo = DocumentOwnerRepository(maker)

        async def _go():
            result = await repo.get_by_document("missing", "kb1")
            assert result is None

        _run(_go())

    def test_get_by_owner(self):
        from src.database.repositories.ownership import DocumentOwnerRepository

        maker, session = _make_session_maker()
        model = MagicMock()
        model.id = "o1"
        model.document_id = "d1"
        model.kb_id = "kb1"
        model.owner_user_id = "u1"
        model.owner_name = "User"
        model.owner_email = "u@u.com"
        model.owner_department = "IT"
        model.owner_team = "Dev"
        model.assignment_type = "manual"
        model.confidence_score = 0.9
        model.created_at = datetime.now(timezone.utc)
        model.updated_at = datetime.now(timezone.utc)

        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [model]
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)
        repo = DocumentOwnerRepository(maker)

        async def _go():
            results = await repo.get_by_owner("u1")
            assert len(results) == 1

        _run(_go())

    def test_get_by_kb(self):
        from src.database.repositories.ownership import DocumentOwnerRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)
        repo = DocumentOwnerRepository(maker)

        async def _go():
            results = await repo.get_by_kb("kb1")
            assert results == []

        _run(_go())


# ===========================================================================
# TopicOwnerRepository
# ===========================================================================
class TestTopicOwnerRepository:
    def test_save(self):
        from src.database.repositories.ownership import TopicOwnerRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        session.add = MagicMock()
        session.commit = AsyncMock()
        repo = TopicOwnerRepository(maker)

        async def _go():
            await repo.save({"topic_name": "IT", "kb_id": "kb1", "sme_user_id": "u1", "topic_keywords": "[]"})
            session.add.assert_called_once()

        _run(_go())

    def test_get_by_kb(self):
        from src.database.repositories.ownership import TopicOwnerRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)
        repo = TopicOwnerRepository(maker)

        async def _go():
            results = await repo.get_by_kb("kb1")
            assert results == []

        _run(_go())


# ===========================================================================
# ErrorReportRepository
# ===========================================================================
class TestErrorReportRepository:
    def test_get_by_id_not_found(self):
        from src.database.repositories.ownership import ErrorReportRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        repo = ErrorReportRepository(maker)

        async def _go():
            result = await repo.get_by_id("err1")
            assert result is None

        _run(_go())

    def test_get_by_document(self):
        from src.database.repositories.ownership import ErrorReportRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)
        repo = ErrorReportRepository(maker)

        async def _go():
            results = await repo.get_by_document("doc1", "kb1")
            assert results == []

        _run(_go())


# ===========================================================================
# ProvenanceRepository additional
# ===========================================================================
class TestProvenanceRepoUpsert:
    def test_upsert_new(self):
        from src.database.repositories.traceability import ProvenanceRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        session.add = MagicMock()
        session.commit = AsyncMock()
        repo = ProvenanceRepository(maker)

        async def _go():
            result = await repo.upsert({
                "knowledge_id": "k1", "kb_id": "kb1", "content_hash": "abc",
            })
            assert result is None  # No previous hash

        _run(_go())

    def test_upsert_existing(self):
        from src.database.repositories.traceability import ProvenanceRepository

        maker, session = _make_session_maker()
        existing = MagicMock()
        existing.content_hash = "old_hash"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()
        repo = ProvenanceRepository(maker)

        async def _go():
            result = await repo.upsert({
                "knowledge_id": "k1", "kb_id": "kb1", "content_hash": "new_hash",
            })
            assert result == "old_hash"

        _run(_go())


# ===========================================================================
# Auth routes: additional endpoints
# ===========================================================================
class TestAuthRoutesAdditional:
    def _make_app(self):
        import src.api.app  # noqa: F401
        from src.api.routes import auth as auth_mod
        app = FastAPI()
        app.include_router(auth_mod.router)
        return app, auth_mod

    def _mock_state(self, **overrides):
        from src.api.state import AppState
        state = AppState()
        for k, v in overrides.items():
            state[k] = v
        return state

    def test_assign_role_no_service(self):
        app, auth_mod = self._make_app()
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=None):

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/auth/users/u1/roles", json={"role_name": "admin"})
                    assert resp.status_code == 503

            _run(_go())

    def test_revoke_role_no_service(self):
        app, auth_mod = self._make_app()
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=None):

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/auth/users/u1/roles/admin")
                    assert resp.status_code == 503

            _run(_go())

    def test_get_user_no_service(self):
        app, auth_mod = self._make_app()
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=None):

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/auth/users/u1")
                    assert resp.status_code == 503

            _run(_go())

    def test_set_kb_permission_no_service(self):
        app, auth_mod = self._make_app()
        state = self._mock_state()
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=None), \
             patch.object(auth_mod, "_get_state", return_value=state):

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/auth/kb/kb1/permissions", json={
                        "user_id": "u1", "permission_level": "contributor"
                    })
                    assert resp.status_code == 503

            _run(_go())

    def test_remove_kb_permission_no_service(self):
        app, auth_mod = self._make_app()
        state = self._mock_state()
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=None), \
             patch.object(auth_mod, "_get_state", return_value=state):

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/auth/kb/kb1/permissions/u1")
                    assert resp.status_code == 503

            _run(_go())

    def test_login_success(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.authenticate = AsyncMock(return_value={"id": "u1", "email": "a@b.com", "display_name": "User"})
        auth_svc.get_user_roles = AsyncMock(return_value=[{"role": "admin"}])

        jwt_svc = MagicMock()
        jwt_pair = MagicMock()
        jwt_pair.access_token = "access_jwt"
        jwt_pair.refresh_token = "refresh_jwt"
        jwt_pair.refresh_expires_at = datetime.now(timezone.utc)
        jwt_svc.create_token_pair = MagicMock(return_value=jwt_pair)
        jwt_svc.access_expire_seconds = 3600
        jwt_svc.refresh_expire_seconds = 86400
        jwt_svc.decode_refresh_token = MagicMock(return_value={"jti": "j1", "family_id": "f1"})

        rbac = MagicMock()
        rbac.get_effective_permissions = MagicMock(return_value=["kb:read"])

        token_store = AsyncMock()
        token_store.store_refresh_token = AsyncMock()

        state = self._mock_state(auth_service=auth_svc, jwt_service=jwt_svc, rbac_engine=rbac, token_store=token_store)
        with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc), \
             patch.object(auth_mod, "_get_state", return_value=state):

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "pass"})
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True
                    assert data["roles"] == ["admin"]

            _run(_go())

    def test_register_success(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.create_user_with_password = AsyncMock(return_value={"id": "u2", "email": "b@b.com"})
        state = self._mock_state(auth_service=auth_svc)
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc), \
             patch.object(auth_mod, "_get_state", return_value=state):

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/auth/register", json={
                        "email": "b@b.com", "password": "password123!", "display_name": "B"
                    })
                    assert resp.status_code == 200

            _run(_go())

    def test_change_password_success(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.change_password = AsyncMock(return_value=True)
        state = self._mock_state(auth_service=auth_svc)
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc), \
             patch.object(auth_mod, "_get_state", return_value=state):

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/auth/change-password", json={
                        "old_password": "oldpassword123", "new_password": "newpassword123"
                    })
                    assert resp.status_code == 200

            _run(_go())
