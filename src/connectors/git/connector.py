"""GitConnector implements the IKnowledgeConnector protocol for git repos."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.core.models import ConnectorResult, RawDocument
from src.pipelines.document_parser import parse_file
from .client import GitClient, GitCommandError
from .config import GitConnectorConfig
from .frontmatter import parse_frontmatter, promote_legal_metadata

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "git:"


class GitConnector:
    """Clone/pull a git repo, filter files, and emit RawDocuments.

    Change detection uses the commit SHA as the version fingerprint:
        fingerprint = f"git:{current_sha}"
    When `last_fingerprint` matches, fetch still runs `git pull` and returns
    an empty document list with `skipped=True`. When only a subset of files
    changed, the connector still emits all matching files (the ingestion
    pipeline deduplicates via content_hash) — but filters via `changed_only`
    can be enabled per config in future revisions.
    """

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "git"

    async def health_check(self) -> bool:
        await asyncio.sleep(0)
        return True

    async def fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> ConnectorResult:
        try:
            cfg = GitConnectorConfig.from_source({"crawl_config": config, **config})
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        client = GitClient(cfg.workdir)
        try:
            await client.ensure_repo(
                cfg.repo_url, branch=cfg.branch, auth_token=cfg.auth_token,
            )
        except GitCommandError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type,
                error=f"git clone/fetch failed: {exc}",
                metadata={"repo_url": cfg.repo_url, "workdir": str(cfg.workdir)},
            )

        current_sha = await client.current_commit()
        fingerprint = f"{_FINGERPRINT_PREFIX}{current_sha}"

        if not force and last_fingerprint == fingerprint:
            return ConnectorResult(
                success=True, source_type=self.source_type, documents=[],
                version_fingerprint=fingerprint,
                metadata={
                    "skipped": True, "reason": "No new commits",
                    "commit_sha": current_sha,
                    "repo_url": cfg.repo_url,
                },
            )

        root = cfg.workdir / cfg.subdir if cfg.subdir else cfg.workdir
        if not root.exists():
            return ConnectorResult(
                success=False, source_type=self.source_type,
                error=f"subdir '{cfg.subdir}' not found in repo",
                metadata={"repo_url": cfg.repo_url, "commit_sha": current_sha},
            )

        matched_files = await asyncio.to_thread(
            _walk_repo, cfg.workdir, root,
            cfg.include_globs, cfg.exclude_globs, cfg.max_file_size,
        )
        logger.info(
            "git connector: %d files matched in %s (%s)",
            len(matched_files), cfg.repo_url, current_sha[:8],
        )

        documents, empty_count, oversized_count = await self._build_documents(
            client, cfg, matched_files, current_sha,
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "repo_url": cfg.repo_url,
                "branch": cfg.branch,
                "commit_sha": current_sha,
                "files_matched": len(matched_files),
                "documents_emitted": len(documents),
                "files_empty": empty_count,
                "files_oversized": oversized_count,
                "workdir": str(cfg.workdir),
            },
        )

    async def lazy_fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> AsyncIterator[RawDocument]:
        result = await self.fetch(config, force=force, last_fingerprint=last_fingerprint)
        if not result.success or result.skipped:
            return
        for doc in result.documents:
            yield doc

    async def _build_documents(
        self,
        client: GitClient,
        cfg: GitConnectorConfig,
        matched_files: list[Path],
        current_sha: str,
    ) -> tuple[list[RawDocument], int, int]:
        documents: list[RawDocument] = []
        empty_count = 0
        oversized_count = 0

        # Single bulk git log → {rel_path: (sha, date, author)}. Replaces
        # ~N sequential subprocess calls (previously the dominant cost
        # for large repos like legalize-kr with 6,907 tracked files).
        commits_map = await client.file_last_commits_map()
        logger.info(
            "git connector: commit map built for %d tracked paths",
            len(commits_map),
        )

        for abs_path in matched_files:
            try:
                rel_path = abs_path.relative_to(cfg.workdir).as_posix()
            except ValueError:
                continue

            try:
                size = abs_path.stat().st_size
            except OSError:
                continue
            if size > cfg.max_file_size:
                oversized_count += 1
                continue

            try:
                text = await asyncio.to_thread(parse_file, abs_path)
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                logger.warning("parse_file failed for %s: %s", rel_path, exc)
                continue

            raw_content = (text or "").strip()
            if not raw_content:
                empty_count += 1
                continue

            # Strip YAML frontmatter for markdown files and promote its fields
            # to metadata so structured keys (공포일자, 소관부처, 법령ID …)
            # become payload filters instead of embedding noise.
            frontmatter: dict[str, Any] = {}
            body = raw_content
            if abs_path.suffix.lower() in (".md", ".markdown"):
                frontmatter, body = parse_frontmatter(raw_content)
                body = body.strip()

            if not body:
                empty_count += 1
                continue

            commit_sha, commit_date, author = commits_map.get(
                rel_path, (current_sha, "", ""),
            )
            updated_at = _parse_iso_date(commit_date)

            doc_id = f"git:{_repo_slug(cfg.repo_url)}:{rel_path}"
            metadata: dict[str, Any] = {
                "source_type": self.source_type,
                "repo_url": cfg.repo_url,
                "branch": cfg.branch,
                "file_path": rel_path,
                "file_ext": abs_path.suffix.lower(),
                "file_size_bytes": size,
                "commit_sha": commit_sha or current_sha,
                "commit_date": commit_date,
                "knowledge_type": cfg.name,
            }

            legal_meta = promote_legal_metadata(frontmatter)
            if legal_meta:
                metadata.update(legal_meta)
                parent_law = _extract_parent_law_slug(rel_path, cfg.subdir)
                if parent_law:
                    metadata["parent_law_slug"] = parent_law
                metadata["law_file_kind"] = _law_file_kind(abs_path.stem)

            author = author or metadata.get("ministry", "")

            documents.append(
                RawDocument(
                    doc_id=doc_id,
                    title=str(metadata.get("law_name") or abs_path.stem or rel_path),
                    content=body,
                    source_uri=_build_source_uri(cfg.repo_url, rel_path, commit_sha or current_sha),
                    author=author,
                    updated_at=updated_at,
                    content_hash=RawDocument.sha256(body),
                    metadata=metadata,
                )
            )

        return documents, empty_count, oversized_count


def _walk_repo(
    repo_root: Path,
    scan_root: Path,
    include_globs: Iterable[str],
    exclude_globs: Iterable[str],
    max_file_size: int,
) -> list[Path]:
    """Return files under `scan_root` matching include/exclude globs."""
    includes = tuple(include_globs)
    excludes = tuple(exclude_globs)
    out: list[Path] = []

    for path in scan_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:
            continue
        if rel.startswith(".git/") or rel == ".git":
            continue
        if any(_glob_match(rel, pat) for pat in excludes):
            continue
        if includes and not any(_glob_match(rel, pat) for pat in includes):
            continue
        try:
            if path.stat().st_size > max_file_size:
                continue
        except OSError:
            continue
        out.append(path)

    out.sort()
    return out


def _glob_match(path: str, pattern: str) -> bool:
    """Glob match with full ** support (matches across path separators)."""
    return _compile_glob(pattern).match(path) is not None


@lru_cache(maxsize=256)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                parts.append(".*")
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
            else:
                parts.append("[^/]*")
                i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(c))
            i += 1
    return re.compile(f"^{''.join(parts)}$")


def _repo_slug(repo_url: str) -> str:
    stripped = repo_url.rstrip("/")
    if stripped.endswith(".git"):
        stripped = stripped[:-4]
    return stripped.rsplit("/", 1)[-1] or stripped


def _build_source_uri(repo_url: str, rel_path: str, sha: str) -> str:
    base = repo_url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    if base.startswith("http") and "github.com" in base and sha:
        return f"{base}/blob/{sha}/{rel_path}"
    return f"{base}#{rel_path}"


def _extract_parent_law_slug(rel_path: str, subdir: str) -> str:
    """Return the immediate parent directory name (= law slug) of a legal file.

    For ``kr/119구조ㆍ구급에관한법률/시행령.md`` returns ``119구조ㆍ구급에관한법률``.
    Strips the crawl subdir prefix so grouping is stable across configs.
    """
    path = rel_path
    if subdir:
        prefix = f"{subdir.strip('/')}/"
        if path.startswith(prefix):
            path = path[len(prefix):]
    parts = path.split("/")
    if len(parts) >= 2:
        return parts[-2]
    return ""


def _law_file_kind(stem: str) -> str:
    """Classify a legal MD file by its filename stem.

    Legalize-kr uses these well-known stems:
        법률, 시행령, 시행규칙, 시행규칙(총리령), 시행규칙(행정안전부령), …
    """
    if not stem:
        return "unknown"
    if stem.startswith("법률"):
        return "law"
    if stem.startswith("시행령"):
        return "decree"
    if stem.startswith("시행규칙"):
        return "rule"
    return "other"


def _parse_iso_date(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
