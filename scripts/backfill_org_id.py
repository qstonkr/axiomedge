#!/usr/bin/env python3
"""Backfill organization_id on existing KBs (B-0 Day 1 prerequisite).

Single-tenant dev environments started before B-0 have ``KBConfigModel.organization_id``
NULL on every row. Day 5 will flip the column to NOT NULL + add an FK; that
migration will fail unless every row points at a valid Organization.

This script is idempotent — only updates rows where the column is NULL — and
defaults all of them to the ``default-org`` tenant seeded by migration
``0003_rbac_b0``.

Usage::

    uv run python scripts/backfill_org_id.py            # dry-run (default)
    uv run python scripts/backfill_org_id.py --apply    # actually update rows
    uv run python scripts/backfill_org_id.py --org-id acme --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.auth.org_service import DEFAULT_ORG_ID
from src.config import get_settings

logger = logging.getLogger(__name__)


async def _run(target_org_id: str, apply: bool) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database.database_url)

    try:
        async with engine.begin() as conn:
            org_check = await conn.execute(
                text("SELECT id FROM organizations WHERE id = :id"),
                {"id": target_org_id},
            )
            if org_check.first() is None:
                logger.error(
                    "Organization '%s' does not exist. Run alembic upgrade first "
                    "(migration 0003_rbac_b0 creates default-org).",
                    target_org_id,
                )
                return 1

            null_result = await conn.execute(
                text("SELECT COUNT(*) FROM kb_configs WHERE organization_id IS NULL")
            )
            null_count = null_result.scalar_one()

            total_result = await conn.execute(
                text("SELECT COUNT(*) FROM kb_configs")
            )
            total_count = total_result.scalar_one()

            logger.info(
                "Found %d KBs with NULL organization_id (out of %d total)",
                null_count, total_count,
            )

            if null_count == 0:
                logger.info("Nothing to backfill — already set on every row.")
                return 0

            if not apply:
                logger.info(
                    "Dry-run only. Re-run with --apply to set organization_id='%s' "
                    "on %d rows.", target_org_id, null_count,
                )
                return 0

            updated = await conn.execute(
                text(
                    "UPDATE kb_configs SET organization_id = :org "
                    "WHERE organization_id IS NULL"
                ),
                {"org": target_org_id},
            )
            logger.info(
                "Updated %d KBs → organization_id='%s'",
                updated.rowcount, target_org_id,
            )
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--org-id", default=DEFAULT_ORG_ID,
        help=f"Target organization ID (default: {DEFAULT_ORG_ID})",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually run the UPDATE. Without it, only counts are printed.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.org_id, args.apply))


if __name__ == "__main__":
    sys.exit(main())
