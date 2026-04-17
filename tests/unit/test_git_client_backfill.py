"""Unit tests for src/connectors/git/client.py — backfill coverage.

Covers GitClient async methods, _inject_token, _redact, and error paths.
All subprocess calls are mocked — no real git operations.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.connectors.git.client import (
    GitClient,
    GitCommandError,
    _inject_token,
    _redact,
)


# -------------------------------------------------------------------
# _inject_token
# -------------------------------------------------------------------


class TestInjectToken:
    """Token injection into repo URLs."""

    def test_no_token_returns_original(self) -> None:
        url = "https://github.com/org/repo.git"
        assert _inject_token(url, "") == url

    def test_https_url_gets_token(self) -> None:
        url = "https://github.com/org/repo.git"
        result = _inject_token(url, "ghp_abc123")
        assert "x-access-token:ghp_abc123@github.com" in result
        assert result.startswith("https://")

    def test_http_url_gets_token(self) -> None:
        url = "http://github.com/org/repo.git"
        result = _inject_token(url, "tok")
        assert "x-access-token:tok@github.com" in result

    def test_ssh_url_unchanged(self) -> None:
        url = "git@github.com:org/repo.git"
        assert _inject_token(url, "tok") == url

    def test_local_path_unchanged(self) -> None:
        url = "/tmp/local-repo"
        assert _inject_token(url, "tok") == url

    def test_url_with_port(self) -> None:
        url = "https://git.example.com:8443/org/repo.git"
        result = _inject_token(url, "tok")
        assert ":8443" in result
        assert "x-access-token:tok@" in result


# -------------------------------------------------------------------
# _redact
# -------------------------------------------------------------------


class TestRedact:
    """URL credential redaction."""

    def test_no_credentials_unchanged(self) -> None:
        url = "https://github.com/org/repo.git"
        assert _redact(url) == url

    def test_credentials_redacted(self) -> None:
        url = "https://x-access-token:secret@github.com/org/repo.git"
        result = _redact(url)
        assert "secret" not in result
        assert "github.com" in result

    def test_ssh_url_unchanged(self) -> None:
        url = "git@github.com:org/repo.git"
        assert _redact(url) == url

    def test_url_with_port_redacted(self) -> None:
        url = "https://user:pass@git.example.com:8443/repo.git"
        result = _redact(url)
        assert "pass" not in result
        assert "8443" in result


# -------------------------------------------------------------------
# GitClient — construction
# -------------------------------------------------------------------


class TestGitClientInit:
    def test_workdir_property(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        assert client.workdir == tmp_path

    def test_custom_git_binary(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path, git_binary="/usr/local/bin/git")
        assert client._git == "/usr/local/bin/git"


# -------------------------------------------------------------------
# GitClient._run (the subprocess wrapper)
# -------------------------------------------------------------------


class TestGitClientRun:
    """Test the internal _run helper."""

    @pytest.mark.asyncio
    async def test_run_success(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b"abc123\n",
            b"",
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await client._run("rev-parse", "HEAD")
            assert result == "abc123\n"

    @pytest.mark.asyncio
    async def test_run_failure_raises(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"fatal: not a repo\n")
        mock_proc.returncode = 128

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(GitCommandError, match="failed.*exit 128"):
                await client._run("rev-parse", "HEAD")

    @pytest.mark.asyncio
    async def test_run_with_cwd_none(self, tmp_path: Path) -> None:
        """When cwd=None (clone), no cwd should be passed."""
        client = GitClient(tmp_path)
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok\n", b"")
        mock_proc.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            await client._run("clone", "url", cwd=None)
            call_kwargs = mock_exec.call_args
            assert call_kwargs.kwargs.get("cwd") is None


# -------------------------------------------------------------------
# GitClient.current_commit
# -------------------------------------------------------------------


class TestCurrentCommit:
    @pytest.mark.asyncio
    async def test_returns_stripped_sha(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        with patch.object(
            client, "_run", return_value="abc123def456\n"
        ) as mock_run:
            sha = await client.current_commit()
            assert sha == "abc123def456"
            mock_run.assert_awaited_once_with("rev-parse", "HEAD")


# -------------------------------------------------------------------
# GitClient.changed_paths
# -------------------------------------------------------------------


class TestChangedPaths:
    @pytest.mark.asyncio
    async def test_empty_from_sha(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        changed, deleted = await client.changed_paths("")
        assert changed == []
        assert deleted == []

    @pytest.mark.asyncio
    async def test_added_modified_deleted(self, tmp_path: Path) -> None:
        diff_output = (
            "A\tnew_file.py\n"
            "M\tmodified.py\n"
            "D\tremoved.py\n"
        )
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=diff_output):
            changed, deleted = await client.changed_paths("abc123")
            assert "new_file.py" in changed
            assert "modified.py" in changed
            assert "removed.py" in deleted

    @pytest.mark.asyncio
    async def test_rename_tracked(self, tmp_path: Path) -> None:
        diff_output = "R100\told_name.py\tnew_name.py\n"
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=diff_output):
            changed, deleted = await client.changed_paths("abc123")
            assert "new_name.py" in changed
            assert "old_name.py" in deleted

    @pytest.mark.asyncio
    async def test_git_error_returns_empty(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        with patch.object(
            client, "_run", side_effect=GitCommandError("bad ref")
        ):
            changed, deleted = await client.changed_paths("badsha")
            assert changed == []
            assert deleted == []

    @pytest.mark.asyncio
    async def test_malformed_line_skipped(self, tmp_path: Path) -> None:
        diff_output = "M\tgood.py\nbadline\nA\tok.py\n"
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=diff_output):
            changed, deleted = await client.changed_paths("abc123")
            assert "good.py" in changed
            assert "ok.py" in changed
            assert len(deleted) == 0


# -------------------------------------------------------------------
# GitClient.file_last_commit
# -------------------------------------------------------------------


class TestFileLastCommit:
    @pytest.mark.asyncio
    async def test_normal(self, tmp_path: Path) -> None:
        output = "abc123\x1f2025-01-01T00:00:00+09:00\x1fAuthor Name"
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=output):
            sha, date, author = await client.file_last_commit("file.py")
            assert sha == "abc123"
            assert "2025" in date
            assert author == "Author Name"

    @pytest.mark.asyncio
    async def test_empty_output(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=""):
            sha, date, author = await client.file_last_commit("missing.py")
            assert sha == ""
            assert date == ""
            assert author == ""

    @pytest.mark.asyncio
    async def test_git_error(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        with patch.object(
            client, "_run", side_effect=GitCommandError("err")
        ):
            sha, date, author = await client.file_last_commit("bad.py")
            assert sha == ""

    @pytest.mark.asyncio
    async def test_partial_fields_padded(self, tmp_path: Path) -> None:
        """If fewer than 3 fields, missing ones should be empty strings."""
        output = "abc123\x1f2025-01-01"
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=output):
            sha, date, author = await client.file_last_commit("f.py")
            assert sha == "abc123"
            assert date == "2025-01-01"
            assert author == ""


# -------------------------------------------------------------------
# GitClient.file_last_commits_map
# -------------------------------------------------------------------


class TestFileLastCommitsMap:
    @pytest.mark.asyncio
    async def test_parses_bulk_output(self, tmp_path: Path) -> None:
        # Simulate git log output with SOH delimiter
        block1 = "\x01sha1\x1f2025-01-01\x1fAlice\nfile_a.py\nfile_b.py\n"
        block2 = "\x01sha2\x1f2025-01-02\x1fBob\nfile_c.py\nfile_a.py\n"
        output = block1 + block2
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=output):
            result = await client.file_last_commits_map()
            # file_a.py appears in both blocks — first (sha1) wins
            assert result["file_a.py"][0] == "sha1"
            assert result["file_b.py"][0] == "sha1"
            assert result["file_c.py"][0] == "sha2"

    @pytest.mark.asyncio
    async def test_empty_output(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=""):
            result = await client.file_last_commits_map()
            assert result == {}

    @pytest.mark.asyncio
    async def test_git_error_returns_empty(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        with patch.object(
            client, "_run", side_effect=GitCommandError("err")
        ):
            result = await client.file_last_commits_map()
            assert result == {}

    @pytest.mark.asyncio
    async def test_header_only_block_skipped(self, tmp_path: Path) -> None:
        """Block with header but no file list."""
        output = "\x01sha1\x1f2025-01-01\x1fAlice"
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=output):
            result = await client.file_last_commits_map()
            assert result == {}

    @pytest.mark.asyncio
    async def test_short_header_skipped(self, tmp_path: Path) -> None:
        """Block with fewer than 3 header fields is skipped."""
        output = "\x01sha_only\nfile.py\n"
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=output):
            result = await client.file_last_commits_map()
            assert result == {}


# -------------------------------------------------------------------
# GitClient.ensure_repo
# -------------------------------------------------------------------


class TestEnsureRepo:
    @pytest.mark.asyncio
    async def test_clone_when_no_repo(self, tmp_path: Path) -> None:
        workdir = tmp_path / "repo"
        client = GitClient(workdir)
        with (
            patch.object(
                client, "_is_existing_repo", return_value=False
            ),
            patch.object(client, "_clone", new_callable=AsyncMock) as m,
        ):
            await client.ensure_repo("https://example.com/repo.git")
            m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_when_existing(self, tmp_path: Path) -> None:
        workdir = tmp_path / "repo"
        client = GitClient(workdir)
        with (
            patch.object(
                client, "_is_existing_repo", return_value=True
            ),
            patch.object(
                client,
                "_fetch_and_reset",
                new_callable=AsyncMock,
            ) as m,
        ):
            await client.ensure_repo("https://example.com/repo.git")
            m.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_token_injected(self, tmp_path: Path) -> None:
        workdir = tmp_path / "repo"
        client = GitClient(workdir)
        with (
            patch.object(
                client, "_is_existing_repo", return_value=False
            ),
            patch.object(client, "_clone", new_callable=AsyncMock) as m,
        ):
            await client.ensure_repo(
                "https://example.com/repo.git",
                auth_token="tok123",
            )
            called_url = m.call_args.args[0]
            assert "tok123" in called_url


# -------------------------------------------------------------------
# GitClient._clone
# -------------------------------------------------------------------


class TestClone:
    @pytest.mark.asyncio
    async def test_clone_basic(self, tmp_path: Path) -> None:
        workdir = tmp_path / "repo"
        client = GitClient(workdir)
        with patch.object(client, "_run", new_callable=AsyncMock) as m:
            await client._clone(
                "https://example.com/r.git", branch="", depth=None
            )
            args = m.call_args.args
            assert "clone" in args

    @pytest.mark.asyncio
    async def test_clone_with_branch_and_depth(
        self, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "repo"
        client = GitClient(workdir)
        with patch.object(client, "_run", new_callable=AsyncMock) as m:
            await client._clone(
                "https://example.com/r.git", branch="dev", depth=1
            )
            args = m.call_args.args
            assert "--branch" in args
            assert "dev" in args
            assert "--depth" in args
            assert "1" in args

    @pytest.mark.asyncio
    async def test_clone_removes_existing_dir(
        self, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "repo"
        workdir.mkdir()
        (workdir / "stale.txt").write_text("old")
        client = GitClient(workdir)
        with patch.object(client, "_run", new_callable=AsyncMock):
            await client._clone(
                "https://example.com/r.git", branch="", depth=None
            )
            # shutil.rmtree should have removed old contents
            assert not (workdir / "stale.txt").exists()


# -------------------------------------------------------------------
# GitClient._fetch_and_reset
# -------------------------------------------------------------------


class TestFetchAndReset:
    @pytest.mark.asyncio
    async def test_with_branch(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        calls: list[tuple] = []

        async def fake_run(*args, **kw):
            calls.append(args)
            return ""

        with patch.object(client, "_run", side_effect=fake_run):
            await client._fetch_and_reset(
                "https://example.com/r.git", branch="dev"
            )
            # Should set-url, fetch, checkout dev, reset --hard
            assert len(calls) == 4
            assert "dev" in calls[2]
            assert "origin/dev" in calls[3]

    @pytest.mark.asyncio
    async def test_without_branch_uses_default(
        self, tmp_path: Path
    ) -> None:
        client = GitClient(tmp_path)
        with (
            patch.object(
                client,
                "_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
            patch.object(
                client, "_run", new_callable=AsyncMock, return_value=""
            ) as mock_run,
        ):
            await client._fetch_and_reset(
                "https://example.com/r.git", branch=""
            )
            # Should have called checkout main
            checkout_call = [
                c for c in mock_run.call_args_list
                if "checkout" in c.args
            ]
            assert len(checkout_call) >= 1


# -------------------------------------------------------------------
# GitClient._default_branch
# -------------------------------------------------------------------


class TestDefaultBranch:
    @pytest.mark.asyncio
    async def test_parses_ref(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        with patch.object(
            client,
            "_run",
            return_value="refs/remotes/origin/develop\n",
        ):
            branch = await client._default_branch()
            assert branch == "develop"

    @pytest.mark.asyncio
    async def test_error_returns_main(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        with patch.object(
            client, "_run", side_effect=GitCommandError("err")
        ):
            branch = await client._default_branch()
            assert branch == "main"

    @pytest.mark.asyncio
    async def test_empty_ref_returns_main(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        with patch.object(client, "_run", return_value=""):
            branch = await client._default_branch()
            assert branch == "main"


# -------------------------------------------------------------------
# GitClient._is_existing_repo
# -------------------------------------------------------------------


class TestIsExistingRepo:
    def test_with_git_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        client = GitClient(tmp_path)
        assert client._is_existing_repo() is True

    def test_without_git_dir(self, tmp_path: Path) -> None:
        client = GitClient(tmp_path)
        assert client._is_existing_repo() is False


# -------------------------------------------------------------------
# GitCommandError
# -------------------------------------------------------------------


class TestGitCommandError:
    def test_is_runtime_error(self) -> None:
        err = GitCommandError("failed")
        assert isinstance(err, RuntimeError)
        assert str(err) == "failed"
