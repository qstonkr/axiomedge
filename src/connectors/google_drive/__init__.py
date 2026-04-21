"""Google Drive connector — Drive v3 API.

토큰 모드: shared (admin service account JSON 또는 OAuth access_token).
사용자가 folder_id 입력 → BFS 로 하위 모든 파일 fetch.

MIME 별 처리:
- ``application/vnd.google-apps.document`` → ``files.export?mimeType=text/plain``
- ``application/vnd.google-apps.presentation`` → ``files.export?mimeType=text/plain``
- ``application/vnd.google-apps.spreadsheet`` → 별도 google_sheets connector 권장
  (본 connector 는 csv export 만 — 행 단위 의미 손실)
- ``application/pdf`` / DOCX / PPTX → ``files.get?alt=media`` → tempfile →
  ``parse_file()`` 위임
"""

from .config import GoogleDriveConnectorConfig
from .connector import GoogleDriveConnector

__all__ = ["GoogleDriveConnector", "GoogleDriveConnectorConfig"]
