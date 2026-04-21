"""Dropbox connector — Dropbox API v2 (files).

토큰 모드: shared (admin App access token, ``files.metadata.read`` +
``files.content.read`` scopes). 사용자가 folder_path 만 입력 — recursive
list_folder + download → tempfile → ``parse_file()`` 위임.
"""

from .client import DropboxAPIError, DropboxClient
from .config import DropboxConnectorConfig
from .connector import DropboxConnector

__all__ = [
    "DropboxAPIError",
    "DropboxClient",
    "DropboxConnector",
    "DropboxConnectorConfig",
]
