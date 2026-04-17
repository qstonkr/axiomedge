"""Backfill unit tests for src/api/routes/_search_steps.py (coverage lift)."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-import to avoid circular dependency
import src.api.app  # noqa: F401
from src.api.routes import _search_steps as steps
from src.core.models import SearchChunk


def _run(coro):
    return asyncio.run(coro)


# ── Helpers / Fixtures ──────────────────────────────────────────────


def _chunk(
    content: str = "test content",
    score: float = 0.8,
    kb_id: str = "kb-1",
    doc_name: str = "doc.pdf",
    **extra,
) -> dict[str, Any]:
    return {
        "chunk_id": extra.pop("chunk_id", "c1"),
        "content": content,
        "score": score,
        "kb_id": kb_id,
        "document_name": doc_name,
        "source_uri": extra.pop("source_uri", ""),
        "metadata": extra.pop("metadata", {}),
        **extra,
    }


# ── _extract_query_keywords ────────────────────────────────────────


class TestExtractQueryKeywords:
    def test_fallback_when_kiwi_import_fails(self):
        """When KiwiPy is unavailable, falls back to whitespace split."""
        saved = steps._kiwi_instance
        steps._kiwi_instance = None
        try:
            with patch.dict("sys.modules", {"kiwipiepy": None}):
                with patch(
                    "src.api.routes._search_steps.Kiwi",
                    side_effect=ImportError,
                    create=True,
                ):
                    # Force ImportError path
                    steps._kiwi_instance = None
                    result = steps._extract_query_keywords(
                        "서버 관리"
                    )
                    assert isinstance(result, list)
        finally:
            steps._kiwi_instance = saved

    def test_kiwi_tokenize_exception(self):
        """When kiwi.tokenize raises, falls back to split."""
        saved = steps._kiwi_instance
        mock_kiwi = MagicMock()
        mock_kiwi.tokenize.side_effect = RuntimeError("fail")
        steps._kiwi_instance = mock_kiwi
        try:
            result = steps._extract_query_keywords("테스트 질의입니다")
            assert isinstance(result, list)
        finally:
            steps._kiwi_instance = saved


# ── _step_cache_check ──────────────────────────────────────────────


class TestStepCacheCheck:
    def test_returns_none_when_no_caches(self):
        result = _run(steps._step_cache_check(
            "q", {}, ["kb-1"], 5, time.time(),
        ))
        assert result is None

    def test_multi_layer_cache_hit(self):
        mock_cache = AsyncMock()

        @dataclass
        class FakeCacheEntry:
            response: dict = field(default_factory=lambda: {
                "answer": "cached answer",
                "results": [],
                "_cache_version": "test-ver",
                "metadata": {},
                "search_time_ms": 1.0,
            })

        mock_cache.get = AsyncMock(return_value=FakeCacheEntry())
        state = {"multi_layer_cache": mock_cache}

        with patch(
            "src.api.routes._search_steps.weights"
        ) as mock_w:
            mock_w.cache.cache_version = "test-ver"
            with patch(
                "src.api.routes._search_steps._is_valid_cache",
                return_value=True,
            ):
                with patch(
                    "src.api.routes._search_steps._try_deserialize_cache",
                    return_value="CACHED",
                ):
                    result = _run(steps._step_cache_check(
                        "q", state, ["kb-1"], 5, time.time(),
                    ))
                    assert result == "CACHED"

    def test_legacy_cache_hit(self):
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value={
            "answer": "ok",
            "_cache_version": "v1",
            "metadata": {},
        })
        state = {"search_cache": mock_cache}

        with patch(
            "src.api.routes._search_steps.weights"
        ) as mock_w:
            mock_w.cache.cache_version = "v1"
            with patch(
                "src.api.routes._search_steps._is_valid_cache",
                return_value=True,
            ):
                with patch(
                    "src.api.routes._search_steps._try_deserialize_cache",
                    return_value="LEGACY_CACHED",
                ):
                    result = _run(steps._step_cache_check(
                        "q", state, ["kb-1"], 5, time.time(),
                    ))
                    assert result == "LEGACY_CACHED"


# ── _resolve_collections_from_qdrant ───────────────────────────────


class TestResolveCollections:
    def test_no_qdrant_collections_returns_default(self):
        result = _run(steps._resolve_collections_from_qdrant({}))
        assert result == ["knowledge"]

    def test_resolves_from_qdrant(self):
        mock_qc = AsyncMock()
        mock_qc.get_existing_collection_names = AsyncMock(
            return_value=["kb_my_kb", "kb_test_skip"],
        )
        result = _run(steps._resolve_collections_from_qdrant(
            {"qdrant_collections": mock_qc},
        ))
        assert "my-kb" in result
        assert not any("test" in r for r in result)

    def test_exception_returns_default(self):
        mock_qc = AsyncMock()
        mock_qc.get_existing_collection_names = AsyncMock(
            side_effect=RuntimeError("fail"),
        )
        result = _run(steps._resolve_collections_from_qdrant(
            {"qdrant_collections": mock_qc},
        ))
        assert result == ["knowledge"]


# ── _filter_by_kb_registry ─────────────────────────────────────────


class TestFilterByKbRegistry:
    def test_no_registry_returns_as_is(self):
        result = _run(steps._filter_by_kb_registry(
            ["kb-1", "kb-2"], {},
        ))
        assert result == ["kb-1", "kb-2"]

    def test_filters_by_active(self):
        mock_reg = MagicMock()
        mock_fn = AsyncMock(return_value={"kb-1"})
        with patch(
            "src.api.routes.search_helpers.get_active_kb_ids",
            mock_fn,
        ):
            result = _run(steps._filter_by_kb_registry(
                ["kb-1", "kb-2"], {"kb_registry": mock_reg},
            ))
            assert result == ["kb-1"]

    def test_exception_returns_unfiltered(self):
        mock_reg = MagicMock()
        mock_fn = AsyncMock(side_effect=RuntimeError("fail"))
        with patch(
            "src.api.routes.search_helpers.get_active_kb_ids",
            mock_fn,
        ):
            result = _run(steps._filter_by_kb_registry(
                ["kb-1", "kb-2"], {"kb_registry": mock_reg},
            ))
            assert result == ["kb-1", "kb-2"]


# ── _step_resolve_collections ──────────────────────────────────────


class TestStepResolveCollections:
    def test_from_kb_ids(self):
        req = MagicMock()
        req.kb_ids = ["kb-1"]
        req.kb_filter = None
        req.group_id = None
        req.group_name = None

        with patch.object(
            steps, "_filter_by_kb_registry",
            AsyncMock(return_value=["kb-1"]),
        ):
            result = _run(steps._step_resolve_collections(req, {}))
            assert result == ["kb-1"]

    def test_from_group(self):
        req = MagicMock()
        req.kb_ids = []
        req.kb_filter = None
        req.group_id = "g1"
        req.group_name = None

        mock_group = AsyncMock()
        mock_group.resolve_kb_ids = AsyncMock(return_value=["kb-x"])

        with patch.object(
            steps, "_filter_by_kb_registry",
            AsyncMock(return_value=["kb-x"]),
        ):
            result = _run(steps._step_resolve_collections(
                req, {"search_group_repo": mock_group},
            ))
            assert result == ["kb-x"]


# ── _step_preprocess ───────────────────────────────────────────────


class TestStepPreprocess:
    def test_no_preprocessor(self):
        q, info = steps._step_preprocess("test query", {})
        assert q == "test query"
        assert info is None

    def test_with_preprocessor(self):
        @dataclass
        class FakeCorrection:
            original: str = "tset"
            corrected: str = "test"
            reason: str = "typo"

        @dataclass
        class FakePPResult:
            corrected_query: str = "test query"
            original_query: str = "tset query"
            corrections: list = field(
                default_factory=lambda: [FakeCorrection()]
            )

        mock_pp = MagicMock()
        mock_pp.preprocess.return_value = FakePPResult()

        with patch(
            "src.api.routes._search_steps.QueryPreprocessInfo",
            create=True,
        ):
            q, info = steps._step_preprocess(
                "tset query", {"query_preprocessor": mock_pp},
            )
            assert q == "test query"


# ── _step_expand_query ─────────────────────────────────────────────


class TestStepExpandQuery:
    def test_no_expander(self):
        sq, dq, terms = _run(
            steps._step_expand_query("query", ["kb-1"], {}),
        )
        assert sq == "query"
        assert dq == "query"
        assert terms == []

    def test_with_expander(self):
        @dataclass
        class FakeExpansion:
            expanded_terms: list = field(
                default_factory=lambda: ["확장어"]
            )
            expanded_query: str = "query 확장어"

        mock_exp = AsyncMock()
        mock_exp.expand_query = AsyncMock(
            return_value=FakeExpansion()
        )

        sq, dq, terms = _run(steps._step_expand_query(
            "query", ["kb-1"], {"query_expander": mock_exp},
        ))
        assert sq == "query 확장어"
        assert terms == ["확장어"]

    def test_expander_exception(self):
        mock_exp = AsyncMock()
        mock_exp.expand_query = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        sq, dq, terms = _run(steps._step_expand_query(
            "query", ["kb-1"], {"query_expander": mock_exp},
        ))
        assert sq == "query"


# ── _step_classify_query ───────────────────────────────────────────


class TestStepClassifyQuery:
    def test_no_classifier(self):
        top_k, dw, sw = steps._step_classify_query("test", 5, {})
        assert top_k == 5

    def test_owner_query(self):
        class FakeQType(Enum):
            OWNER = "owner_query"

        @dataclass
        class FakeClassification:
            query_type: FakeQType = FakeQType.OWNER

        mock_cls = MagicMock()
        mock_cls.classify.return_value = FakeClassification()

        top_k, dw, sw = steps._step_classify_query(
            "test", 5, {"query_classifier": mock_cls},
        )
        assert top_k >= 10

    def test_concept_query(self):
        class FakeQType(Enum):
            CONCEPT = "concept"

        @dataclass
        class FakeClassification:
            query_type: FakeQType = FakeQType.CONCEPT

        mock_cls = MagicMock()
        mock_cls.classify.return_value = FakeClassification()

        top_k, dw, sw = steps._step_classify_query(
            "test", 5, {"query_classifier": mock_cls},
        )
        assert top_k >= 8

    def test_procedure_query(self):
        class FakeQType(Enum):
            PROCEDURE = "procedure"

        @dataclass
        class FakeClassification:
            query_type: FakeQType = FakeQType.PROCEDURE

        mock_cls = MagicMock()
        mock_cls.classify.return_value = FakeClassification()

        top_k, _, _ = steps._step_classify_query(
            "test", 5, {"query_classifier": mock_cls},
        )
        assert top_k == 5  # procedure doesn't boost top_k

    def test_date_query_override(self):
        """Date pattern in query overrides weights."""
        top_k, dw, sw = steps._step_classify_query(
            "2024년 3월 보고서", 5, {},
        )
        from src.config.weights import weights as w
        assert dw == w.hybrid_search.date_query_dense_weight
        assert sw == w.hybrid_search.date_query_sparse_weight

    def test_week_pattern(self):
        top_k, dw, sw = steps._step_classify_query(
            "3월 2주차 보고서", 5, {},
        )
        from src.config.weights import weights as w
        assert dw == w.hybrid_search.date_query_dense_weight

    def test_classifier_exception(self):
        mock_cls = MagicMock()
        mock_cls.classify.side_effect = RuntimeError("fail")
        top_k, _, _ = steps._step_classify_query(
            "test", 5, {"query_classifier": mock_cls},
        )
        assert top_k == 5


# ── _step_embed ────────────────────────────────────────────────────


class TestStepEmbed:
    def test_no_embedder_raises(self):
        with pytest.raises(Exception):
            _run(steps._step_embed("query", {}))

    def test_embed_basic(self):
        mock_emb = MagicMock()
        mock_emb.encode.return_value = {
            "dense_vecs": [[0.1, 0.2]],
            "lexical_weights": [{"1": 0.5}],
            "colbert_vecs": None,
        }
        dense, sparse, colbert = _run(
            steps._step_embed("query", {"embedder": mock_emb}),
        )
        assert dense == [0.1, 0.2]
        assert sparse == {1: 0.5}
        assert colbert is None


# ── _build_chunks_from_results ─────────────────────────────────────


class TestBuildChunksFromResults:
    def test_basic(self):
        @dataclass
        class FakeResult:
            point_id: str = "p1"
            content: str = "hello"
            score: float = 0.9
            metadata: dict = field(default_factory=lambda: {
                "document_name": "doc.pdf",
            })

        chunks = steps._build_chunks_from_results(
            [FakeResult()], "kb-1", None,
        )
        assert len(chunks) == 1
        assert chunks[0]["kb_id"] == "kb-1"

    def test_document_filter(self):
        @dataclass
        class FakeResult:
            point_id: str = "p1"
            content: str = "hello"
            score: float = 0.9
            metadata: dict = field(default_factory=lambda: {
                "document_name": "report.pdf",
            })

        chunks = steps._build_chunks_from_results(
            [FakeResult()], "kb-1", ["guide"],
        )
        assert len(chunks) == 0

        chunks = steps._build_chunks_from_results(
            [FakeResult()], "kb-1", ["report"],
        )
        assert len(chunks) == 1


# ── _step_composite_rerank ─────────────────────────────────────────


class TestStepCompositeRerank:
    def test_no_reranker(self):
        chunks = [_chunk()]
        result, applied, sc = steps._step_composite_rerank(
            "q", chunks, 5, {},
        )
        assert not applied
        assert result is chunks

    def test_empty_chunks(self):
        result, applied, sc = steps._step_composite_rerank(
            "q", [], 5, {"composite_reranker": MagicMock()},
        )
        assert not applied

    def test_rerank_applied(self):
        reranked = [SearchChunk(
            chunk_id="c1", content="reranked", score=0.95,
            kb_id="kb-1", document_name="doc.pdf",
            metadata={"source_uri": "/doc", "last_modified": "2024-01-01"},
        )]
        mock_reranker = MagicMock()
        mock_reranker.rerank.return_value = reranked

        chunks = [_chunk(_week_matched=True)]
        result, applied, sc = steps._step_composite_rerank(
            "q", chunks, 5, {"composite_reranker": mock_reranker},
        )
        assert applied
        assert result[0]["chunk_id"] == "c1"
        assert result[0]["score"] == 0.95

    def test_rerank_returns_empty(self):
        mock_reranker = MagicMock()
        mock_reranker.rerank.return_value = []

        chunks = [_chunk()]
        result, applied, sc = steps._step_composite_rerank(
            "q", chunks, 5, {"composite_reranker": mock_reranker},
        )
        assert not applied


# ── _step_week_match_guarantee ─────────────────────────────────────


class TestStepWeekMatchGuarantee:
    def test_already_has_week(self):
        chunks = [_chunk(_week_matched=True)]
        result = steps._step_week_match_guarantee(chunks, True, [])
        assert result is chunks

    def test_no_rerank(self):
        result = steps._step_week_match_guarantee(
            [_chunk()], False, [],
        )
        assert len(result) == 1

    def test_pins_week_candidate(self):
        chunks = [_chunk(), _chunk(chunk_id="c2")]
        search_chunks = [
            SearchChunk(
                chunk_id="wk1", content="weekly",
                score=0.7, kb_id="kb-1",
                document_name="4월2주차.pdf",
                metadata={"_week_matched": True},
            ),
        ]
        result = steps._step_week_match_guarantee(
            chunks, True, search_chunks,
        )
        assert result[-1]["chunk_id"] == "wk1"
        assert result[-1]["_week_matched"] is True

    def test_no_week_candidates(self):
        chunks = [_chunk()]
        search_chunks = [
            SearchChunk(
                chunk_id="sc1", content="x",
                score=0.5, kb_id="kb-1",
                document_name="doc.pdf",
                metadata={},
            ),
        ]
        result = steps._step_week_match_guarantee(
            chunks, True, search_chunks,
        )
        assert result is chunks


# ── _step_generate_answer ──────────────────────────────────────────


class TestStepGenerateAnswer:
    def test_incorrect_crag(self):
        from src.search.crag_evaluator import (
            ConfidenceLevel,
            RetrievalAction,
        )

        @dataclass
        class FakeCrag:
            action: RetrievalAction = RetrievalAction.INCORRECT
            recommendation: str = "no answer"
            confidence_level: ConfidenceLevel = ConfidenceLevel.LOW
            confidence_score: float = 0.1

        answer, qt, conf = _run(steps._step_generate_answer(
            "q", [_chunk()], FakeCrag(), True, {},
        ))
        assert answer == "no answer"

    def test_low_confidence_crag(self):
        from src.search.crag_evaluator import (
            ConfidenceLevel,
            RetrievalAction,
        )

        @dataclass
        class FakeCrag:
            action: RetrievalAction = RetrievalAction.AMBIGUOUS
            confidence_score: float = 0.0
            confidence_level: ConfidenceLevel = ConfidenceLevel.LOW
            recommendation: str | None = None

        answer, qt, conf = _run(steps._step_generate_answer(
            "q", [_chunk()], FakeCrag(), True, {},
        ))
        assert "신뢰도가 낮아" in answer

    def test_no_include_answer(self):
        answer, qt, conf = _run(steps._step_generate_answer(
            "q", [_chunk()], None, False, {},
        ))
        assert answer is None

    def test_empty_chunks(self):
        answer, qt, conf = _run(steps._step_generate_answer(
            "q", [], None, True, {},
        ))
        assert answer is None

    def test_fallback_to_answer_service(self):
        @dataclass
        class FakeResult:
            answer: str = "service answer"
            query_type: str = "factual"
            confidence_indicator: str = "높음"

        mock_svc = AsyncMock()
        mock_svc.enrich = AsyncMock(return_value=FakeResult())

        with patch.object(
            steps, "_try_tiered_generation",
            AsyncMock(return_value=None),
        ):
            answer, qt, conf = _run(steps._step_generate_answer(
                "q", [_chunk()], None, True,
                {"answer_service": mock_svc},
            ))
            assert answer == "service answer"

    def test_no_answer_service_returns_none(self):
        with patch.object(
            steps, "_try_tiered_generation",
            AsyncMock(return_value=None),
        ):
            answer, qt, conf = _run(steps._step_generate_answer(
                "q", [_chunk()], None, True, {},
            ))
            assert answer is None


# ── _try_tiered_generation ─────────────────────────────────────────


class TestTryTieredGeneration:
    def test_no_tiered_gen(self):
        result = _run(steps._try_tiered_generation("q", [_chunk()], {}))
        assert result is None

    def test_no_llm(self):
        result = _run(steps._try_tiered_generation(
            "q", [_chunk()], {"tiered_response_generator": MagicMock()},
        ))
        assert result is None

    def test_success_high_confidence(self):
        class FakeQT(Enum):
            FACTUAL = "factual"

        class FakeTieredResult:
            content = "tiered answer"
            confidence = 0.95
            query_type = FakeQT.FACTUAL

        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(
            return_value=FakeTieredResult(),
        )
        mock_cls = MagicMock()

        with patch(
            "src.search.query_classifier.QueryClassifier",
            return_value=mock_cls,
        ):
            with patch(
                "src.search.tiered_response.RAGContext",
            ):
                mock_cls.classify.return_value = MagicMock(
                    query_type=MagicMock(value="factual")
                )
                result = _run(steps._try_tiered_generation(
                    "q", [_chunk()], {
                        "tiered_response_generator": mock_gen,
                        "llm": MagicMock(),
                    },
                ))
                assert result is not None
                assert result[0] == "tiered answer"

    def test_exception_returns_none(self):
        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(
            side_effect=RuntimeError("boom"),
        )

        with patch(
            "src.search.query_classifier.QueryClassifier",
            side_effect=RuntimeError("boom"),
        ):
            result = _run(steps._try_tiered_generation(
                "q", [_chunk()], {
                    "tiered_response_generator": mock_gen,
                    "llm": MagicMock(),
                },
            ))
            assert result is None


# ── _check_kb_pair_conflict ────────────────────────────────────────


class TestCheckKbPairConflict:
    def test_no_conflict_high_overlap(self):
        kb_answers = {
            "kb-a": ["서버 관리 절차 정리"],
            "kb-b": ["서버 관리 절차 문서"],
        }
        result = steps._check_kb_pair_conflict(
            "kb-a", "kb-b", kb_answers, 0.3,
        )
        assert result is None

    def test_conflict_low_overlap(self):
        kb_answers = {
            "kb-a": ["서버 관리 절차"],
            "kb-b": ["네트워크 설정 가이드"],
        }
        result = steps._check_kb_pair_conflict(
            "kb-a", "kb-b", kb_answers, 0.9,
        )
        assert result is not None
        assert result["kb_a"] == "kb-a"

    def test_empty_words(self):
        kb_answers = {"kb-a": [""], "kb-b": ["test"]}
        result = steps._check_kb_pair_conflict(
            "kb-a", "kb-b", kb_answers, 0.5,
        )
        assert result is None


# ── _step_detect_conflicts ─────────────────────────────────────────


class TestStepDetectConflicts:
    def test_single_kb_no_conflict(self):
        assert steps._step_detect_conflicts([_chunk()], ["kb-1"]) == []

    def test_empty_chunks(self):
        assert steps._step_detect_conflicts([], ["kb-1", "kb-2"]) == []

    def test_multi_kb_conflict(self):
        chunks = [
            _chunk(kb_id="kb-a", content="서버 관리"),
            _chunk(kb_id="kb-b", content="완전히 다른 내용 네트워크 설정"),
        ]
        with patch.object(
            steps, "_check_kb_pair_conflict",
            return_value={"kb_a": "kb-a", "kb_b": "kb-b", "warning": "!"},
        ):
            result = steps._step_detect_conflicts(
                chunks, ["kb-a", "kb-b"],
            )
            assert len(result) == 1


# ── _step_follow_ups ──────────────────────────────────────────────


class TestStepFollowUps:
    def test_no_answer(self):
        result = _run(steps._step_follow_ups(
            "q", None, [_chunk()], True, {},
        ))
        assert result == []

    def test_no_include_answer(self):
        result = _run(steps._step_follow_ups(
            "q", "answer", [_chunk()], False, {},
        ))
        assert result == []

    def test_no_llm(self):
        result = _run(steps._step_follow_ups(
            "q", "answer", [_chunk()], True, {},
        ))
        assert result == []

    def test_generates_followups(self):
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(
            return_value="질문1\n질문2\n질문3",
        )
        result = _run(steps._step_follow_ups(
            "q", "answer", [_chunk()], True, {"llm": mock_llm},
        ))
        assert len(result) == 3

    def test_exception_returns_empty(self):
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(
            side_effect=RuntimeError("fail"),
        )
        result = _run(steps._step_follow_ups(
            "q", "answer", [_chunk()], True, {"llm": mock_llm},
        ))
        assert result == []


# ── _step_build_transparency ──────────────────────────────────────


class TestStepBuildTransparency:
    def test_disabled_via_env(self):
        with patch.dict(
            os.environ,
            {"SEARCH_TRANSPARENCY_ENABLED": "false"},
        ):
            result = steps._step_build_transparency(
                "answer", "factual", "높음",
            )
            assert result is None

    def test_no_answer(self):
        result = steps._step_build_transparency(
            None, "factual", "높음",
        )
        assert result is None

    def test_basic(self):
        with patch.dict(
            os.environ,
            {"SEARCH_TRANSPARENCY_ENABLED": "true"},
        ):
            result = steps._step_build_transparency(
                "answer", "factual", "높음",
            )
            assert result is not None
            assert "source_type" in result
            assert result["source_type"] == "document"

    def test_unknown_query_type(self):
        with patch.dict(
            os.environ,
            {"SEARCH_TRANSPARENCY_ENABLED": "true"},
        ):
            result = steps._step_build_transparency(
                "answer", "unknown_type", "보통",
            )
            assert result is not None


# ── _step_cache_store ──────────────────────────────────────────────


class TestStepCacheStore:
    def test_error_pattern_skips_cache(self):
        mock_resp = MagicMock()
        mock_resp.answer = "응답 생성 중 오류가 발생했습니다"
        _run(steps._step_cache_store(
            "q", mock_resp, ["kb-1"], 5, {},
        ))

    def test_multi_layer_cache_store(self):
        mock_resp = MagicMock()
        mock_resp.answer = "good answer"
        mock_resp.model_dump.return_value = {"answer": "good answer"}

        mock_cache = AsyncMock()
        mock_cache.set = AsyncMock()

        _run(steps._step_cache_store(
            "q", mock_resp, ["kb-1"], 5,
            {"multi_layer_cache": mock_cache},
        ))
        mock_cache.set.assert_awaited_once()

    def test_multi_layer_cache_exception(self):
        mock_resp = MagicMock()
        mock_resp.answer = "good answer"
        mock_resp.model_dump.return_value = {"answer": "good answer"}

        mock_cache = AsyncMock()
        mock_cache.set = AsyncMock(
            side_effect=RuntimeError("fail"),
        )

        # Should not raise
        _run(steps._step_cache_store(
            "q", mock_resp, ["kb-1"], 5,
            {"multi_layer_cache": mock_cache},
        ))

    def test_legacy_cache_store(self):
        mock_resp = MagicMock()
        mock_resp.answer = "good answer"
        mock_resp.model_dump.return_value = {"answer": "good answer"}

        mock_cache = AsyncMock()
        mock_cache.set = AsyncMock()

        _run(steps._step_cache_store(
            "q", mock_resp, ["kb-1"], 5,
            {"search_cache": mock_cache},
        ))
        mock_cache.set.assert_awaited_once()

    def test_legacy_cache_exception(self):
        mock_resp = MagicMock()
        mock_resp.answer = "good answer"
        mock_resp.model_dump.return_value = {"answer": "good answer"}

        mock_cache = AsyncMock()
        mock_cache.set = AsyncMock(
            side_effect=RuntimeError("fail"),
        )

        _run(steps._step_cache_store(
            "q", mock_resp, ["kb-1"], 5,
            {"search_cache": mock_cache},
        ))


# ── _parse_datetime_safe ──────────────────────────────────────────


class TestParseDatetimeSafe:
    def test_none(self):
        assert steps._parse_datetime_safe(None) is None

    def test_datetime_with_tz(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = steps._parse_datetime_safe(dt)
        assert result == dt

    def test_datetime_no_tz(self):
        dt = datetime(2024, 1, 1)
        result = steps._parse_datetime_safe(dt)
        assert result.tzinfo == timezone.utc

    def test_iso_string(self):
        result = steps._parse_datetime_safe("2024-01-01T00:00:00Z")
        assert result is not None
        assert result.year == 2024

    def test_iso_string_no_tz(self):
        result = steps._parse_datetime_safe("2024-01-01T00:00:00")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_invalid_string(self):
        assert steps._parse_datetime_safe("not-a-date") is None

    def test_other_type(self):
        assert steps._parse_datetime_safe(12345) is None


# ── _step_apply_trust_and_freshness ───────────────────────────────


class TestStepApplyTrustAndFreshness:
    def test_both_disabled(self):
        with patch.dict(os.environ, {
            "SEARCH_KTS_ENABLED": "false",
            "SEARCH_FRESHNESS_ENABLED": "false",
        }):
            chunks = [_chunk()]
            result = steps._step_apply_trust_and_freshness(
                chunks, {},
            )
            assert result is chunks

    def test_kts_only(self):
        with patch.dict(os.environ, {
            "SEARCH_KTS_ENABLED": "true",
            "SEARCH_FRESHNESS_ENABLED": "false",
        }):
            chunks = [_chunk(metadata={
                "source_type": "confluence_official",
            })]
            result = steps._step_apply_trust_and_freshness(
                chunks, {},
            )
            meta = result[0]["metadata"]
            assert meta["kts_source_credibility"] == 1.0

    def test_freshness_with_predictor(self):
        with patch.dict(os.environ, {
            "SEARCH_KTS_ENABLED": "false",
            "SEARCH_FRESHNESS_ENABLED": "true",
        }):
            mock_pred = MagicMock()
            mock_pred.score.return_value = 0.85
            chunks = [_chunk(metadata={
                "last_modified": "2024-06-01T00:00:00Z",
            })]
            result = steps._step_apply_trust_and_freshness(
                chunks, {"freshness_predictor": mock_pred},
            )
            assert "freshness_score" in result[0]["metadata"]
            assert result[0]["metadata"]["freshness_score"] == 0.85


# ── _step_graph_expand ─────────────────────────────────────────────


class TestStepGraphExpand:
    def test_no_expander(self):
        result = _run(steps._step_graph_expand(
            "q", [_chunk()], ["kb-1"], {}, "http://qdrant",
        ))
        assert len(result) == 1

    def test_empty_chunks(self):
        result = _run(steps._step_graph_expand(
            "q", [], ["kb-1"],
            {"graph_expander": MagicMock()},
            "http://qdrant",
        ))
        assert result == []

    def test_with_expander(self):
        expanded = [_chunk(content="expanded")]
        with patch(
            "src.api.routes.search_helpers.graph_expansion",
            AsyncMock(return_value=expanded),
        ):
            result = _run(steps._step_graph_expand(
                "q", [_chunk()], ["kb-1"],
                {"graph_expander": MagicMock()},
                "http://qdrant",
            ))
            assert result == expanded


# ── _step_crag_evaluate ───────────────────────────────────────────


class TestStepCragEvaluate:
    def test_no_evaluator(self):
        result = _run(steps._step_crag_evaluate(
            "q", [_chunk()], time.time(), {},
        ))
        assert result is None

    def test_empty_chunks(self):
        result = _run(steps._step_crag_evaluate(
            "q", [], time.time(),
            {"crag_evaluator": AsyncMock()},
        ))
        assert result is None

    def test_success(self):
        from src.search.crag_evaluator import (
            ConfidenceLevel,
            RetrievalAction,
        )

        @dataclass
        class FakeEval:
            action: RetrievalAction = RetrievalAction.CORRECT
            confidence_score: float = 0.9
            confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH

        mock_eval = AsyncMock()
        mock_eval.evaluate = AsyncMock(return_value=FakeEval())

        result = _run(steps._step_crag_evaluate(
            "q", [_chunk()], time.time(),
            {"crag_evaluator": mock_eval},
        ))
        assert result is not None
        assert result.action == RetrievalAction.CORRECT

    def test_exception_returns_none(self):
        mock_eval = AsyncMock()
        mock_eval.evaluate = AsyncMock(
            side_effect=RuntimeError("fail"),
        )
        result = _run(steps._step_crag_evaluate(
            "q", [_chunk()], time.time(),
            {"crag_evaluator": mock_eval},
        ))
        assert result is None


# ── _step_log_usage ───────────────────────────────────────────────


class TestStepLogUsage:
    def _make_request(self):
        req = MagicMock()
        req.mode = "hub"
        req.group_name = "test-group"
        return req

    def test_no_repo(self):
        _run(steps._step_log_usage(
            "q", "q", [_chunk()], 100.0, ["kb-1"],
            self._make_request(), "answer", [], False, {},
        ))

    def test_basic_log(self):
        mock_repo = AsyncMock()
        mock_repo.log_search = AsyncMock()

        with patch(
            "src.config.get_settings",
        ) as mock_gs:
            mock_gs.return_value.distill.log_full_context = False
            _run(steps._step_log_usage(
                "q", "q", [_chunk()], 100.0, ["kb-1"],
                self._make_request(), "answer", ["f1"], False,
                {"usage_log_repo": mock_repo},
            ))
            mock_repo.log_search.assert_awaited_once()

    def test_with_crag_and_full_context(self):
        from src.search.crag_evaluator import (
            ConfidenceLevel,
            RetrievalAction,
        )

        @dataclass
        class FakeCrag:
            action: RetrievalAction = RetrievalAction.CORRECT
            confidence_score: float = 0.9
            confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH

        mock_repo = AsyncMock()
        mock_repo.log_search = AsyncMock()

        with patch(
            "src.config.get_settings",
        ) as mock_gs:
            mock_gs.return_value.distill.log_full_context = True
            _run(steps._step_log_usage(
                "q", "q", [_chunk()], 100.0, ["kb-1"],
                self._make_request(), "answer", [], False,
                {"usage_log_repo": mock_repo},
                crag_evaluation=FakeCrag(),
            ))
            mock_repo.log_search.assert_awaited_once()
            ctx = mock_repo.log_search.call_args[1]["context"]
            assert "answer" in ctx
            assert "chunks" in ctx
            assert "crag_action" in ctx

    def test_exception_does_not_raise(self):
        mock_repo = AsyncMock()
        mock_repo.log_search = AsyncMock(
            side_effect=RuntimeError("db error"),
        )

        with patch(
            "src.config.get_settings",
        ) as mock_gs:
            mock_gs.return_value.distill.log_full_context = False
            _run(steps._step_log_usage(
                "q", "q", [_chunk()], 100.0, ["kb-1"],
                self._make_request(), "answer", [], False,
                {"usage_log_repo": mock_repo},
            ))


# ── _is_valid_cache ───────────────────────────────────────────────


class TestIsValidCache:
    def test_wrong_version(self):
        assert not steps._is_valid_cache(
            {"_cache_version": "v1"}, "v2",
        )

    def test_error_answer(self):
        assert not steps._is_valid_cache(
            {
                "_cache_version": "v1",
                "answer": "응답 생성 중 오류가 발생했습니다",
            },
            "v1",
        )

    def test_valid(self):
        assert steps._is_valid_cache(
            {"_cache_version": "v1", "answer": "good answer"},
            "v1",
        )

    def test_no_answer(self):
        assert steps._is_valid_cache(
            {"_cache_version": "v1"}, "v1",
        )


# ── _try_deserialize_cache ────────────────────────────────────────


class TestTryDeserializeCache:
    def test_success(self):
        with patch(
            "src.api.routes.search.HubSearchResponse",
        ) as mock_cls:
            mock_cls.return_value = "deserialized"
            with patch(
                "src.api.routes._search_steps.metrics_inc",
            ):
                result = steps._try_deserialize_cache(
                    {"answer": "ok", "metadata": {}},
                    time.time(),
                    "multi_layer",
                )
                assert result == "deserialized"

    def test_deserialization_failure(self):
        with patch(
            "src.api.routes._search_steps.metrics_inc",
        ):
            result = steps._try_deserialize_cache(
                {"answer": "ok"}, time.time(), "L1",
            )
            # Should return None on failure
            assert result is None


# ── _check_multi_layer_cache ──────────────────────────────────────


class TestCheckMultiLayerCache:
    def test_no_entry(self):
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        result = _run(steps._check_multi_layer_cache(
            mock_cache, "q", ["kb-1"], 5, "v1", time.time(),
        ))
        assert result is None

    def test_entry_no_response(self):
        @dataclass
        class FakeEntry:
            response: dict | None = None

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=FakeEntry())
        result = _run(steps._check_multi_layer_cache(
            mock_cache, "q", ["kb-1"], 5, "v1", time.time(),
        ))
        assert result is None

    def test_exception(self):
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(
            side_effect=RuntimeError("fail"),
        )
        result = _run(steps._check_multi_layer_cache(
            mock_cache, "q", ["kb-1"], 5, "v1", time.time(),
        ))
        assert result is None


# ── _check_legacy_cache ───────────────────────────────────────────


class TestCheckLegacyCache:
    def test_no_cached(self):
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        result = _run(steps._check_legacy_cache(
            mock_cache, "q", ["kb-1"], 5, "v1", time.time(),
        ))
        assert result is None

    def test_exception(self):
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(
            side_effect=RuntimeError("fail"),
        )
        result = _run(steps._check_legacy_cache(
            mock_cache, "q", ["kb-1"], 5, "v1", time.time(),
        ))
        assert result is None

    def test_valid_cached(self):
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value={
            "_cache_version": "v1",
            "answer": "ok",
        })
        with patch.object(
            steps, "_is_valid_cache", return_value=True,
        ):
            with patch.object(
                steps, "_try_deserialize_cache",
                return_value="CACHED",
            ):
                result = _run(steps._check_legacy_cache(
                    mock_cache, "q", ["kb-1"], 5, "v1",
                    time.time(),
                ))
                assert result == "CACHED"


# ── _step_tree_expand ─────────────────────────────────────────────


class TestStepTreeExpand:
    def test_disabled(self):
        with patch(
            "src.config.get_settings",
        ) as mock_gs:
            mock_gs.return_value.tree_index.enabled = False
            result = _run(steps._step_tree_expand(
                "q", [_chunk()], ["kb-1"], {},
            ))
            assert len(result) == 1

    def test_no_graph_repo(self):
        with patch(
            "src.config.get_settings",
        ) as mock_gs:
            mock_gs.return_value.tree_index.enabled = True
            result = _run(steps._step_tree_expand(
                "q", [_chunk()], ["kb-1"], {},
            ))
            assert len(result) == 1

    def test_empty_chunks(self):
        with patch(
            "src.config.get_settings",
        ) as mock_gs:
            mock_gs.return_value.tree_index.enabled = True
            result = _run(steps._step_tree_expand(
                "q", [], ["kb-1"],
                {"graph_repo": MagicMock()},
            ))
            assert result == []

    def test_exception_returns_original(self):
        with patch(
            "src.config.get_settings",
        ) as mock_gs:
            mock_gs.return_value.tree_index.enabled = True
            chunks = [_chunk()]
            with patch(
                "src.search.tree_context_expander.expand_siblings",
                side_effect=RuntimeError("fail"),
            ):
                result = _run(steps._step_tree_expand(
                    "q", chunks, ["kb-1"],
                    {"graph_repo": MagicMock()},
                ))
                assert result == chunks
