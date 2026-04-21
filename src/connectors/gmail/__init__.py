"""Gmail connector — Gmail API v1.

토큰 모드: shared (admin service account JSON, **domain-wide delegation +
``subject`` 로 impersonate 할 user 지정 필요**) 또는 raw access_token.

사용자가 ``user_id`` (이메일 또는 ``me``) + Gmail 검색 ``query`` 입력 →
matching messages 목록 → 각 message body → 1 RawDocument.

검색 query 예시:
- ``from:boss@company.com after:2026-01-01``
- ``subject:안건 has:attachment``
- ``label:Important``
"""

from .config import GmailConnectorConfig
from .connector import GmailConnector

__all__ = ["GmailConnector", "GmailConnectorConfig"]
