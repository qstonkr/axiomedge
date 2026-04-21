"""OneDrive connector — Microsoft Graph drive items.

토큰 모드: shared (admin app-only token). 사용자가 drive_id (또는
``users/{upn}/drive``) + folder_path 만 입력. 기본 ``Files.Read.All`` scope.

각 file → text 추출은 MIME 별 동작:
- text/plain, text/markdown → content 그대로
- application/json → content 그대로
- 그 외 (PDF/DOCX/PPTX/XLSX) → ``/content`` 다운로드 후 ``parse_file()`` 위임
  — 본 connector 는 파일 metadata 와 다운로드만 담당, 파싱은 pipeline 의
  document_parser 가 처리.
"""

from .config import OneDriveConnectorConfig
from .connector import OneDriveConnector

__all__ = ["OneDriveConnector", "OneDriveConnectorConfig"]
