"""Salesforce OAuth refresh — Connected App credentials → access_token.

Flow: ``refresh_token`` grant_type → 새 access_token (보통 2시간 유효).
매 connector run 시작 시 refresh — 만료 token 으로 호출 안 가도록.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SalesforceAuthError(RuntimeError):
    """Salesforce access token 발급 실패."""


def _parse_credentials(token_str: str) -> dict[str, Any]:
    """SecretBox 의 JSON token → dict. Required: instance_url, client_id,
    client_secret, refresh_token."""
    try:
        creds = json.loads(token_str)
    except json.JSONDecodeError as e:
        raise SalesforceAuthError(
            f"Salesforce auth_token 이 유효한 JSON 아님: {e}",
        ) from e
    if not isinstance(creds, dict):
        raise SalesforceAuthError("Salesforce auth_token must be JSON object")
    for required in ("instance_url", "client_id", "client_secret", "refresh_token"):
        if not creds.get(required):
            raise SalesforceAuthError(
                f"Salesforce auth_token 에 ``{required}`` 누락",
            )
    return creds


async def refresh_access_token(token_str: str) -> tuple[str, str]:
    """Refresh token → ``(access_token, instance_url)``.

    instance_url 도 함께 반환 — Salesforce 는 호출 시점의 instance host 사용해야
    함 (사용자 sandbox/production 마다 다름). credentials JSON 의 instance_url
    fallback, 응답 안에 instance_url 있으면 그것 우선.
    """
    creds = _parse_credentials(token_str)
    token_url = f"{creds['instance_url'].rstrip('/')}/services/oauth2/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(token_url, data=data)
    except httpx.RequestError as e:
        raise SalesforceAuthError(f"token endpoint 호출 실패: {e}") from e

    if resp.status_code != 200:
        raise SalesforceAuthError(
            f"Salesforce token refresh {resp.status_code}: {resp.text[:200]}",
        )
    payload = resp.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise SalesforceAuthError(
            f"Salesforce token response 에 access_token 없음: {payload}",
        )
    instance_url = str(payload.get("instance_url") or creds["instance_url"]).rstrip("/")
    return str(access_token), instance_url
