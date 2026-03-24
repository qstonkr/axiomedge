"""Async SQLAlchemy Session Factory.

Centralize async SQLAlchemy session factory creation for PostgreSQL.
Combines db_url normalization and session factory creation.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def to_async_database_url(url: str) -> str:
    """Convert a database URL to asyncpg-compatible format."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def create_async_session_factory(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_pre_ping: bool = True,
    echo: bool = False,
) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory for PostgreSQL."""
    database_url = to_async_database_url(database_url)
    engine = create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=pool_pre_ping,
        echo=echo,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


_knowledge_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_knowledge_session_maker() -> async_sessionmaker[AsyncSession] | None:
    """Get or create the knowledge session maker singleton.

    Uses DATABASE_URL from environment. Returns None if not configured.
    """
    global _knowledge_session_maker
    if _knowledge_session_maker is not None:
        return _knowledge_session_maker

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return None

    _knowledge_session_maker = create_async_session_factory(db_url)
    return _knowledge_session_maker


def reset_session_maker() -> None:
    """Reset singleton (for testing)."""
    global _knowledge_session_maker
    _knowledge_session_maker = None
