"""Knowledge Dashboard Local Feature Flags.

All flags are enabled for local development.
"""

from __future__ import annotations

from functools import lru_cache


class FeatureFlags:
    """All features enabled for local development."""

    def __init__(self) -> None:
        self.chat_enabled: bool = True
        self.graph_enabled: bool = True
        self.operations_enabled: bool = True
        self.admin_enabled: bool = True
        self.auth_required: bool = False  # No auth for local
        self.metrics_enabled: bool = False  # No StatsD locally
        self.session_persistence_enabled: bool = False

    def __repr__(self) -> str:
        attrs = ", ".join(f"{k}={v}" for k, v in self.__dict__.items())
        return f"FeatureFlags({attrs})"


@lru_cache(maxsize=1)
def get_feature_flags() -> FeatureFlags:
    """Return the singleton FeatureFlags instance (cached)."""
    return FeatureFlags()


def is_enabled(flag_name: str) -> bool:
    """Check whether a flag is enabled. Almost always True for local."""
    flags = get_feature_flags()
    attr = flag_name if flag_name.endswith("_enabled") else f"{flag_name}_enabled"
    if flag_name == "auth_required":
        attr = "auth_required"
    return bool(getattr(flags, attr))
