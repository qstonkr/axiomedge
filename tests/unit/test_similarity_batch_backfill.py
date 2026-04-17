"""Coverage backfill — BatchMatchMixin (_batch.py).

Tests batch matching, dense index building, cross-encoder setup,
RapidFuzz candidates, mini dense index, and Jaccard utility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.search.similarity._batch import BatchMatchMixin
from src.search.similarity.strategies import (
    EnhancedMatcherConfig,
    MatchDecision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeTerm:
    term: str = "test"
    term_ko: str | None = None


def _make_mixin(**overrides: Any) -> BatchMatchMixin:
    """Create a BatchMatchMixin with required attributes from the parent class."""
    m = BatchMatchMixin()
    defaults: dict[str, Any] = {
        "_config": EnhancedMatcherConfig(),
        "_rf_choices": [],
        "_rf_idx_map": [],
        "_precomputed": [],
        "_dense_index": None,
        "_embedding_adapter": None,
        "_cross_encoder": None,
        "_force_disable_ce": False,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------------------
# _jaccard_from_sets (static)
# ---------------------------------------------------------------------------

class TestJaccardFromSets:
    def test_both_empty(self) -> None:
        assert BatchMatchMixin._jaccard_from_sets(set(), set()) == 1.0

    def test_one_empty(self) -> None:
        assert BatchMatchMixin._jaccard_from_sets({"a"}, set()) == 0.0
        assert BatchMatchMixin._jaccard_from_sets(set(), {"b"}) == 0.0

    def test_identical(self) -> None:
        s = {"a", "b", "c"}
        assert BatchMatchMixin._jaccard_from_sets(s, s) == 1.0

    def test_partial_overlap(self) -> None:
        a = {"a", "b"}
        b = {"b", "c"}
        # intersection=1, union=3
        assert abs(BatchMatchMixin._jaccard_from_sets(a, b) - 1 / 3) < 1e-9

    def test_disjoint(self) -> None:
        assert BatchMatchMixin._jaccard_from_sets({"a"}, {"b"}) == 0.0


# ---------------------------------------------------------------------------
# set_cross_encoder / set_embedding_adapter / init_dense_index
# ---------------------------------------------------------------------------

class TestSetters:
    def test_set_cross_encoder(self) -> None:
        m = _make_mixin()
        ce = MagicMock()
        m.set_cross_encoder(ce)
        assert m._cross_encoder is ce

    def test_set_embedding_adapter(self) -> None:
        m = _make_mixin()
        adapter = MagicMock()
        m.set_embedding_adapter(adapter)
        assert m._embedding_adapter is adapter

    def test_init_dense_index_disabled(self) -> None:
        config = EnhancedMatcherConfig(enable_dense_search=False)
        m = _make_mixin(_config=config)
        m.init_dense_index(MagicMock())
        assert m._dense_index is None

    @patch("src.search.similarity._batch.DenseTermIndex", create=True)
    def test_init_dense_index_success(self, mock_cls: MagicMock) -> None:
        config = EnhancedMatcherConfig(enable_dense_search=True)
        pc = [MagicMock()]
        m = _make_mixin(_config=config, _precomputed=pc)

        mock_idx = MagicMock()
        mock_cls.return_value = mock_idx

        with patch(
            "src.search.dense_term_index.DenseTermIndex", mock_cls,
        ):
            m.init_dense_index(MagicMock(name="provider"))

        mock_idx.build.assert_called_once_with(pc)

    def test_init_dense_index_exception_sets_none(self) -> None:
        config = EnhancedMatcherConfig(enable_dense_search=True)
        m = _make_mixin(_config=config, _precomputed=[])

        mock_idx = MagicMock()
        mock_idx.build.side_effect = RuntimeError("boom")
        with patch(
            "src.search.dense_term_index.DenseTermIndex",
            return_value=mock_idx,
        ):
            m.init_dense_index(MagicMock())

        assert m._dense_index is None


# ---------------------------------------------------------------------------
# _rapidfuzz_candidates
# ---------------------------------------------------------------------------

class TestRapidfuzzCandidates:
    def test_disabled_returns_empty(self) -> None:
        config = EnhancedMatcherConfig(enable_rapidfuzz=False)
        m = _make_mixin(_config=config)
        assert m._rapidfuzz_candidates("test", MagicMock(), MagicMock()) == []

    def test_no_choices_returns_empty(self) -> None:
        config = EnhancedMatcherConfig(enable_rapidfuzz=True)
        m = _make_mixin(_config=config, _rf_choices=[])
        assert m._rapidfuzz_candidates("test", MagicMock(), MagicMock()) == []

    def test_empty_normalized_returns_empty(self) -> None:
        config = EnhancedMatcherConfig(enable_rapidfuzz=True)
        m = _make_mixin(_config=config, _rf_choices=["a"])
        mock_norm = MagicMock()
        mock_norm.normalize_for_comparison.return_value = ""
        import src.search.similarity._batch as _batch_mod
        with patch.object(_batch_mod, "TermNormalizer", mock_norm, create=True):
            result = m._rapidfuzz_candidates(
                "   ", MagicMock(), MagicMock(),
            )
        assert result == []

    def test_extract_returns_mapped_indices(self) -> None:
        config = EnhancedMatcherConfig(enable_rapidfuzz=True)
        # rf_idx_map maps choice-idx -> precomputed-idx
        m = _make_mixin(
            _config=config,
            _rf_choices=["alpha", "beta"],
            _rf_idx_map=[10, 20],
        )
        mock_process = MagicMock()
        mock_fuzz = MagicMock()
        # extract returns [(text, score, choice_idx), ...]
        mock_process.extract.return_value = [
            ("alpha", 95.0, 0),
            ("beta", 80.0, 1),
        ]
        mock_norm = MagicMock()
        mock_norm.normalize_for_comparison.return_value = "query"
        import src.search.similarity._batch as _batch_mod
        with patch.object(_batch_mod, "TermNormalizer", mock_norm, create=True):
            result = m._rapidfuzz_candidates(
                "query", mock_process, mock_fuzz,
            )
        assert result == [10, 20]

    def test_extract_exception_returns_empty(self) -> None:
        config = EnhancedMatcherConfig(enable_rapidfuzz=True)
        m = _make_mixin(
            _config=config,
            _rf_choices=["x"],
            _rf_idx_map=[0],
        )
        mock_process = MagicMock()
        mock_process.extract.side_effect = ValueError("bad")
        mock_fuzz = MagicMock()
        mock_norm = MagicMock()
        mock_norm.normalize_for_comparison.return_value = "query"
        import src.search.similarity._batch as _batch_mod
        with patch.object(_batch_mod, "TermNormalizer", mock_norm, create=True):
            result = m._rapidfuzz_candidates(
                "query", mock_process, mock_fuzz,
            )
        assert result == []


# ---------------------------------------------------------------------------
# _collect_mini_dense_candidates
# ---------------------------------------------------------------------------

class TestCollectMiniDenseCandidates:
    def test_import_error_returns_empty(self) -> None:
        m = _make_mixin()
        with patch(
            "builtins.__import__",
            side_effect=ImportError("no rapidfuzz"),
        ):
            result = m._collect_mini_dense_candidates(
                [_FakeTerm()], [0],
            )
        assert result == set()

    def test_collects_from_rapidfuzz_and_sparse(self) -> None:
        config = EnhancedMatcherConfig(enable_rapidfuzz=True)
        m = _make_mixin(_config=config, _rf_choices=["a"], _rf_idx_map=[5])

        mock_rf_candidates = MagicMock(return_value=[5])
        m._rapidfuzz_candidates = mock_rf_candidates  # type: ignore[assignment]

        m._l2_sparse = MagicMock(return_value=[(7, 0.8)])  # type: ignore[assignment]

        terms = [_FakeTerm(term="hello", term_ko="안녕")]

        with patch("src.search.similarity._batch.fuzz", create=True), \
             patch(
                 "src.search.similarity._batch.rf_process",
                 create=True,
             ):
            # rapidfuzz import happens inside; patch it
            with patch.dict(
                "sys.modules",
                {
                    "rapidfuzz": MagicMock(),
                    "rapidfuzz.fuzz": MagicMock(),
                    "rapidfuzz.process": MagicMock(),
                },
            ):
                result = m._collect_mini_dense_candidates(terms, [0])

        assert 5 in result or 7 in result


# ---------------------------------------------------------------------------
# _build_mini_dense
# ---------------------------------------------------------------------------

class TestBuildMiniDense:
    def test_no_embedding_adapter_returns_none(self) -> None:
        m = _make_mixin(_embedding_adapter=None)
        result = m._build_mini_dense([], [], [])
        assert result is None

    def test_no_candidates_returns_none(self) -> None:
        m = _make_mixin(_embedding_adapter=MagicMock())
        m._collect_mini_dense_candidates = MagicMock(  # type: ignore[assignment]
            return_value=set(),
        )
        result = m._build_mini_dense(
            [_FakeTerm()], [0], ["query"],
        )
        assert result is None

    def test_exception_returns_none(self) -> None:
        m = _make_mixin(_embedding_adapter=MagicMock())
        m._collect_mini_dense_candidates = MagicMock(  # type: ignore[assignment]
            return_value={1, 2},
        )
        m._build_and_search_mini_index = MagicMock(  # type: ignore[assignment]
            side_effect=RuntimeError("boom"),
        )
        m._precomputed = [MagicMock(), MagicMock(), MagicMock()]  # type: ignore[assignment]
        result = m._build_mini_dense(
            [_FakeTerm()], [0], ["query"],
        )
        assert result is None

    def test_delegates_to_build_and_search(self) -> None:
        expected = {0: [(1, 0.9)]}
        m = _make_mixin(
            _embedding_adapter=MagicMock(),
            _precomputed=[MagicMock(), MagicMock()],
        )
        m._collect_mini_dense_candidates = MagicMock(  # type: ignore[assignment]
            return_value={0, 1},
        )
        m._build_and_search_mini_index = MagicMock(  # type: ignore[assignment]
            return_value=expected,
        )
        result = m._build_mini_dense(
            [_FakeTerm()], [0], ["query"],
        )
        assert result == expected


# ---------------------------------------------------------------------------
# _build_and_search_mini_index
# ---------------------------------------------------------------------------

class TestBuildAndSearchMiniIndex:
    def test_not_ready_returns_none(self) -> None:
        pcs = [MagicMock() for _ in range(2)]
        m = _make_mixin(
            _precomputed=pcs,
            _embedding_adapter=MagicMock(),
        )
        mock_idx = MagicMock()
        mock_idx.is_ready = False
        with patch(
            "src.search.dense_term_index.DenseTermIndex",
            return_value=mock_idx,
        ):
            result = m._build_and_search_mini_index(
                [0, 1], [0], ["query"],
            )
        assert result is None

    def test_success_remaps_indices(self) -> None:
        # Need 8+ precomputed items so candidate_list indices 3,7 are valid
        pcs = [MagicMock() for _ in range(8)]
        m = _make_mixin(
            _precomputed=pcs,
            _embedding_adapter=MagicMock(),
        )
        mock_idx = MagicMock()
        mock_idx.is_ready = True
        # search_batch returns list of list-of-tuples
        # For one query: [(mini_idx=0, score=0.95)]
        mock_idx.search_batch.return_value = [[(0, 0.95)]]

        with patch(
            "src.search.dense_term_index.DenseTermIndex",
            return_value=mock_idx,
        ):
            result = m._build_and_search_mini_index(
                candidate_list=[3, 7],
                l1_unmatched_indices=[2],
                l1_unmatched_texts=["query"],
            )
        assert result is not None
        # mini_idx=0 -> candidate_list[0]=3
        assert result[2] == [(3, 0.95)]


# ---------------------------------------------------------------------------
# match_batch (integration-style, mocking internal helpers)
# ---------------------------------------------------------------------------

class TestMatchBatch:
    @pytest.mark.asyncio
    async def test_batch_returns_decisions(self) -> None:
        m = _make_mixin()
        m._resolve_ce_config = MagicMock(return_value=50)  # type: ignore[assignment]
        m._collect_l1_unmatched = MagicMock(  # type: ignore[assignment]
            return_value=([], []),
        )
        m._prepare_dense_batch = MagicMock(return_value=None)  # type: ignore[assignment]

        decision = MatchDecision(zone="NEW_TERM")
        m._match_single = AsyncMock(return_value=decision)  # type: ignore[assignment]

        terms = [_FakeTerm(term="a"), _FakeTerm(term="b")]
        results = await m.match_batch(terms)

        assert len(results) == 2
        assert all(r.zone == "NEW_TERM" for r in results)
        assert m._force_disable_ce is False

    @pytest.mark.asyncio
    async def test_batch_with_dense_override(self) -> None:
        m = _make_mixin()
        m._resolve_ce_config = MagicMock(return_value=0)  # type: ignore[assignment]
        m._collect_l1_unmatched = MagicMock(  # type: ignore[assignment]
            return_value=([0], ["q"]),
        )
        dense_results = {0: [(1, 0.9)]}
        m._prepare_dense_batch = MagicMock(  # type: ignore[assignment]
            return_value=dense_results,
        )
        decision = MatchDecision(zone="AUTO_MATCH", score=0.9)
        m._match_single = AsyncMock(return_value=decision)  # type: ignore[assignment]

        results = await m.match_batch([_FakeTerm()])
        assert len(results) == 1
        # Verify dense_override was passed
        call_kwargs = m._match_single.call_args[1]
        assert call_kwargs["dense_override"] == [(1, 0.9)]

    @pytest.mark.asyncio
    async def test_batch_disable_cross_encoder(self) -> None:
        m = _make_mixin()
        m._resolve_ce_config = MagicMock(return_value=0)  # type: ignore[assignment]
        m._collect_l1_unmatched = MagicMock(  # type: ignore[assignment]
            return_value=([], []),
        )
        m._prepare_dense_batch = MagicMock(return_value=None)  # type: ignore[assignment]
        m._match_single = AsyncMock(  # type: ignore[assignment]
            return_value=MatchDecision(zone="NEW_TERM"),
        )

        await m.match_batch(
            [_FakeTerm()], disable_cross_encoder=True,
        )
        m._resolve_ce_config.assert_called_once_with(
            [_FakeTerm()], True,
        )

    @pytest.mark.asyncio
    async def test_empty_batch(self) -> None:
        m = _make_mixin()
        m._resolve_ce_config = MagicMock(return_value=50)  # type: ignore[assignment]
        m._collect_l1_unmatched = MagicMock(  # type: ignore[assignment]
            return_value=([], []),
        )
        m._prepare_dense_batch = MagicMock(return_value=None)  # type: ignore[assignment]

        results = await m.match_batch([])
        assert results == []
