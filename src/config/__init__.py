"""Knowledge Local — Infrastructure Settings (SSOT).

``from src.config import get_settings`` 한 줄로 모든 인프라 설정 접근.
실제 구현은 ``src/config/settings.py`` 에 있다.

### Config 3파일 경계

| 파일 | 역할 |
|---|---|
| ``src/config/`` (이 패키지) | **인프라** — DB 주소, 포트, timeout, 연결 풀 (env var override) |
| ``src/config_weights/`` | **하이퍼파라미터** — 검색 가중치, threshold, 캐시 TTL |
| ``src/distill/config.py`` | **Distill 프로필** — LoRA, lr, epochs, QA style (YAML / DB override) |
"""

from src.config.settings import (  # noqa: F401
    # Constants
    DEFAULT_DATABASE_URL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_MODEL_HF,
    DEFAULT_LLM_MODEL,
    DEFAULT_RUNTIME_BASE_DIR,
    # Settings classes
    ApiSettings,
    AuthSettings,
    ConfluenceSettings,
    DashboardSettings,
    DatabaseSettings,
    DistillSettings,
    EmbeddingSettings,
    Neo4jSettings,
    OllamaSettings,
    PipelineSettings,
    QdrantSettings,
    QualitySettings,
    RedisSettings,
    Settings,
    TeiSettings,
    TreeIndexSettings,
    # Functions
    get_settings,
    reset_settings,
)

__all__ = [
    "DEFAULT_DATABASE_URL",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_EMBEDDING_MODEL_HF",
    "DEFAULT_LLM_MODEL",
    "DEFAULT_RUNTIME_BASE_DIR",
    "ApiSettings",
    "AuthSettings",
    "ConfluenceSettings",
    "DashboardSettings",
    "DatabaseSettings",
    "DistillSettings",
    "EmbeddingSettings",
    "Neo4jSettings",
    "OllamaSettings",
    "PipelineSettings",
    "QdrantSettings",
    "QualitySettings",
    "RedisSettings",
    "Settings",
    "TeiSettings",
    "TreeIndexSettings",
    "get_settings",
    "reset_settings",
]
