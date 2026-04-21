"""Box connector — Box API v2 (folders + files).

토큰 모드: shared (admin Box JWT app token 또는 OAuth access token).
사용자가 folder_id 입력 (root = ``"0"``) → recursive folder BFS + file
download → tempfile → ``parse_file()``.
"""

from .client import BoxAPIError, BoxClient
from .config import BoxConnectorConfig
from .connector import BoxConnector

__all__ = [
    "BoxAPIError",
    "BoxClient",
    "BoxConnector",
    "BoxConnectorConfig",
]
