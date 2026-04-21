"""Outlook (Microsoft 365 mail) connector — Microsoft Graph API.

토큰 모드: shared (admin app-only token, ``Mail.Read`` scope). SharePoint/
OneDrive/Teams 와 동일한 ``_msgraph`` base client 재사용. 사용자는 user_id
(이메일 또는 ``me``) + folder + days_back 만 입력.

각 message → 1 RawDocument:
- title: subject
- content: subject + sender + body (text 우선, html 은 strip)
- updated_at: receivedDateTime
"""

from .config import OutlookConnectorConfig
from .connector import OutlookConnector

__all__ = ["OutlookConnector", "OutlookConnectorConfig"]
