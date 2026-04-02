"""Extended unit tests for src/database/repositories/ — CategoryRepository and remaining methods."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest


def _run(coro):
    return asyncio.run(coro)


def _make_session_maker():
    """Create a mock async_sessionmaker that produces mock sessions."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    maker = MagicMock()
    maker.return_value = session
    return maker, session


def _make_scalars_result(models):
    """Build a mock result that supports .scalars().all()."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = models
    scalars.first.return_value = models[0] if models else None
    result.scalars.return_value = scalars
    result.scalar_one_or_none.return_value = models[0] if models else None
    result.scalar.return_value = len(models)
    return result


# ===========================================================================
# CategoryRepository
# ===========================================================================
class TestCategoryRepository:
    def test_get_l1_categories_empty(self):
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        session.execute = AsyncMock(return_value=_make_scalars_result([]))
        repo = CategoryRepository(maker)

        async def _go():
            cats = await repo.get_l1_categories()
            assert cats == []

        _run(_go())

    def test_get_l1_categories_with_results(self):
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        cat1 = MagicMock()
        cat1.id = uuid4()
        cat1.name = "IT"
        cat1.description = "IT category"
        cat1.keywords = ["server", "network"]
        cat1.sort_order = 1

        session.execute = AsyncMock(return_value=_make_scalars_result([cat1]))
        repo = CategoryRepository(maker)

        async def _go():
            cats = await repo.get_l1_categories()
            assert len(cats) == 1
            assert cats[0]["name"] == "IT"
            assert cats[0]["keywords"] == ["server", "network"]

        _run(_go())

    def test_get_l1_categories_cache(self):
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        cat1 = MagicMock()
        cat1.id = uuid4()
        cat1.name = "IT"
        cat1.description = ""
        cat1.keywords = []
        cat1.sort_order = 1

        session.execute = AsyncMock(return_value=_make_scalars_result([cat1]))
        repo = CategoryRepository(maker)

        async def _go():
            cats1 = await repo.get_l1_categories()
            cats2 = await repo.get_l1_categories()  # should use cache
            assert cats1 == cats2
            # execute should be called only once due to caching
            assert session.execute.await_count == 1

        _run(_go())

    def test_invalidate_cache(self):
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        session.execute = AsyncMock(return_value=_make_scalars_result([]))
        repo = CategoryRepository(maker)

        async def _go():
            await repo.get_l1_categories()
            assert repo._l1_cache is not None
            repo.invalidate_cache()
            assert repo._l1_cache is None

        _run(_go())

    def test_get_l1_categories_skip_cache(self):
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        session.execute = AsyncMock(return_value=_make_scalars_result([]))
        repo = CategoryRepository(maker)

        async def _go():
            await repo.get_l1_categories(use_cache=False)
            await repo.get_l1_categories(use_cache=False)
            assert session.execute.await_count == 2

        _run(_go())

    def test_get_l1_categories_db_error(self):
        from src.database.repositories.category import CategoryRepository
        from sqlalchemy.exc import SQLAlchemyError

        maker, session = _make_session_maker()
        session.execute = AsyncMock(side_effect=SQLAlchemyError("db err"))
        repo = CategoryRepository(maker)

        async def _go():
            cats = await repo.get_l1_categories(use_cache=False)
            assert cats == []

        _run(_go())

    def test_get_all_categories(self):
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        cat1 = MagicMock()
        cat1.id = uuid4()
        cat1.level = 1
        cat1.name = "IT"
        cat1.name_ko = "IT"
        cat1.description = "desc"
        cat1.keywords = ["k"]
        cat1.parent_id = None
        cat1.sort_order = 1
        cat1.is_active = True

        cat2 = MagicMock()
        cat2.id = uuid4()
        cat2.level = 2
        cat2.name = "Server"
        cat2.name_ko = "서버"
        cat2.description = ""
        cat2.keywords = "not_a_list"  # test non-list fallback
        cat2.parent_id = cat1.id
        cat2.sort_order = 1
        cat2.is_active = True

        session.execute = AsyncMock(return_value=_make_scalars_result([cat1, cat2]))
        repo = CategoryRepository(maker)

        async def _go():
            cats = await repo.get_all_categories()
            assert len(cats) == 2
            assert cats[0]["level"] == 1
            assert cats[1]["parent_id"] == str(cat1.id)

        _run(_go())

    def test_get_all_categories_db_error(self):
        from src.database.repositories.category import CategoryRepository
        from sqlalchemy.exc import SQLAlchemyError

        maker, session = _make_session_maker()
        session.execute = AsyncMock(side_effect=SQLAlchemyError("err"))
        repo = CategoryRepository(maker)

        async def _go():
            cats = await repo.get_all_categories()
            assert cats == []

        _run(_go())

    def test_create_category(self):
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        orm_mock = MagicMock()
        orm_mock.id = uuid4()
        orm_mock.name = "New Cat"
        orm_mock.level = 1

        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        with patch("src.database.repositories.category.KnowledgeCategoryModel", return_value=orm_mock):
            repo = CategoryRepository(maker)

            async def _go():
                result = await repo.create_category({"name": "New Cat", "level": 1})
                assert result is not None
                assert result["name"] == "New Cat"

            _run(_go())

    def test_create_category_db_error(self):
        from src.database.repositories.category import CategoryRepository
        from sqlalchemy.exc import SQLAlchemyError

        maker, session = _make_session_maker()
        session.add = MagicMock(side_effect=SQLAlchemyError("err"))

        with patch("src.database.repositories.category.KnowledgeCategoryModel", side_effect=SQLAlchemyError("err")):
            repo = CategoryRepository(maker)

            async def _go():
                result = await repo.create_category({"name": "Bad Cat"})
                assert result is None

            _run(_go())

    def test_update_category(self):
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        repo = CategoryRepository(maker)
        cat_id = uuid4()

        async def _go():
            result = await repo.update_category(cat_id, {"name": "Updated"})
            assert result is True

        _run(_go())

    def test_update_category_db_error(self):
        from src.database.repositories.category import CategoryRepository
        from sqlalchemy.exc import SQLAlchemyError

        maker, session = _make_session_maker()
        session.execute = AsyncMock(side_effect=SQLAlchemyError("err"))
        repo = CategoryRepository(maker)

        async def _go():
            result = await repo.update_category(uuid4(), {"name": "Bad"})
            assert result is False

        _run(_go())

    def test_soft_delete_category(self):
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        repo = CategoryRepository(maker)

        async def _go():
            result = await repo.soft_delete_category(uuid4())
            assert result is True

        _run(_go())

    def test_keywords_non_list(self):
        """Test that non-list keywords returns empty list."""
        from src.database.repositories.category import CategoryRepository

        maker, session = _make_session_maker()
        cat = MagicMock()
        cat.id = uuid4()
        cat.name = "Test"
        cat.description = ""
        cat.keywords = "string_not_list"
        cat.sort_order = 1

        session.execute = AsyncMock(return_value=_make_scalars_result([cat]))
        repo = CategoryRepository(maker)

        async def _go():
            cats = await repo.get_l1_categories(use_cache=False)
            assert cats[0]["keywords"] == []

        _run(_go())
