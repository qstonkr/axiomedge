"""Tests for the git knowledge connector.

Uses real `git` CLI against a local bare repo in tmp_path — no network.
Tests are skipped automatically if `git` is not available on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.connectors.git import GitConnector, GitConnectorConfig
from src.connectors.git.connector import (
    _build_source_uri,
    _compile_glob,
    _glob_match,
    _repo_slug,
    _walk_repo,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git CLI not available"
)


# ---------------------------------------------------------------------------
# Glob helpers
# ---------------------------------------------------------------------------

class TestGlob:
    def test_double_star_matches_any_depth(self):
        assert _glob_match("kr/foo/bar.md", "**/*.md")
        assert _glob_match("README.md", "**/*.md")
        assert _glob_match("kr/a/b/c.md", "kr/**/*.md")

    def test_single_star_stops_at_separator(self):
        assert _glob_match("README.md", "*.md")
        assert not _glob_match("docs/README.md", "*.md")

    def test_non_matching(self):
        assert not _glob_match("kr/foo.txt", "**/*.md")
        assert not _glob_match("README.md", "kr/**/*.md")

    def test_exclude_dotdir(self):
        assert _glob_match(".git/config", ".git/**")
        assert not _glob_match("kr/foo.md", ".git/**")

    def test_compile_glob_is_cached(self):
        a = _compile_glob("**/*.md")
        b = _compile_glob("**/*.md")
        assert a is b


# ---------------------------------------------------------------------------
# _repo_slug, _build_source_uri
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_repo_slug_github_https(self):
        assert _repo_slug("https://github.com/foo/bar.git") == "bar"
        assert _repo_slug("https://github.com/foo/bar") == "bar"
        assert _repo_slug("https://github.com/foo/bar/") == "bar"

    def test_build_source_uri_github_blob(self):
        uri = _build_source_uri(
            "https://github.com/foo/bar.git", "docs/a.md", "abc123",
        )
        assert uri == "https://github.com/foo/bar/blob/abc123/docs/a.md"

    def test_build_source_uri_non_github(self):
        uri = _build_source_uri("git@gitlab.com:foo/bar.git", "a.md", "sha")
        assert uri.endswith("#a.md")


# ---------------------------------------------------------------------------
# _walk_repo
# ---------------------------------------------------------------------------

class TestWalkRepo:
    def test_include_exclude(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "kr" / "foo").mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / "kr" / "foo" / "law.md").write_text("# law")
        (repo / "kr" / "foo" / "law.txt").write_text("ignore")
        (repo / "README.md").write_text("# readme")
        (repo / ".git" / "config").write_text("x")

        files = _walk_repo(
            repo, repo,
            include_globs=("**/*.md",),
            exclude_globs=(".git/**", "README.md"),
            max_file_size=1024 * 1024,
        )
        rels = sorted(p.relative_to(repo).as_posix() for p in files)
        assert rels == ["kr/foo/law.md"]

    def test_max_file_size(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "big.md").write_text("x" * 500)
        (repo / "small.md").write_text("x")
        files = _walk_repo(
            repo, repo,
            include_globs=("**/*.md",),
            exclude_globs=(),
            max_file_size=100,
        )
        assert [p.name for p in files] == ["small.md"]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestGitConnectorConfig:
    def test_requires_repo_url(self):
        with pytest.raises(ValueError):
            GitConnectorConfig.from_source({"crawl_config": {}})

    def test_defaults(self):
        cfg = GitConnectorConfig.from_source({
            "id": "abc",
            "name": "legalize",
            "crawl_config": {"repo_url": "https://github.com/foo/bar"},
        })
        assert cfg.repo_url == "https://github.com/foo/bar"
        assert cfg.branch == ""
        assert cfg.include_globs == ("**/*.md",)
        assert cfg.workdir_slug == "abc"

    def test_custom_globs_and_subdir(self):
        cfg = GitConnectorConfig.from_source({
            "id": "x",
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "branch": "main",
                "include_globs": ["kr/**/*.md"],
                "exclude_globs": [".git/**", "README.md"],
                "subdir": "/kr/",
                "max_file_size": 1024,
            },
        })
        assert cfg.branch == "main"
        assert cfg.include_globs == ("kr/**/*.md",)
        assert cfg.exclude_globs == (".git/**", "README.md")
        assert cfg.subdir == "kr"
        assert cfg.max_file_size == 1024

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret")
        cfg = GitConnectorConfig.from_source({
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "auth_token_env": "MY_TOKEN",
            },
        })
        assert cfg.auth_token == "secret"


# ---------------------------------------------------------------------------
# Integration: GitConnector.fetch against a local bare repo
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _init_source_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "test")
    (path / "README.md").write_text("# readme\nignored")
    (path / "kr").mkdir()
    (path / "kr" / "law_a.md").write_text("# 법률 A\n내용")
    (path / "kr" / "law_b.md").write_text("# 법률 B\n내용")
    (path / "kr" / "junk.txt").write_text("junk")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")


@pytest.fixture
def local_repo(tmp_path):
    source = tmp_path / "source_repo"
    _init_source_repo(source)

    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(source), str(bare)], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return source, bare


@pytest.mark.asyncio
async def test_fetch_clones_and_emits_documents(local_repo, tmp_path, monkeypatch):
    _, bare = local_repo
    monkeypatch.setenv("GIT_CONNECTOR_WORKDIR", str(tmp_path / "workdirs"))

    connector = GitConnector()
    result = await connector.fetch({
        "id": "ds-1",
        "name": "test-repo",
        "repo_url": str(bare),
        "branch": "main",
        "include_globs": ["kr/**/*.md"],
        "exclude_globs": [".git/**", "README.md"],
    })

    assert result.success, result.error
    assert result.source_type == "git"
    assert result.version_fingerprint.startswith("git:")
    assert len(result.documents) == 2
    titles = sorted(d.title for d in result.documents)
    assert titles == ["law_a", "law_b"]

    doc = next(d for d in result.documents if d.title == "law_a")
    assert "법률 A" in doc.content
    assert doc.metadata["file_path"] == "kr/law_a.md"
    assert doc.metadata["source_type"] == "git"
    assert doc.metadata["commit_sha"]
    assert doc.metadata["repo_url"] == str(bare)
    # Plain markdown (no YAML frontmatter) stays untouched and is NOT flagged
    # as a legal document.
    assert "_is_legal_document" not in doc.metadata


@pytest.mark.asyncio
async def test_fetch_skips_when_fingerprint_matches(local_repo, tmp_path, monkeypatch):
    _, bare = local_repo
    monkeypatch.setenv("GIT_CONNECTOR_WORKDIR", str(tmp_path / "workdirs"))

    connector = GitConnector()
    first = await connector.fetch({
        "id": "ds-2",
        "repo_url": str(bare),
        "include_globs": ["kr/**/*.md"],
    })
    assert first.success
    fingerprint = first.version_fingerprint

    second = await connector.fetch(
        {
            "id": "ds-2",
            "repo_url": str(bare),
            "include_globs": ["kr/**/*.md"],
        },
        last_fingerprint=fingerprint,
    )
    assert second.success
    assert second.skipped
    assert second.documents == []
    assert second.version_fingerprint == fingerprint


@pytest.mark.asyncio
async def test_fetch_force_returns_docs_even_with_matching_fingerprint(
    local_repo, tmp_path, monkeypatch,
):
    _, bare = local_repo
    monkeypatch.setenv("GIT_CONNECTOR_WORKDIR", str(tmp_path / "workdirs"))

    connector = GitConnector()
    first = await connector.fetch({
        "id": "ds-3",
        "repo_url": str(bare),
        "include_globs": ["kr/**/*.md"],
    })
    second = await connector.fetch(
        {
            "id": "ds-3",
            "repo_url": str(bare),
            "include_globs": ["kr/**/*.md"],
        },
        force=True,
        last_fingerprint=first.version_fingerprint,
    )
    assert second.success
    assert not second.skipped
    assert len(second.documents) == 2


@pytest.mark.asyncio
async def test_fetch_detects_new_commits(local_repo, tmp_path, monkeypatch):
    source, bare = local_repo
    monkeypatch.setenv("GIT_CONNECTOR_WORKDIR", str(tmp_path / "workdirs"))

    connector = GitConnector()
    first = await connector.fetch({
        "id": "ds-4",
        "repo_url": str(bare),
        "include_globs": ["kr/**/*.md"],
    })
    first_fp = first.version_fingerprint

    # New commit on the source → push to bare
    (source / "kr" / "law_c.md").write_text("# 법률 C\n신규")
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "add law_c")
    _git(source, "push", "-q", str(bare), "main")

    second = await connector.fetch(
        {
            "id": "ds-4",
            "repo_url": str(bare),
            "include_globs": ["kr/**/*.md"],
        },
        last_fingerprint=first_fp,
    )
    assert second.success
    assert not second.skipped
    assert second.version_fingerprint != first_fp
    assert len(second.documents) == 3


@pytest.mark.asyncio
async def test_fetch_subdir_scoping(local_repo, tmp_path, monkeypatch):
    _, bare = local_repo
    monkeypatch.setenv("GIT_CONNECTOR_WORKDIR", str(tmp_path / "workdirs"))

    connector = GitConnector()
    result = await connector.fetch({
        "id": "ds-5",
        "repo_url": str(bare),
        "subdir": "kr",
        "include_globs": ["**/*.md"],
    })
    assert result.success
    assert len(result.documents) == 2
    # README.md at repo root should not be included
    assert all(d.metadata["file_path"].startswith("kr/") for d in result.documents)


@pytest.mark.asyncio
async def test_fetch_parses_legal_frontmatter(tmp_path, monkeypatch):
    """Git connector should strip YAML frontmatter and promote fields to metadata."""
    monkeypatch.setenv("GIT_CONNECTOR_WORKDIR", str(tmp_path / "workdirs"))

    source = tmp_path / "legal_source"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.email", "t@e.com")
    _git(source, "config", "user.name", "t")

    law_dir = source / "kr" / "119구조ㆍ구급에관한법률"
    law_dir.mkdir(parents=True)
    legal_md = """---
