"""GitHub Issues connector — config + Link-header paging mock + PR 분리."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Catalog meta
# ---------------------------------------------------------------------------


class TestCatalogMeta:
    def test_github_issues_in_per_user_set(self):
        from src.connectors.catalog_meta import (
            PER_USER_TOKEN_CONNECTORS,
            is_per_user_token_connector,
            is_user_self_service,
        )
        assert "github_issues" in PER_USER_TOKEN_CONNECTORS
        assert is_per_user_token_connector("github_issues")
        assert is_user_self_service("github_issues")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestGHConfig:
    def test_missing_token_raises(self):
        from src.connectors.github_issues.config import GitHubIssuesConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            GitHubIssuesConnectorConfig.from_source({
                "crawl_config": {"repos": ["o/r"]},
            })

    def test_missing_repos_raises(self):
        from src.connectors.github_issues.config import GitHubIssuesConnectorConfig
        with pytest.raises(ValueError, match="repos"):
            GitHubIssuesConnectorConfig.from_source({
                "crawl_config": {"auth_token": "tk"},
            })

    def test_invalid_repo_format_raises(self):
        from src.connectors.github_issues.config import GitHubIssuesConnectorConfig
        with pytest.raises(ValueError, match="owner/repo"):
            GitHubIssuesConnectorConfig.from_source({
                "crawl_config": {"auth_token": "tk", "repos": ["just-name"]},
            })

    def test_string_repos_split(self):
        from src.connectors.github_issues.config import GitHubIssuesConnectorConfig
        cfg = GitHubIssuesConnectorConfig.from_source({
            "crawl_config": {"auth_token": "tk", "repos": "o/r1, o/r2"},
        })
        assert cfg.repos == ("o/r1", "o/r2")

    def test_invalid_state_raises(self):
        from src.connectors.github_issues.config import GitHubIssuesConnectorConfig
        with pytest.raises(ValueError, match="state"):
            GitHubIssuesConnectorConfig.from_source({
                "crawl_config": {
                    "auth_token": "tk", "repos": ["o/r"],
                    "state": "weird",
                },
            })

    def test_days_back_zero_preserved(self):
        from src.connectors.github_issues.config import GitHubIssuesConnectorConfig
        cfg = GitHubIssuesConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "tk", "repos": ["o/r"], "days_back": 0,
            },
        })
        assert cfg.days_back == 0  # 0 = 무한, default 90 으로 치환되면 안 됨


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestGHAuth:
    def test_bearer_header(self):
        from src.connectors.github_issues.client import GitHubClient
        client = GitHubClient(auth_token="ghp_xyz")
        assert client._headers["Authorization"] == "Bearer ghp_xyz"
        assert "X-GitHub-Api-Version" in client._headers
        _run(client.aclose())

    def test_empty_token_raises(self):
        from src.connectors.github_issues.client import GitHubClient
        with pytest.raises(ValueError, match="auth_token"):
            GitHubClient("")


# ---------------------------------------------------------------------------
# Fetch — issue + PR 분리, comment 결합
# ---------------------------------------------------------------------------


class TestGHFetch:
    @pytest.mark.asyncio
    async def test_fetch_includes_pr_when_enabled(self):
        from src.connectors.github_issues import GitHubIssuesConnector
        from src.connectors.github_issues.client import GitHubClient

        async def _list_issues(owner, repo, **kwargs):
            yield {
                "number": 1, "title": "Login bug", "body": "401 on Safari",
                "state": "open", "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/issues/1",
                "labels": [{"name": "bug"}],
                "updated_at": "2026-04-21T09:00:00Z",
                "comments": 1,
            }
            yield {
                "number": 2, "title": "Fix login",
                "body": "PR body here", "state": "open",
                "user": {"login": "bob"},
                "html_url": "https://github.com/o/r/pull/2",
                "labels": [],
                "updated_at": "2026-04-21T10:00:00Z",
                "comments": 0,
                "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/2"},
            }

        async def _list_comments(owner, repo, num, **kwargs):
            return [
                {"user": {"login": "carol"},
                 "created_at": "2026-04-21T09:30:00Z",
                 "body": "checking session token"},
            ]

        client_instance = GitHubClient.__new__(GitHubClient)
        client_instance.list_issues = _list_issues
        client_instance.list_issue_comments = _list_comments
        client_instance.aclose = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "src.connectors.github_issues.connector.GitHubClient",
            return_value=client_instance,
        ):
            result = await GitHubIssuesConnector().fetch({
                "auth_token": "ghp_x", "repos": ["o/r"],
                "days_back": 0,  # since 안 보냄
            })

        assert result.success
        assert len(result.documents) == 2
        # issue + PR 둘 다
        kinds = [
            "PR" if d.metadata["is_pr"] else "Issue"
            for d in result.documents
        ]
        assert "Issue" in kinds and "PR" in kinds

        issue_doc = next(d for d in result.documents if not d.metadata["is_pr"])
        assert "Login bug" in issue_doc.title
        assert "401 on Safari" in issue_doc.content
        assert "Labels: bug" in issue_doc.content
        assert "checking session token" in issue_doc.content  # comment 포함

    @pytest.mark.asyncio
    async def test_include_prs_false_skips_prs(self):
        from src.connectors.github_issues import GitHubIssuesConnector
        from src.connectors.github_issues.client import GitHubClient

        async def _list_issues(owner, repo, **kwargs):
            yield {
                "number": 1, "title": "Issue", "body": "body",
                "state": "open", "user": {"login": "a"},
                "html_url": "x", "labels": [], "comments": 0,
                "updated_at": "2026-04-21T00:00:00Z",
            }
            yield {
                "number": 2, "title": "PR", "body": "pr body",
                "state": "open", "user": {"login": "b"},
                "html_url": "y", "labels": [], "comments": 0,
                "updated_at": "2026-04-21T00:00:00Z",
                "pull_request": {"url": "z"},
            }

        client_instance = GitHubClient.__new__(GitHubClient)
        client_instance.list_issues = _list_issues
        client_instance.list_issue_comments = AsyncMock(return_value=[])
        client_instance.aclose = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "src.connectors.github_issues.connector.GitHubClient",
            return_value=client_instance,
        ):
            result = await GitHubIssuesConnector().fetch({
                "auth_token": "ghp_x", "repos": ["o/r"],
                "include_prs": False, "days_back": 0,
            })

        assert result.success
        assert len(result.documents) == 1
        assert not result.documents[0].metadata["is_pr"]

    @pytest.mark.asyncio
    async def test_skip_404_repo_continues_others(self):
        from src.connectors.github_issues import GitHubIssuesConnector
        from src.connectors.github_issues.client import GitHubAPIError, GitHubClient

        async def _list_issues(owner, repo, **kwargs):
            if repo == "missing":
                raise GitHubAPIError("not found", status=404)
            yield {
                "number": 5, "title": "OK issue", "body": "ok",
                "state": "open", "user": {"login": "a"},
                "html_url": "x", "labels": [], "comments": 0,
                "updated_at": "2026-04-21T00:00:00Z",
            }

        client_instance = GitHubClient.__new__(GitHubClient)
        client_instance.list_issues = _list_issues
        client_instance.list_issue_comments = AsyncMock(return_value=[])
        client_instance.aclose = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "src.connectors.github_issues.connector.GitHubClient",
            return_value=client_instance,
        ):
            result = await GitHubIssuesConnector().fetch({
                "auth_token": "ghp_x", "repos": ["o/missing", "o/good"],
                "days_back": 0,
            })

        assert result.success
        assert "o/missing" in result.metadata["repos_skipped"]
        assert len(result.documents) == 1


# ---------------------------------------------------------------------------
# Google Sites entry 제거 검증 — Drive description 으로 흡수
# ---------------------------------------------------------------------------


class TestGoogleSitesRemoved:
    def test_no_gwiki_in_catalog_meta(self):
        from src.connectors.catalog_meta import (
            PER_USER_TOKEN_CONNECTORS,
            SHARED_TOKEN_CONNECTORS,
            NO_TOKEN_CONNECTORS,
        )
        # gwiki 가 어떤 set 에도 없는지 — Drive 로 흡수됐으니 별도 등록 X
        all_connectors = (
            PER_USER_TOKEN_CONNECTORS
            | SHARED_TOKEN_CONNECTORS
            | NO_TOKEN_CONNECTORS
        )
        assert "gwiki" not in all_connectors
        assert "google_sites" not in all_connectors
