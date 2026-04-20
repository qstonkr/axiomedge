#!/usr/bin/env python3
"""기존 data_sources 의 평문 token → SecretBox 자동 흡수.

배경:
- 0006 migration 으로 ``secret_path`` / ``has_secret`` 컬럼 추가
- 그러나 기존 데이터의 ``crawl_config.auth_token`` (Git PAT) 등은
  여전히 DB 평문 — 본 스크립트가 SecretBox 로 일괄 이동.
- ``CONFLUENCE_PAT`` env 가 설정되어 있고 default-org 의 confluence
  source 가 단 1개면 → 그 source 에 자동 attach (모호한 경우 skip).

Idempotent — 이미 ``has_secret=True`` 인 source 는 건드리지 않음.

Usage::

    SECRET_BOX_KEY=... uv run python scripts/migrate_data_source_secrets.py
        # dry-run (default) — 무엇이 바뀔지만 출력
    SECRET_BOX_KEY=... uv run python scripts/migrate_data_source_secrets.py --apply
        # 실제 적용
    SECRET_BOX_KEY=... uv run python scripts/migrate_data_source_secrets.py --apply --skip-env
        # 환경변수 (CONFLUENCE_PAT) 흡수는 skip — DB 평문 token 만 이동

Safety:
- ``--apply`` 없이는 어떤 변경도 일어나지 않음
- 각 source 별 처리 결과 (action / source_id / org / type) 출력
- SecretBox 저장 실패 시 그 source 만 skip — 다른 source 진행
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.auth.secret_box import SecretBoxError, get_secret_box
from src.config import get_settings
from src.stores.postgres.models import DataSourceModel

logger = logging.getLogger(__name__)

# 평문 token 으로 인식하는 crawl_config 키 — 라우트의 _SECRET_MASK_KEYS 와 동기.
SECRET_KEYS = ("auth_token", "pat", "password", "api_key", "token")


def _secret_path(organization_id: str, source_id: str) -> str:
    return f"org/{organization_id}/data-source/{source_id}"


def _extract_plain_token(crawl_config: Any) -> tuple[str | None, str | None]:
    """crawl_config dict 에서 평문 token 후보 추출. (token, key) 또는 (None, None)."""
    if not isinstance(crawl_config, dict):
        return None, None
    for key in SECRET_KEYS:
        val = crawl_config.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip(), key
    return None, None


async def _migrate_db_plaintext(
    session_factory: async_sessionmaker, apply: bool, summary: dict[str, int],
) -> list[dict[str, Any]]:
    """DB 의 crawl_config 평문 token → SecretBox 이동. 결과 리스트 반환."""
    actions: list[dict[str, Any]] = []
    box = get_secret_box()

    async with session_factory() as session:
        result = await session.execute(
            select(DataSourceModel).where(DataSourceModel.has_secret.is_(False)),
        )
        rows = list(result.scalars().all())

    for row in rows:
        cfg_text = row.crawl_config or "{}"
        try:
            cfg = json.loads(cfg_text)
        except (json.JSONDecodeError, TypeError):
            cfg = {}
        plain, key = _extract_plain_token(cfg)
        if not plain:
            continue

        path = _secret_path(row.organization_id, row.id)
        action = {
            "source_id": row.id,
            "name": row.name,
            "organization_id": row.organization_id,
            "source_type": row.source_type,
            "from_key": key,
            "secret_path": path,
            "applied": False,
            "error": None,
        }
        actions.append(action)
        if not apply:
            summary["would_migrate_db"] += 1
            continue

        try:
            await box.put(path, plain)
        except SecretBoxError as e:
            action["error"] = f"SecretBox.put failed: {e}"
            summary["errors"] += 1
            continue

        # crawl_config 에서 평문 token 키 strip
        new_cfg = {k: v for k, v in cfg.items() if k not in SECRET_KEYS}
        async with session_factory() as session:
            try:
                await session.execute(
                    update(DataSourceModel)
                    .where(DataSourceModel.id == row.id)
                    .values(
                        crawl_config=json.dumps(new_cfg),
                        secret_path=path,
                        has_secret=True,
                    ),
                )
                await session.commit()
                action["applied"] = True
                summary["migrated_db"] += 1
            except Exception as e:  # noqa: BLE001
                await session.rollback()
                action["error"] = f"DB update failed: {e}"
                summary["errors"] += 1

    return actions


async def _migrate_env_confluence_pat(
    session_factory: async_sessionmaker, apply: bool, summary: dict[str, int],
) -> dict[str, Any] | None:
    """CONFLUENCE_PAT env → default-org 의 confluence source (정확히 1개) 에 attach.

    모호한 경우 (여러 source / 여러 org) 는 skip.
    """
    env_pat = os.getenv("CONFLUENCE_PAT", "").strip()
    if not env_pat:
        return None

    from src.auth.org_service import DEFAULT_ORG_ID

    async with session_factory() as session:
        result = await session.execute(
            select(DataSourceModel).where(
                DataSourceModel.organization_id == DEFAULT_ORG_ID,
                DataSourceModel.source_type.in_(("confluence", "wiki")),
                DataSourceModel.has_secret.is_(False),
            ),
        )
        candidates = list(result.scalars().all())

    info: dict[str, Any] = {
        "env_var": "CONFLUENCE_PAT",
        "candidate_count": len(candidates),
        "applied": False,
        "skip_reason": None,
    }

    if len(candidates) == 0:
        info["skip_reason"] = "default-org 에 confluence/wiki source 가 없음"
        return info
    if len(candidates) > 1:
        info["skip_reason"] = (
            f"default-org 에 confluence/wiki source 가 {len(candidates)}개 — "
            "모호해서 자동 attach 안 함. admin UI 에서 source 별 token 입력 권장."
        )
        return info

    target = candidates[0]
    info["target_source_id"] = target.id
    info["target_name"] = target.name
    if not apply:
        summary["would_migrate_env"] += 1
        return info

    box = get_secret_box()
    path = _secret_path(target.organization_id, target.id)
    try:
        await box.put(path, env_pat)
    except SecretBoxError as e:
        info["error"] = f"SecretBox.put failed: {e}"
        summary["errors"] += 1
        return info

    async with session_factory() as session:
        try:
            await session.execute(
                update(DataSourceModel)
                .where(DataSourceModel.id == target.id)
                .values(secret_path=path, has_secret=True),
            )
            await session.commit()
            info["applied"] = True
            summary["migrated_env"] += 1
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            info["error"] = f"DB update failed: {e}"
            summary["errors"] += 1

    return info


async def _run(apply: bool, skip_env: bool) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    summary = {
        "would_migrate_db": 0,
        "migrated_db": 0,
        "would_migrate_env": 0,
        "migrated_env": 0,
        "errors": 0,
    }

    try:
        # SecretBox 활성화 검증 — fail-closed.
        try:
            get_secret_box()
        except SecretBoxError as e:
            print(f"❌ SecretBox 활성화 실패: {e}", file=sys.stderr)
            return 2

        print("=" * 60)
        print(f"data_sources secret 마이그레이션 — {'APPLY' if apply else 'DRY-RUN'}")
        print("=" * 60)

        # 1) DB 평문 token 이동
        db_actions = await _migrate_db_plaintext(session_factory, apply, summary)
        if db_actions:
            print(f"\n📦 DB 평문 token ({len(db_actions)}건):")
            for a in db_actions:
                marker = (
                    "✅" if a["applied"]
                    else "❌" if a["error"]
                    else "📋"  # dry-run
                )
                print(
                    f"  {marker} [{a['organization_id']}] {a['name']} "
                    f"({a['source_type']}, key={a['from_key']})",
                )
                if a["error"]:
                    print(f"      └─ {a['error']}")

        # 2) env CONFLUENCE_PAT 흡수
        if not skip_env:
            env_info = await _migrate_env_confluence_pat(
                session_factory, apply, summary,
            )
            if env_info:
                print("\n🔐 환경변수 CONFLUENCE_PAT:")
                if env_info["applied"]:
                    print(
                        f"  ✅ → {env_info['target_name']} ({env_info['target_source_id']})",
                    )
                elif env_info.get("error"):
                    print(f"  ❌ {env_info['error']}")
                elif env_info.get("skip_reason"):
                    print(f"  ⏭️  {env_info['skip_reason']}")
                else:
                    print(
                        f"  📋 → {env_info['target_name']} (apply 시 attach)",
                    )

        # Summary
        print("\n" + "=" * 60)
        print("Summary:")
        for k, v in summary.items():
            print(f"  {k}: {v}")

        if not apply and (
            summary["would_migrate_db"] > 0 or summary["would_migrate_env"] > 0
        ):
            print("\n💡 위 변경사항을 실제로 적용하려면 ``--apply`` 옵션 추가.")

        return 0 if summary["errors"] == 0 else 1
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="실제로 변경 적용 (없으면 dry-run)",
    )
    parser.add_argument(
        "--skip-env", action="store_true",
        help="환경변수 (CONFLUENCE_PAT) 흡수 skip — DB 평문만 이동",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="상세 로그",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    return asyncio.run(_run(apply=args.apply, skip_env=args.skip_env))


if __name__ == "__main__":
    raise SystemExit(main())