제목: 119구조ㆍ구급에 관한 법률
법령MST: 266637
법령ID: '011349'
법령구분: 법률
소관부처:
  - 소방청
공포일자: 2024-12-03
시행일자: 2025-06-04
상태: 시행
출처: https://www.law.go.kr/법령/119구조ㆍ구급에관한법률
---

# 119구조ㆍ구급에 관한 법률

## 제1장 총칙

##### 제1조 (목적)

이 법은 화재, 재난ㆍ재해 및 테러, 그 밖의 위급한 상황에서 ...
"""
    (law_dir / "법률.md").write_text(legal_md, encoding="utf-8")
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "initial")

    bare = tmp_path / "legal_origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(source), str(bare)], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    connector = GitConnector()
    result = await connector.fetch({
        "id": "ds-legal",
        "repo_url": str(bare),
        "subdir": "kr",
        "include_globs": ["**/*.md"],
    })

    assert result.success, result.error
    assert len(result.documents) == 1
    doc = result.documents[0]

    # Frontmatter stripped from content
    assert not doc.content.startswith("---")
    assert "법령MST" not in doc.content
    assert doc.content.startswith("# 119구조ㆍ구급에 관한 법률")

    # Title pulled from YAML 제목
    assert doc.title == "119구조ㆍ구급에 관한 법률"

    # Metadata promoted
    md = doc.metadata
    assert md["_is_legal_document"] is True
    assert md["law_type"] == "법률"
    assert md["law_id"] == "011349"
    assert md["law_mst"] == 266637
    assert md["ministry"] == "소방청"
    assert md["ministries"] == ["소방청"]
    assert md["promulgation_date"] == "2024-12-03"
    assert md["enforcement_date"] == "2025-06-04"
    assert md["doc_date"] == "2024-12-03"
    assert md["knowledge_type"] == "법령:법률"
    assert md["law_file_kind"] == "law"
    assert md["parent_law_slug"] == "119구조ㆍ구급에관한법률"

    # Author falls back to ministry when git author is empty upstream
    assert doc.author == "t" or doc.author == "소방청"


@pytest.mark.asyncio
async def test_fetch_returns_error_for_bad_url(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONNECTOR_WORKDIR", str(tmp_path / "workdirs"))
    connector = GitConnector()
    result = await connector.fetch({
        "id": "ds-bad",
        "repo_url": str(tmp_path / "does-not-exist"),
    })
    assert not result.success
    assert result.error
    assert "git" in result.error.lower()
