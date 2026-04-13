"""Git connector configuration."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_INCLUDE = ("**/*.md",)
_DEFAULT_EXCLUDE = (".git/**", "node_modules/**", "**/.DS_Store")
_DEFAULT_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB per file


def _default_workdir_root() -> Path:
    return Path(
        os.getenv("GIT_CONNECTOR_WORKDIR", str(Path.home() / ".knowledge-local" / "git_repos"))
    )


@dataclass
class GitConnectorConfig:
    """Resolved configuration for a git-backed data source.

    Attributes:
        repo_url: Git remote URL (https://, git@, or local path).
        branch: Branch or tag to check out. Empty string = remote default.
        include_globs: Glob patterns to include, evaluated against repo-relative paths.
        exclude_globs: Glob patterns to exclude.
        subdir: Optional subdirectory inside the repo to crawl (e.g. "kr").
        max_file_size: Skip files larger than this (bytes).
        auth_token: HTTPS PAT for private repos (injected as x-access-token).
        workdir_root: Directory holding working copies, one per data source.
        workdir_slug: Unique slug identifying the working copy directory.
        name: Human readable source name used in document metadata.
    """

    repo_url: str
    branch: str = ""
    include_globs: tuple[str, ...] = _DEFAULT_INCLUDE
    exclude_globs: tuple[str, ...] = _DEFAULT_EXCLUDE
    subdir: str = ""
    max_file_size: int = _DEFAULT_MAX_FILE_SIZE
    auth_token: str = ""
    workdir_root: Path = field(default_factory=_default_workdir_root)
    workdir_slug: str = ""
    name: str = ""

    @property
    def workdir(self) -> Path:
        slug = self.workdir_slug or self._default_slug()
        return self.workdir_root / slug

    def _default_slug(self) -> str:
        base = re.sub(r"[^\w\-]+", "_", self.repo_url).strip("_")
        digest = hashlib.sha1(self.repo_url.encode("utf-8")).hexdigest()[:8]  # noqa: S324
        return f"{base[:40]}_{digest}" if base else digest

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> GitConnectorConfig:
        """Build a config from a data_source dict (crawl_config + metadata)."""
        crawl_cfg = source.get("crawl_config") or {}
        metadata = source.get("metadata") or {}

        repo_url = (
            crawl_cfg.get("repo_url")
            or metadata.get("repo_url")
            or metadata.get("url")
            or ""
        ).strip()
        if not repo_url:
            raise ValueError("git connector requires crawl_config.repo_url")

        def _as_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
            if value is None:
                return default
            if isinstance(value, str):
                return (value,) if value else default
            if isinstance(value, (list, tuple)):
                cleaned = tuple(str(v).strip() for v in value if str(v).strip())
                return cleaned or default
            return default

        token_env = str(crawl_cfg.get("auth_token_env") or "").strip()
        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token and token_env:
            token = os.getenv(token_env, "").strip()

        return cls(
            repo_url=repo_url,
            branch=str(crawl_cfg.get("branch") or "").strip(),
            include_globs=_as_tuple(crawl_cfg.get("include_globs"), _DEFAULT_INCLUDE),
            exclude_globs=_as_tuple(crawl_cfg.get("exclude_globs"), _DEFAULT_EXCLUDE),
            subdir=str(crawl_cfg.get("subdir") or "").strip().strip("/"),
            max_file_size=int(crawl_cfg.get("max_file_size") or _DEFAULT_MAX_FILE_SIZE),
            auth_token=token,
            workdir_slug=str(source.get("id") or "").strip(),
            name=str(source.get("name") or ""),
        )
