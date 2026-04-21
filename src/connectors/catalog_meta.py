"""Server-side connector metadata — token mode dispatch.

Frontend ``src/apps/web/src/lib/connectors/catalog.ts`` 와 sync 유지.
신규 connector 추가 시 catalog.ts 와 본 파일 둘 다 갱신.

분류 정책:

- **per-user token**: 사용자가 직접 토큰 입력. SecretBox path =
  ``user/{user_id}/data-source/{source_id}``. (Notion/Git/Confluence)
- **shared token**: admin 이 organization-wide bot token 1회 등록. 사용자
  는 channel_ids 같은 sub-resource 만 입력. SecretBox path =
  ``org/{org_id}/connector-shared/{connector_id}``. (Slack)
- **none**: 토큰 불필요 (file_upload, crawl_result).
"""

from __future__ import annotations

from typing import Final

# 사용자가 본인 토큰 입력 — user-scoped SecretBox path.
PER_USER_TOKEN_CONNECTORS: Final[frozenset[str]] = frozenset({
    "notion",
    "git",
    "confluence",
    "jira",
    "github_issues",
})

# Organization-wide bot token — admin 1회 등록, 사용자는 sub-resource 만.
SHARED_TOKEN_CONNECTORS: Final[frozenset[str]] = frozenset({
    "slack",
    "sharepoint",
    "onedrive",
    "teams",
    "outlook",
    "google_drive",
    "google_sheets",
    "gmail",
})

# 토큰 불필요.
NO_TOKEN_CONNECTORS: Final[frozenset[str]] = frozenset({
    "file_upload",
    "crawl_result",
})

# 사용자 self-service 가능한 connector — admin 만 가능한 것 (예: confluence-org)
# 와 구분. catalog.ts 의 scope 와 sync.
USER_SELF_SERVICE_CONNECTORS: Final[frozenset[str]] = (
    PER_USER_TOKEN_CONNECTORS
    | SHARED_TOKEN_CONNECTORS
    | NO_TOKEN_CONNECTORS
)


def is_shared_token_connector(source_type: str) -> bool:
    return source_type in SHARED_TOKEN_CONNECTORS


def is_per_user_token_connector(source_type: str) -> bool:
    return source_type in PER_USER_TOKEN_CONNECTORS


def is_user_self_service(source_type: str) -> bool:
    return source_type in USER_SELF_SERVICE_CONNECTORS
