"""Distill 테이블 생성 스크립트.

DistillBase 전용 테이블을 생성. RAG 코어 테이블과 독립.

Usage:
    uv run python scripts/distill_init_db.py
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings
from src.distill.models import DistillBase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    settings = get_settings()
    db_url = settings.database.database_url
    logger.info("Creating distill tables at: %s", db_url.split("@")[-1])

    engine = create_async_engine(db_url)

    async with engine.begin() as conn:
        await conn.run_sync(DistillBase.metadata.create_all)

    await engine.dispose()
    logger.info("Distill tables created successfully")

    # 테이블 목록 출력
    for table_name in DistillBase.metadata.tables:
        logger.info("  ✅ %s", table_name)


if __name__ == "__main__":
    asyncio.run(main())
