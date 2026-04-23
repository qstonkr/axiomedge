"""Microsoft Graph API common client — SharePoint/OneDrive/Teams 공유.

3개 connector (sharepoint/onedrive/teams) 가 같은 base client 위에서 동작:
공통 paging (``@odata.nextLink``), 429 throttle handling, Bearer token auth.
신규 MS Graph endpoint connector 추가 시 본 client 위에 endpoint-specific
wrapper 만 추가하면 됨.

토큰 모드: **shared** (admin 이 organization-wide app-only token 1회 등록 →
``org/{org_id}/connector-shared/{connector_id}`` SecretBox path 에 저장).
사용자는 site_id/team_id 같은 sub-resource 만 입력.
"""

from .client import MSGraphAPIError, MSGraphClient
from .driveitem import download_drive_item

__all__ = [
    "MSGraphAPIError",
    "MSGraphClient",
    "download_drive_item",
]
