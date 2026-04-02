"""Unit tests for BaseRepository."""

from unittest.mock import MagicMock

from src.database.repositories.base import BaseRepository


class TestBaseRepository:
    def test_stores_session_maker(self) -> None:
        mock_maker = MagicMock()
        repo = BaseRepository(session_maker=mock_maker)
        assert repo._session_maker is mock_maker

    def test_get_session_returns_maker_result(self) -> None:
        mock_session = MagicMock()
        mock_maker = MagicMock(return_value=mock_session)
        repo = BaseRepository(session_maker=mock_maker)
        import asyncio
        session = asyncio.run(repo._get_session())
        mock_maker.assert_called_once()
        assert session is mock_session
