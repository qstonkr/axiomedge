"""Lightweight feature-flag layer (PR-11 N).

Scope precedence: ``kb:<id>`` > ``org:<id>`` > ``_global`` > default.
ENV override (``FF_<NAME>=true``) 는 모든 DB 조회보다 우선 — 긴급/테스트용.

Cache: 60초 TTL in-memory, asyncio.Lock 으로 thundering-herd 방지. 새로 작성된
flag 가 60초 이내 자동 갱신되도록 정상적으로 작동.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 60.0
_GLOBAL_SCOPE = "_global"


class FeatureFlagCache:
    """TTL cache for feature flags."""

    def __init__(self, *, ttl_seconds: float = _DEFAULT_TTL) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[tuple[str, str], tuple[bool, dict[str, Any], float]] = {}
        self._lock = asyncio.Lock()

    def _is_fresh(self, fetched_at: float) -> bool:
        return (time.time() - fetched_at) < self._ttl

    async def get(
        self,
        name: str,
        scope: str = _GLOBAL_SCOPE,
        *,
        loader,
    ) -> tuple[bool, dict[str, Any]]:
        """Return ``(enabled, payload)`` for a (name, scope), refreshing on miss.

        ``loader`` is awaited only on cache miss/stale and yields the raw row
        dict ``{"enabled": bool, "payload": dict}`` or ``None`` for absent.
        """
        key = (name, scope)
        async with self._lock:
            entry = self._cache.get(key)
            if entry and self._is_fresh(entry[2]):
                return entry[0], entry[1]

            row = await loader(name, scope)
            if row is None:
                enabled, payload = False, {}
            else:
                enabled = bool(row.get("enabled", False))
                payload = dict(row.get("payload") or {})
            self._cache[key] = (enabled, payload, time.time())
            return enabled, payload

    def invalidate(self, name: str | None = None, scope: str | None = None) -> None:
        """Remove cached entries — full reset when both args are None."""
        if name is None and scope is None:
            self._cache.clear()
            return
        keys = [
            k for k in self._cache
            if (name is None or k[0] == name)
            and (scope is None or k[1] == scope)
        ]
        for k in keys:
            self._cache.pop(k, None)


_cache: FeatureFlagCache = FeatureFlagCache()


def reset_cache_for_testing() -> None:
    """Test helper — invalidate the singleton cache."""
    _cache.invalidate()


def _env_override(name: str) -> bool | None:
    """Return ENV override (``FF_<NAME>``) or None if unset.

    **Production hardening (P0-4)**:
    - In ``APP_ENV=production`` the env override is rejected unless ``name``
      appears in the comma-separated allowlist ``FF_ALLOW_ENV_OVERRIDE``.
    - This prevents an attacker who can edit ConfigMap/env from bypassing
      the audit-logged admin UI kill switch on dangerous flags
      (``ENABLE_GRAPHRAG_BATCH_NEO4J``, ``ENABLE_INGESTION_FILE_PARALLEL``,
      etc.).
    - Non-production environments (dev/staging) keep the override active for
      debugging convenience.
    """
    raw = os.getenv(f"FF_{name}")
    if raw is None:
        return None

    if os.getenv("APP_ENV", "").lower() == "production":
        allowlist_raw = os.getenv("FF_ALLOW_ENV_OVERRIDE", "")
        allowlist = {
            x.strip() for x in allowlist_raw.split(",") if x.strip()
        }
        if name not in allowlist:
            logger.warning(
                "FF_%s env override IGNORED in production "
                "(not in FF_ALLOW_ENV_OVERRIDE). Use admin UI to toggle.",
                name,
            )
            return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


async def _load_from_db(name: str, scope: str) -> dict[str, Any] | None:
    """Default loader — reads from FeatureFlagRepository.

    Returns None if DB unavailable or flag absent.
    """
    try:
        from src.stores.postgres.session import get_knowledge_session_maker
        from src.stores.postgres.repositories.feature_flags import (
            FeatureFlagRepository,
        )
    except ImportError:
        return None
    session_maker = get_knowledge_session_maker()
    if session_maker is None:
        return None
    repo = FeatureFlagRepository(session_maker)
    try:
        return await repo.get(name=name, scope=scope)
    except (RuntimeError, OSError, AttributeError) as e:
        logger.warning("FeatureFlag DB load failed (%s/%s): %s", name, scope, e)
        return None


async def get_flag(
    name: str,
    *,
    kb_id: str | None = None,
    org_id: str | None = None,
    default: bool = False,
    loader=None,
) -> bool:
    """Resolve flag with precedence kb > org > global > default. ENV wins."""
    env = _env_override(name)
    if env is not None:
        return env

    fn = loader or _load_from_db

    if kb_id:
        enabled, _ = await _cache.get(name, f"kb:{kb_id}", loader=fn)
        if enabled:
            return True
    if org_id:
        enabled, _ = await _cache.get(name, f"org:{org_id}", loader=fn)
        if enabled:
            return True

    enabled, _ = await _cache.get(name, _GLOBAL_SCOPE, loader=fn)
    return enabled if enabled else default


async def get_flag_payload(
    name: str,
    *,
    kb_id: str | None = None,
    org_id: str | None = None,
    loader=None,
) -> dict[str, Any]:
    """Return payload of the most-specific scope that is enabled."""
    fn = loader or _load_from_db
    candidates: list[str] = []
    if kb_id:
        candidates.append(f"kb:{kb_id}")
    if org_id:
        candidates.append(f"org:{org_id}")
    candidates.append(_GLOBAL_SCOPE)
    for sc in candidates:
        enabled, payload = await _cache.get(name, sc, loader=fn)
        if enabled:
            return payload
    return {}


__all__ = [
    "FeatureFlagCache",
    "get_flag",
    "get_flag_payload",
    "reset_cache_for_testing",
]
