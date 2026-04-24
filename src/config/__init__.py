"""Knowledge Local — Config Package (SSOT facade).

``from src.config import get_settings`` / ``from src.config import DistillProfile`` —
인프라 설정과 distill 프로필 Pydantic 모델을 한 곳에서 접근.

### Config 경계 (PR11 재편)

| 위치 | 역할 |
|---|---|
| ``src/config/settings.py`` | **인프라** — DB 주소, 포트, timeout, 연결 풀 (env var override) |
| ``src/config/profiles.py`` | **Distill 프로필** Pydantic 모델 — LoRA, training, QA style, deploy |
| ``src/config/weights/`` | **하이퍼파라미터** — 검색 가중치, threshold, 캐시 TTL |
| ``src/distill/config.py`` | **Distill 운영** — build 상수 + YAML I/O + facade re-export. |
"""

from src.config.profiles import (  # noqa: F401
    DataQualityConfig,
    DeployConfig,
    DistillConfig,
    DistillDefaults,
    DistillProfile,
    EvalThreshold,
    LoRAConfig,
    QAStyleConfig,
    TrainingConfig,
)
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
    AwsSettings,
    ConfluenceSettings,
    DashboardSettings,
    DatabaseSettings,
    DistillSettings,
    EmbeddingSettings,
    Neo4jSettings,
    NotificationSettings,
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
    "AwsSettings",
    "ConfluenceSettings",
    "DashboardSettings",
    "DatabaseSettings",
    "DataQualityConfig",
    "DeployConfig",
    "DistillConfig",
    "DistillDefaults",
    "DistillProfile",
    "DistillSettings",
    "EmbeddingSettings",
    "EvalThreshold",
    "LoRAConfig",
    "Neo4jSettings",
    "NotificationSettings",
    "OllamaSettings",
    "PipelineSettings",
    "QAStyleConfig",
    "QdrantSettings",
    "QualitySettings",
    "RedisSettings",
    "Settings",
    "TeiSettings",
    "TrainingConfig",
    "TreeIndexSettings",
    "get_settings",
    "reset_settings",
]
