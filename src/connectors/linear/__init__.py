"""Linear connector — GraphQL API.

토큰 모드: per-user (사용자 본인 API key, https://linear.app/settings/api).
team_keys (예: ENG, DESIGN) 입력 → 각 team 의 issues + comments → RawDocument.

GraphQL 쿼리 한 번에 issue + nested comments + assignee/state/labels 까지
가져와서 N+1 호출 회피 (REST 패턴 대비 효율적).
"""

from .client import LinearAPIError, LinearClient
from .config import LinearConnectorConfig
from .connector import LinearConnector

__all__ = [
    "LinearAPIError",
    "LinearClient",
    "LinearConnector",
    "LinearConnectorConfig",
]
