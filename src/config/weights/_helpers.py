"""Internal helpers for config_weights package."""

from __future__ import annotations

import os
from typing import Any


def _env_float(key: str, default: float) -> float:
    """Read float from env var with fallback."""
    raw = os.getenv(key, "")
    if raw:
        try:
            return float(raw)
        except ValueError:
            import logging
            logging.getLogger(__name__).warning(
                "Invalid float for %s=%r, using default %s", key, raw, default,
            )
    return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "")
    if raw:
        try:
            return int(raw)
        except ValueError:
            import logging
            logging.getLogger(__name__).warning(
                "Invalid int for %s=%r, using default %s", key, raw, default,
            )
    return default


def _coerce_value(value: Any, type_hint: str) -> Any:
    """Best-effort type coercion for JSON values to dataclass field types."""
    type_map: dict[str, type] = {
        "float": float,
        "int": int,
        "bool": bool,
        "str": str,
    }
    target = type_map.get(type_hint)
    if target is None:
        return value
    if target is bool and isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return target(value)
