"""Google Workspace 토큰 해석 — service account JSON or raw access_token.

Service account JSON 인 경우 RS256 JWT exchange 로 1시간짜리 access_token
발급. raw access_token 인 경우 그대로 반환 (admin 책임으로 만료 갱신).

PyJWT[crypto] 가 이미 의존성에 있으므로 (SecretBox 의 cryptography 와 별개로)
별도 라이브러리 추가 없음.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
import jwt

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_JWT_TTL_SECONDS = 3600


class GoogleAuthError(RuntimeError):
    """Google access token 발급 실패."""


def _parse_service_account(token_str: str) -> dict[str, Any] | None:
    """token 이 service account JSON 형식이면 dict 반환, 아니면 None."""
    s = (token_str or "").strip()
    if not s.startswith("{"):
        return None
    try:
        sa = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(sa, dict):
        return None
    if sa.get("type") != "service_account":
        return None
    for required in ("client_email", "private_key"):
        if not sa.get(required):
            return None
    return sa


async def resolve_access_token(token_str: str, scopes: list[str]) -> str:
    """SecretBox 의 token 값 → 실제 access_token.

    - service account JSON → JWT exchange (RS256) → 1시간 access_token
    - raw access_token (예: ya29...) → 그대로 반환 (admin 갱신 책임)

    Raises GoogleAuthError on JWT/exchange failure — connector launcher 가
    잡아 401-style 에러로 처리.
    """
    if not token_str:
        raise GoogleAuthError("Google auth token 이 비어있음")

    sa = _parse_service_account(token_str)
    if sa is None:
        # Raw access token — 길이/형식 체크는 안 함 (Google 가 401 반환).
        return token_str.strip()

    now = int(time.time())
    claims = {
        "iss": sa["client_email"],
        "scope": " ".join(scopes),
        "aud": _TOKEN_URL,
        "exp": now + _JWT_TTL_SECONDS,
        "iat": now,
    }
    # subject (sa.get("subject")) 는 도메인 위임 시 impersonate 할 user.
    # 기본은 service account 자체로 접근.
    if sa.get("subject"):
        claims["sub"] = sa["subject"]

    try:
        assertion = jwt.encode(claims, sa["private_key"], algorithm="RS256")
    except (ValueError, TypeError) as e:
        raise GoogleAuthError(f"JWT encode 실패: {e}") from e

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(_TOKEN_URL, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            })
    except httpx.RequestError as e:
        raise GoogleAuthError(f"token endpoint 호출 실패: {e}") from e

    if resp.status_code != 200:
        raise GoogleAuthError(
            f"Google token exchange {resp.status_code}: {resp.text[:200]}",
        )
    payload = resp.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise GoogleAuthError(f"token response 에 access_token 없음: {payload}")
    return str(access_token)
