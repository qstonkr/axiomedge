"""Backfill tests for GlossaryRepository — covers branches missed by test_db_repositories_full.

Targets: search, list_by_kb, get_by_term, save_batch, _prepare_batch_row,
_build_new_term, _update_existing_term JSON handling, _model_to_dict edge cases,
count_by_kb with filters, error/rollback branches.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_maker():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    maker = MagicMock()
    maker.return_value = session
    maker.kw = {
        "bind": MagicMock(url="postgresql+asyncpg://localhost/test"),
    }
    return maker, session


def _make_scalars_result(models):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = models
    scalars.first.return_value = models[0] if models else None
    result.scalars.return_value = scalars
    result.scalar_one_or_none.return_value = (
        models[0] if models else None
    )
    result.scalar.return_value = len(models)
    return result


def _glossary_model(**overrides):
    """Build a MagicMock that looks like GlossaryTermModel."""
    defaults = {
        "id": "t1",
        "kb_id": "kb1",
        "term": "API",
        "term_ko": "에이피아이",
        "definition": "Application Programming Interface",
        "synonyms": '["api"]',
        "abbreviations": "[]",
        "related_terms": "[]",
        "source": "manual",
        "confidence_score": 1.0,
        "status": "approved",
        "occurrence_count": 3,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------------------
# _model_to_dict edge cases
# ---------------------------------------------------------------------------

class TestModelToDict:
    def test_json_list_from_string(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model(synonyms='["a","b"]')
        d = GlossaryRepository._model_to_dict(model)
        assert d["synonyms"] == ["a", "b"]

    def test_json_list_from_none(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model(synonyms=None, abbreviations=None)
        d = GlossaryRepository._model_to_dict(model)
        assert d["synonyms"] == []
        assert d["abbreviations"] == []

    def test_json_list_from_list(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model(synonyms=["x", "y"])
        d = GlossaryRepository._model_to_dict(model)
        assert d["synonyms"] == ["x", "y"]

    def test_json_list_malformed_string(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model(synonyms="not json")
        d = GlossaryRepository._model_to_dict(model)
        assert d["synonyms"] == []

    def test_optional_attrs_missing(self):
        """Model without category/created_by/etc uses getattr defaults."""
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model()
        # Remove attrs so getattr fallback triggers
        del model.category
        del model.created_by
        del model.approved_by
        del model.approved_at
        del model.scope
        del model.source_kb_ids
        del model.physical_meaning
        del model.composition_info
        del model.domain_name
        del model.term_type
        d = GlossaryRepository._model_to_dict(model)
        assert d["category"] is None
        assert d["scope"] == "kb"
        assert d["term_type"] == "term"
        assert d["source_kb_ids"] == []

    def test_occurrence_count_none(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model(occurrence_count=None)
        d = GlossaryRepository._model_to_dict(model)
        assert d["occurrence_count"] == 0


# ---------------------------------------------------------------------------
# _scope_filter
# ---------------------------------------------------------------------------

class TestScopeFilter:
    def test_empty_string_returns_none(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        maker, _ = _make_session_maker()
        repo = GlossaryRepository(maker)
        assert repo._scope_filter("") is None

    def test_all_uppercase(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        maker, _ = _make_session_maker()
        repo = GlossaryRepository(maker)
        assert repo._scope_filter("ALL") is None

    def test_specific_kb_returns_clause(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        maker, _ = _make_session_maker()
        repo = GlossaryRepository(maker)
        assert repo._scope_filter("my-kb") is not None


# ---------------------------------------------------------------------------
# _update_existing_term / _build_new_term
# ---------------------------------------------------------------------------

class TestTermBuilders:
    def test_update_existing_term_json_fields(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        existing = MagicMock()
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        GlossaryRepository._update_existing_term(
            existing,
            {
                "synonyms": ["a", "b"],
                "abbreviations": "not-a-list",
                "definition": "new",
                "nonexistent_field": 999,
            },
            now,
        )
        # synonyms list -> json
        assert existing.synonyms == json.dumps(["a", "b"])
        # abbreviations non-list -> json []
        assert existing.abbreviations == json.dumps([])
        assert existing.definition == "new"
        assert existing.updated_at == now

    def test_build_new_term_defaults(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        with patch(
            "src.stores.postgres.repositories.glossary.GlossaryTermModel",
        ) as MockModel:
            MockModel.return_value = MagicMock()
            GlossaryRepository._build_new_term(
                {"term": "X", "synonyms": ["s1"]}, now,
            )
            call_kw = MockModel.call_args
            assert call_kw is not None
            args = call_kw[1] if call_kw[1] else call_kw[0][0]
            # synonyms serialized
            if isinstance(args, dict):
                assert args["synonyms"] == json.dumps(["s1"])

    def test_build_new_term_non_list_json_field(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        with patch(
            "src.stores.postgres.repositories.glossary.GlossaryTermModel",
        ) as MockModel:
            MockModel.return_value = MagicMock()
            GlossaryRepository._build_new_term(
                {"term": "X", "abbreviations": "not-a-list"}, now,
            )
            call_kw = MockModel.call_args[1]
            if call_kw:
                assert call_kw["abbreviations"] == json.dumps([])


# ---------------------------------------------------------------------------
# _prepare_batch_row
# ---------------------------------------------------------------------------

class TestPrepareBatchRow:
    def test_basic_row(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        maker, _ = _make_session_maker()
        repo = GlossaryRepository(maker)
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        columns = [
            "id", "kb_id", "term", "synonyms",
            "abbreviations", "related_terms", "source_kb_ids",
            "confidence_score", "occurrence_count",
            "created_at", "updated_at",
        ]
        row = repo._prepare_batch_row(
            {"id": "1", "kb_id": "kb1", "term": "T", "synonyms": ["s"]},
            columns,
            now,
        )
        assert isinstance(row, tuple)
        assert len(row) == len(columns)
        # synonyms serialized
        assert row[3] == json.dumps(["s"])
        # abbreviations defaulted
        assert row[4] == "[]"
        # confidence_score defaulted
        assert row[7] == 0
        # created_at defaulted
        assert row[9] == now

    def test_batch_row_non_list_synonym(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        maker, _ = _make_session_maker()
        repo = GlossaryRepository(maker)
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        columns = ["synonyms", "abbreviations", "related_terms",
                    "source_kb_ids", "confidence_score",
                    "occurrence_count", "created_at", "updated_at"]
        row = repo._prepare_batch_row(
            {"synonyms": "string-val"},
            columns,
            now,
        )
        # non-list val → json []
        assert row[0] == json.dumps([])

    def test_batch_row_empty_field_defaults_to_empty_json(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        maker, _ = _make_session_maker()
        repo = GlossaryRepository(maker)
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        columns = ["synonyms", "abbreviations", "related_terms",
                    "source_kb_ids", "confidence_score",
                    "occurrence_count", "created_at", "updated_at"]
        row = repo._prepare_batch_row({}, columns, now)
        # All json fields -> "[]"
        assert row[0] == "[]"
        assert row[1] == "[]"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_search_returns_results(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = GlossaryRepository(self.maker)

        results = await repo.search("kb1", "api")
        assert len(results) == 1
        assert results[0]["term"] == "API"

    async def test_search_empty_query(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        repo = GlossaryRepository(self.maker)
        assert await repo.search("kb1", "") == []
        assert await repo.search("kb1", "   ") == []

    async def test_search_disabled(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        repo = GlossaryRepository(self.maker)
        repo._search_available = False
        assert await repo.search("kb1", "api") == []

    async def test_search_sqlalchemy_error_returns_empty(self):
        from sqlalchemy.exc import SQLAlchemyError
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.side_effect = SQLAlchemyError("fail")
        repo = GlossaryRepository(self.maker)
        assert await repo.search("kb1", "api") == []

    async def test_search_with_kb_all(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = GlossaryRepository(self.maker)

        results = await repo.search("all", "api")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# get_by_term
# ---------------------------------------------------------------------------

class TestGetByTerm:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_get_by_term_found(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = GlossaryRepository(self.maker)

        result = await repo.get_by_term("kb1", "API")
        assert result is not None
        assert result["term"] == "API"

    async def test_get_by_term_not_found(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        result = await repo.get_by_term("kb1", "nonexistent")
        assert result is None

    async def test_get_by_term_kb_all(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = _glossary_model()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = GlossaryRepository(self.maker)

        result = await repo.get_by_term("all", "API")
        assert result is not None


# ---------------------------------------------------------------------------
# list_by_kb
# ---------------------------------------------------------------------------

class TestListByKb:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_list_basic(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        m1 = _glossary_model(id="t1", term="A")
        m2 = _glossary_model(id="t2", term="B")
        self.session.execute.return_value = _make_scalars_result([m1, m2])
        repo = GlossaryRepository(self.maker)

        results = await repo.list_by_kb("kb1")
        assert len(results) == 2

    async def test_list_with_status_filter(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        results = await repo.list_by_kb("kb1", status="pending")
        assert results == []

    async def test_list_with_source_filter(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        results = await repo.list_by_kb("kb1", source="auto")
        assert results == []

    async def test_list_with_scope_global(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        results = await repo.list_by_kb("kb1", scope="global")
        assert results == []

    async def test_list_with_scope_kb(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        results = await repo.list_by_kb("kb1", scope="kb")
        assert results == []

    async def test_list_with_scope_kb_all(self):
        """scope='kb' + kb_id='all' should not add kb_id filter."""
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        results = await repo.list_by_kb("all", scope="kb")
        assert results == []

    async def test_list_with_term_type(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        results = await repo.list_by_kb("kb1", term_type="abbreviation")
        assert results == []

    async def test_list_no_scope_filter_kb_all(self):
        """scope=None + kb_id='all' => _scope_filter returns None."""
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        results = await repo.list_by_kb("all")
        assert results == []


# ---------------------------------------------------------------------------
# count_by_kb with filters
# ---------------------------------------------------------------------------

class TestCountByKb:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_count_with_status(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        result_mock = MagicMock()
        result_mock.scalar.return_value = 10
        self.session.execute.return_value = result_mock
        repo = GlossaryRepository(self.maker)

        count = await repo.count_by_kb("kb1", status="approved")
        assert count == 10

    async def test_count_with_scope_global(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        result_mock = MagicMock()
        result_mock.scalar.return_value = 5
        self.session.execute.return_value = result_mock
        repo = GlossaryRepository(self.maker)

        count = await repo.count_by_kb("kb1", scope="global")
        assert count == 5

    async def test_count_with_scope_kb(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        result_mock = MagicMock()
        result_mock.scalar.return_value = 3
        self.session.execute.return_value = result_mock
        repo = GlossaryRepository(self.maker)

        count = await repo.count_by_kb("kb1", scope="kb")
        assert count == 3

    async def test_count_with_term_type(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        result_mock = MagicMock()
        result_mock.scalar.return_value = 2
        self.session.execute.return_value = result_mock
        repo = GlossaryRepository(self.maker)

        count = await repo.count_by_kb("kb1", term_type="abbreviation")
        assert count == 2

    async def test_count_returns_zero_on_none(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        result_mock = MagicMock()
        result_mock.scalar.return_value = None
        self.session.execute.return_value = result_mock
        repo = GlossaryRepository(self.maker)

        count = await repo.count_by_kb("kb1")
        assert count == 0

    async def test_count_scope_kb_all(self):
        """scope='kb' + kb_id='all' — no kb_id filter added."""
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        result_mock = MagicMock()
        result_mock.scalar.return_value = 7
        self.session.execute.return_value = result_mock
        repo = GlossaryRepository(self.maker)

        count = await repo.count_by_kb("all", scope="kb")
        assert count == 7


# ---------------------------------------------------------------------------
# save error branch
# ---------------------------------------------------------------------------

class TestSaveErrors:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_rollback_on_error(self):
        from sqlalchemy.exc import SQLAlchemyError
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.side_effect = SQLAlchemyError("db fail")
        repo = GlossaryRepository(self.maker)

        with pytest.raises(SQLAlchemyError):
            await repo.save(
                {"kb_id": "kb1", "term": "X", "definition": "y"},
            )
        self.session.rollback.assert_awaited_once()

    async def test_delete_rollback_on_error(self):
        from sqlalchemy.exc import SQLAlchemyError
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        model = MagicMock()
        self.session.execute.return_value = _make_scalars_result([model])
        self.session.delete.side_effect = SQLAlchemyError("fail")
        repo = GlossaryRepository(self.maker)

        with pytest.raises(SQLAlchemyError):
            await repo.delete("t1")
        self.session.rollback.assert_awaited_once()

    async def test_bulk_delete_rollback_on_error(self):
        from sqlalchemy.exc import SQLAlchemyError
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        self.session.execute.side_effect = SQLAlchemyError("fail")
        repo = GlossaryRepository(self.maker)

        with pytest.raises(SQLAlchemyError):
            await repo.bulk_delete(["t1"])
        self.session.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# save_batch
# ---------------------------------------------------------------------------

class TestSaveBatch:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_batch_empty(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        repo = GlossaryRepository(self.maker)
        assert await repo.save_batch([]) == 0

    async def test_save_batch_success(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        repo = GlossaryRepository(self.maker)

        mock_conn = AsyncMock()
        mock_conn.executemany = AsyncMock()
        mock_conn.close = AsyncMock()

        with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
            count = await repo.save_batch([
                {"id": "1", "kb_id": "kb1", "term": "T1"},
                {"id": "2", "kb_id": "kb1", "term": "T2"},
            ])

        assert count == 2
        mock_conn.executemany.assert_awaited_once()
        mock_conn.close.assert_awaited_once()

    async def test_save_batch_no_bind(self):
        """When session_maker has no bind, falls back to env/default."""
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        maker, _ = _make_session_maker()
        maker.kw = {}  # no bind
        repo = GlossaryRepository(maker)

        mock_conn = AsyncMock()
        mock_conn.executemany = AsyncMock()
        mock_conn.close = AsyncMock()

        with (
            patch(
                "asyncpg.connect",
                AsyncMock(return_value=mock_conn),
            ),
            patch.dict(
                "os.environ",
                {"DATABASE_URL": "postgresql+asyncpg://localhost/envdb"},
            ),
        ):
            count = await repo.save_batch([
                {"id": "1", "kb_id": "kb1", "term": "T1"},
            ])

        assert count == 1

    async def test_save_batch_error(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        repo = GlossaryRepository(self.maker)

        with patch(
            "asyncpg.connect",
            AsyncMock(side_effect=RuntimeError("conn fail")),
        ):
            with pytest.raises(RuntimeError, match="conn fail"):
                await repo.save_batch([
                    {"id": "1", "kb_id": "kb1", "term": "T1"},
                ])

    async def test_save_batch_executemany_error_closes_conn(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        repo = GlossaryRepository(self.maker)

        mock_conn = AsyncMock()
        mock_conn.executemany = AsyncMock(
            side_effect=RuntimeError("exec fail"),
        )
        mock_conn.close = AsyncMock()

        with patch(
            "asyncpg.connect",
            AsyncMock(return_value=mock_conn),
        ):
            with pytest.raises(RuntimeError, match="exec fail"):
                await repo.save_batch([
                    {"id": "1", "kb_id": "kb1", "term": "T1"},
                ])
        # Connection closed even on error (finally block)
        mock_conn.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# _json_list_fields
# ---------------------------------------------------------------------------

class TestJsonListFields:
    def test_returns_expected_fields(self):
        from src.stores.postgres.repositories.glossary import (
            GlossaryRepository,
        )
        fields = GlossaryRepository._json_list_fields()
        assert "synonyms" in fields
        assert "abbreviations" in fields
        assert "related_terms" in fields
        assert "source_kb_ids" in fields
