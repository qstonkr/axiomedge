"""Base repository with common session management."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class BaseRepository:
    """Common base for all PostgreSQL repositories.

    Provides session_maker storage and session creation.
    """

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def _get_session(self) -> AsyncSession:
        return self._session_maker()
