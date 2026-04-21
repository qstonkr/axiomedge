"""Salesforce connector — REST API + SOQL.

토큰 모드: shared (admin Connected App refresh_token + access_token).
사용자가 SOQL query + object 만 입력 → records → RawDocument.

토큰 형식 (SecretBox 에 저장 — JSON):
```
{
  "instance_url": "https://your-domain.my.salesforce.com",
  "client_id": "3MVG9...",
  "client_secret": "abc...",
  "refresh_token": "5Aep861..."
}
```

매 connector run 시 refresh_token 으로 access_token 새로 발급. access_token
은 보통 2시간 유효 — refresh 자동 처리.
"""

from .auth import SalesforceAuthError, refresh_access_token
from .client import SalesforceAPIError, SalesforceClient
from .config import SalesforceConnectorConfig
from .connector import SalesforceConnector

__all__ = [
    "SalesforceAPIError",
    "SalesforceAuthError",
    "SalesforceClient",
    "SalesforceConnector",
    "SalesforceConnectorConfig",
    "refresh_access_token",
]
