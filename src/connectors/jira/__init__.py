"""Jira connector — Atlassian REST API v3 (Cloud) 또는 v2 (Server/DC).

토큰 모드: per-user (사용자 본인 API token / PAT). Confluence 와 동일 패턴 —
사용자가 본인 권한 안에서 issue 가져옴 (Jira 권한 모델 자동 강제).

Auth 자동 분기:
- ``email`` field 가 있으면 Cloud Basic auth (``base64(email:api_token)``)
- ``email`` 비어있으면 Server/DC Bearer auth (PAT)

JQL 기반 검색:
- 사용자가 ``jql`` 입력 (예: ``project = ENG AND updated >= -30d``)
- 결과 issues 의 description + comments 가져와 1 issue = 1 RawDocument

ADF (Atlassian Document Format, v3 default) 는 재귀 text 추출.
"""

from .client import JiraAPIError, JiraClient
from .config import JiraConnectorConfig
from .connector import JiraConnector

__all__ = [
    "JiraAPIError",
    "JiraClient",
    "JiraConnector",
    "JiraConnectorConfig",
]
