"""SharePoint connector — Microsoft Graph site/list/items.

토큰 모드: shared (admin app-only token, ``Sites.Read.All`` scope 권장).
사용자는 site_id + list_id (또는 site_url + list_name) 만 입력.
"""

from .config import SharePointConnectorConfig
from .connector import SharePointConnector

__all__ = ["SharePointConnector", "SharePointConnectorConfig"]
