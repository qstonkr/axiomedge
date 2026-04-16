"""Distill 테이블 생성 + 마이그레이션 스크립트.

DistillBase 전용 테이블을 생성하고, 기존 테이블에 새 컬럼을 추가.

Usage:
    uv run python scripts/distill_init_db.py
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings
from src.stores.postgres.session import to_async_database_url
from src.distill.models import DistillBase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 기존 테이블에 추가할 컬럼 (ALTER TABLE ADD COLUMN IF NOT EXISTS)
MIGRATIONS = [
    # DistillTrainingDataModel 신규 컬럼
    "ALTER TABLE distill_training_data ADD COLUMN IF NOT EXISTS consistency_score FLOAT",
    "ALTER TABLE distill_training_data ADD COLUMN IF NOT EXISTS generality_score FLOAT",
    "ALTER TABLE distill_training_data ADD COLUMN IF NOT EXISTS augmentation_verified BOOLEAN",
    "ALTER TABLE distill_training_data ADD COLUMN IF NOT EXISTS augmented_from VARCHAR(36)",
    "ALTER TABLE distill_training_data ADD COLUMN IF NOT EXISTS generation_batch_id VARCHAR(36)",
    "ALTER TABLE distill_training_data ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE distill_training_data ADD COLUMN IF NOT EXISTS review_comment TEXT",
    # DistillBuildModel 신규 컬럼
    "ALTER TABLE distill_builds ADD COLUMN IF NOT EXISTS gguf_sha256 VARCHAR(64)",
    "ALTER TABLE distill_builds ADD COLUMN IF NOT EXISTS model_name VARCHAR(100)",
    "ALTER TABLE distill_builds ADD COLUMN IF NOT EXISTS rollback_from VARCHAR(36)",
    # 인덱스
    (
        "CREATE INDEX IF NOT EXISTS idx_train_data_batch "
        "ON distill_training_data (generation_batch_id)"
    ),
]


async def main():
    settings = get_settings()
    db_url = to_async_database_url(settings.database.database_url)
    logger.info("Creating distill tables at: %s", db_url.split("@")[-1])

    engine = create_async_engine(db_url)

    # 1. create_all — 새 테이블 생성 (기존 테이블 영향 없음)
    async with engine.begin() as conn:
        await conn.run_sync(DistillBase.metadata.create_all)

    # 2. 마이그레이션 — 기존 테이블에 새 컬럼 추가
    async with engine.begin() as conn:
        for sql in MIGRATIONS:
            try:
                await conn.execute(text(sql))
            except Exception as e:
                logger.warning("Migration skipped: %s (%s)", sql[:60], e)

    await engine.dispose()
    logger.info("Distill tables created/migrated successfully")

    for table_name in DistillBase.metadata.tables:
        logger.info("  %s", table_name)


if __name__ == "__main__":
    asyncio.run(main())
