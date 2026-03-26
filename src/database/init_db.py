"""Database initialization script.

Creates all tables using SQLAlchemy metadata.create_all().
Can be run standalone or imported.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import create_async_engine

from src.database.models import KnowledgeBase, RegistryBase
from src.database.session import to_async_database_url

# Import auth models so they register with KnowledgeBase.metadata
import src.auth.models  # noqa: F401

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_db"


async def init_database(database_url: str | None = None) -> None:
    """Create all tables in the database.

    Args:
        database_url: Database URL. Defaults to DATABASE_URL env var
                      or postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_db
    """
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    url = to_async_database_url(url)

    logger.info("Initializing database at: %s", url.split("@")[-1])

    engine = create_async_engine(url, echo=False)

    async with engine.begin() as conn:
        # Create KnowledgeBase tables (Text-column based, cross-dialect)
        await conn.run_sync(KnowledgeBase.metadata.create_all)
        logger.info("KnowledgeBase tables created (%d tables)", len(KnowledgeBase.metadata.tables))

        # Create RegistryBase tables (JSONB-based, PG-specific)
        await conn.run_sync(RegistryBase.metadata.create_all)
        logger.info("RegistryBase tables created (%d tables)", len(RegistryBase.metadata.tables))

    await engine.dispose()
    logger.info("Database initialization complete")


async def drop_all_tables(database_url: str | None = None) -> None:
    """Drop all tables (for testing/reset)."""
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    url = to_async_database_url(url)

    engine = create_async_engine(url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(RegistryBase.metadata.drop_all)
        await conn.run_sync(KnowledgeBase.metadata.drop_all)

    await engine.dispose()
    logger.info("All tables dropped")


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(init_database())


if __name__ == "__main__":
    main()
