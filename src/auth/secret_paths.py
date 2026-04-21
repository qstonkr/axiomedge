"""SecretBox path 규약 — admin/user/shared 3가지 격리 모드.

Path namespace 결정 규칙:

| 패턴 | 용도 |
|---|---|
| ``org/{org_id}/data-source/{source_id}`` | admin 등록 (org-wide) |
| ``user/{user_id}/data-source/{source_id}`` | 사용자 self-service |
| ``org/{org_id}/connector-shared/{connector_id}`` | Slack bot 같은 org-wide shared token |

라우트가 source dict 의 ``owner_user_id`` 보고 자동 선택. shared token 은
별도 admin UI 가 등록 후 connector launcher 가 catalog metadata 보고 fetch.
"""

from __future__ import annotations


def data_source_path(
    *,
    organization_id: str,
    source_id: str,
    owner_user_id: str | None = None,
) -> str:
    """data_source 의 per-source secret path.

    owner_user_id 가 있으면 user-scoped, 없으면 org-scoped (admin 등록).
    이 결정은 라우트가 책임 — 사용자 라우트는 owner_user_id 강제 전달,
    admin 라우트는 None 전달.
    """
    if owner_user_id:
        return f"user/{owner_user_id}/data-source/{source_id}"
    return f"org/{organization_id}/data-source/{source_id}"


def shared_token_path(organization_id: str, connector_id: str) -> str:
    """admin 등록 organization-wide shared bot token (Slack 등) path.

    connector_id 는 ``slack``/``teams_bot`` 같은 source_type 또는 connector
    family 이름. 같은 org 안에서는 1 connector 당 1 shared token — 사용자
    self-service source 가 등록 시 channel_ids 만 받고 launcher 가 이 path
    에서 token 자동 fetch.
    """
    return f"org/{organization_id}/connector-shared/{connector_id}"


def parse_path_scope(path: str) -> str:
    """Path → scope label (``org`` / ``user`` / ``unknown``) — audit log 용도."""
    if path.startswith("org/") and "/connector-shared/" in path:
        return "shared"
    if path.startswith("user/"):
        return "user"
    if path.startswith("org/"):
        return "org"
    return "unknown"
