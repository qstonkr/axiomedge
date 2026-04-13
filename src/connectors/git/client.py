"""Thin async wrapper around the local `git` CLI."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


class GitCommandError(RuntimeError):
    """Raised when a git subprocess exits non-zero."""


class GitClient:
    """Run git commands against a working copy on disk."""

    def __init__(self, workdir: Path, *, git_binary: str = "git") -> None:
        self._workdir = workdir
        self._git = git_binary

    @property
    def workdir(self) -> Path:
        return self._workdir

    async def ensure_repo(
        self,
        repo_url: str,
        *,
        branch: str = "",
        auth_token: str = "",
        depth: int | None = None,
    ) -> None:
        """Clone fresh if missing, otherwise fetch + reset to remote head."""
        effective_url = _inject_token(repo_url, auth_token)
        if self._is_existing_repo():
            await self._fetch_and_reset(effective_url, branch=branch)
            return
        await self._clone(effective_url, branch=branch, depth=depth)

    async def current_commit(self) -> str:
        stdout = await self._run("rev-parse", "HEAD")
        return stdout.strip()

    async def changed_paths(self, from_sha: str) -> tuple[list[str], list[str]]:
        """Return (changed, deleted) paths between from_sha and HEAD.

        Changed includes added, modified, renamed (new name only).
        """
        if not from_sha:
            return [], []
        try:
            stdout = await self._run(
                "diff", "--name-status", f"{from_sha}..HEAD",
            )
        except GitCommandError as exc:
            logger.warning("git diff %s..HEAD failed: %s", from_sha, exc)
            return [], []

        changed: list[str] = []
        deleted: list[str] = []
        for line in stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0].strip()
            if status.startswith("D"):
                deleted.append(parts[1])
            elif status.startswith("R") and len(parts) >= 3:
                changed.append(parts[2])
                deleted.append(parts[1])
            else:
                changed.append(parts[1])
        return changed, deleted

    async def file_last_commit(self, rel_path: str) -> tuple[str, str, str]:
        """Return (sha, iso_date, author) for the latest commit touching rel_path.

        Single-file convenience. For bulk workloads call
        :meth:`file_last_commits_map` — one ``git log`` invocation is several
        orders of magnitude faster than N subprocess calls.
        """
        try:
            stdout = await self._run(
                "log", "-1", "--format=%H%x1f%cI%x1f%an", "--", rel_path,
            )
        except GitCommandError:
            return "", "", ""
        line = stdout.strip()
        if not line:
            return "", "", ""
        parts = line.split("\x1f")
        while len(parts) < 3:
            parts.append("")
        return parts[0], parts[1], parts[2]

    async def file_last_commits_map(self) -> dict[str, tuple[str, str, str]]:
        """Build a ``{rel_path: (sha, iso_date, author)}`` map in one pass.

        Runs a single ``git log --name-only HEAD`` and parses its output:
        commits are emitted newest-first, so the first commit we see for
        any given path IS that path's latest touch. For a 6,907-file repo
        this replaces ~6,907 individual subprocess invocations (minutes)
        with a single stream parse (well under a second).

        Rename/copy events are followed via ``-M`` / ``-C``. Korean paths
        are kept raw (no octal-quoting) via ``-c core.quotepath=false``.
        """
        # Each commit record starts with \x01 (ASCII SOH — safe delimiter
        # because git output cannot contain control characters outside
        # format strings). Field separator inside the header is \x1f.
        try:
            stdout = await self._run(
                "-c", "core.quotepath=false",
                "log",
                "-M", "-C",
                "--format=%x01%H%x1f%cI%x1f%an",
                "--name-only",
                "HEAD",
            )
        except GitCommandError as exc:
            logger.warning("git log bulk fetch failed: %s", exc)
            return {}

        result: dict[str, tuple[str, str, str]] = {}
        if not stdout:
            return result

        for block in stdout.split("\x01"):
            if not block:
                continue
            newline_idx = block.find("\n")
            if newline_idx == -1:
                header = block
                files_blob = ""
            else:
                header = block[:newline_idx]
                files_blob = block[newline_idx + 1:]

            header_parts = header.split("\x1f")
            if len(header_parts) < 3:
                continue
            sha, date, author = header_parts[0], header_parts[1], header_parts[2]

            for raw_path in files_blob.split("\n"):
                path = raw_path.strip()
                if not path:
                    continue
                if path not in result:
                    result[path] = (sha, date, author)

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_existing_repo(self) -> bool:
        return (self._workdir / ".git").is_dir()

    async def _clone(self, effective_url: str, *, branch: str, depth: int | None) -> None:
        self._workdir.parent.mkdir(parents=True, exist_ok=True)
        if self._workdir.exists():
            shutil.rmtree(self._workdir)
        args: list[str] = ["clone"]
        if depth and depth > 0:
            args += ["--depth", str(depth)]
        if branch:
            args += ["--branch", branch]
        args += [effective_url, str(self._workdir)]
        logger.info("git clone %s → %s", _redact(effective_url), self._workdir)
        await self._run(*args, cwd=None)

    async def _fetch_and_reset(self, effective_url: str, *, branch: str) -> None:
        await self._run("remote", "set-url", "origin", effective_url)
        await self._run("fetch", "--prune", "origin")
        target = branch or (await self._default_branch())
        await self._run("checkout", target)
        await self._run("reset", "--hard", f"origin/{target}")

    async def _default_branch(self) -> str:
        try:
            stdout = await self._run(
                "symbolic-ref", "refs/remotes/origin/HEAD",
            )
        except GitCommandError:
            return "main"
        ref = stdout.strip()
        return ref.rsplit("/", 1)[-1] if ref else "main"

    async def _run(self, *args: str, cwd: Path | None = ...) -> str:
        effective_cwd = self._workdir if cwd is ... else cwd
        cmd = [self._git, *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(effective_cwd) if effective_cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        if proc.returncode != 0:
            err = stderr_b.decode("utf-8", "replace").strip()
            raise GitCommandError(
                f"git {' '.join(args[:2])} failed (exit {proc.returncode}): {err}"
            )
        return stdout_b.decode("utf-8", "replace")


def _inject_token(repo_url: str, token: str) -> str:
    """Embed a PAT into an https URL for non-interactive auth.

    Leaves SSH URLs and local paths untouched.
    """
    if not token:
        return repo_url
    parsed = urlparse(repo_url)
    if parsed.scheme not in ("http", "https"):
        return repo_url
    netloc = f"x-access-token:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _redact(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or "@" not in (parsed.netloc or ""):
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=host))
