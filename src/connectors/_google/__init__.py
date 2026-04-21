"""Google Workspace API common client — Drive/Sheets/Gmail 공유.

3개 connector (google_drive/google_sheets/gmail) 가 같은 base 위에서 동작:
공통 paging (``pageToken`` → ``nextPageToken``), 429 retry, Bearer auth.

토큰 입력 형식 (둘 다 지원):
1. **Service account JSON** — ``{"type": "service_account", "client_email":
   "...", "private_key": "...", ...}`` 그대로 SecretBox 에 저장. base client 가
   매 connector run 시작 시 RS256 JWT exchange 로 access_token 발급.
2. **Raw access token** — 운영자가 OAuth playground 등에서 받은 1시간짜리
   token. 만료 시 admin 이 SecretBox 갱신 (자동 refresh 안 함).

Service account 가 권장 — refresh 자동, 만료 걱정 X. raw token 은 PoC/테스트.
"""

from .auth import resolve_access_token
from .client import GoogleAPIError, GoogleClient

__all__ = [
    "GoogleAPIError",
    "GoogleClient",
    "resolve_access_token",
]
