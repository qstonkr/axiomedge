"""GitHub Issues connector — GitHub REST API v3.

토큰 모드: per-user (사용자 본인 PAT, ``repo`` scope 권장 — public 만이면
``public_repo``). issue + comments + PR 본문 가져옴 (GitHub API 가 PR 도 issue
로 반환 — ``pull_request`` field 로 구분).

config.repos 에 ``owner/repo`` list 입력. 여러 repo 한 source 로 묶어 동기화.
"""

from .client import GitHubAPIError, GitHubClient
from .config import GitHubIssuesConnectorConfig
from .connector import GitHubIssuesConnector

__all__ = [
    "GitHubAPIError",
    "GitHubClient",
    "GitHubIssuesConnector",
    "GitHubIssuesConnectorConfig",
]
