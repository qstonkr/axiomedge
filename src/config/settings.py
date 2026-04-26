"""Knowledge Local — Infrastructure Settings (SSOT).

이 파일은 **인프라 설정** 만 담당: DB 호스트/포트, Qdrant URL, Ollama URL,
Redis, 인증 설정, 파이프라인 worker 수 등. 모든 값은 환경 변수로 override 가능.

### Config 3파일 경계 (이 파일이 한 쪽 끝)

| 파일 | 역할 | 예시 | Override |
|---|---|---|---|
| ``src/config/`` (이 패키지) | **인프라** — 서비스 주소, 포트, timeout, 연결 풀 | ``DATABASE_URL``, ``QDRANT_URL``, ``OLLAMA_BASE_URL`` | env var |
| ``src/config_weights/`` | **하이퍼파라미터** — 검색 가중치, threshold, chunk 크기, 캐시 TTL | ``RerankerWeights.model_weight``, ``ChunkingConfig.max_chunk_chars`` | 코드 또는 hot-reload |
| ``src/distill/config.py`` | **Distill 프로필** — LoRA rank, lr, batch size, QA style, 배포 설정 | ``DistillProfile.lora.r``, ``TrainingConfig.epochs`` | YAML / DB |

**새 설정 추가 시** 이 3파일 중 어디에 넣어야 하는지:
- "서버 어디에 접속?" → ``src/config.py``
- "검색 결과 가중치 몇?" → ``src/config_weights.py``
- "학습 파라미터 몇?" → ``src/distill/config.py``

이 경계를 지키지 않으면 SSOT 드리프트 발생 (2026-04-16 audit PR4/PR5 참고).
"""  # noqa: E501

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Model name defaults — SSOT for all fallback references
DEFAULT_LLM_MODEL = "exaone3.5:7.8b"
DEFAULT_EMBEDDING_MODEL = "bge-m3:latest"  # Ollama tag
DEFAULT_EMBEDDING_MODEL_HF = "BAAI/bge-m3"  # HuggingFace ID (ONNX provider)

_DB_FALLBACK = "postgresql+asyncpg://{}:{}@localhost:5432/knowledge_db".format(  # noqa: S106
    os.getenv("PGUSER", "knowledge"), os.getenv("PGPASSWORD", "knowledge"),
)
DEFAULT_DATABASE_URL = os.getenv("DATABASE_URL", _DB_FALLBACK)

DEFAULT_RUNTIME_BASE_DIR = os.getenv(
    "KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR", "/tmp/knowledge-local"
)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    database_url: str = Field(
        default=DEFAULT_DATABASE_URL,
        alias="DATABASE_URL",
    )
    pool_size: int = Field(default=5)
    max_overflow: int = Field(default=10)
    echo: bool = Field(default=False)


class QdrantSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QDRANT_")

    url: str = Field(default="http://localhost:6333")
    collection_name: str = Field(default="knowledge")
    entity_collection_name: str = Field(default="knowledge_entities")
    # NOTE: Vector dimension / vector names 는 이 settings 에 없음. SSOT:
    #   - dimension         → src.config_weights.weights.embedding.dimension
    #   - dense_vector_name → src.vectordb.client.DEFAULT_DENSE_VECTOR_NAME
    #   - sparse_vector_name → src.vectordb.client.DEFAULT_SPARSE_VECTOR_NAME
    # Runtime override 가 필요하면 QDRANT_DENSE_DIMENSION / QDRANT_*_VECTOR_NAME
    # env var 를 읽는 QdrantProviderConfig.from_env (src/vectordb/client.py) 사용.
    timeout: int = Field(default=30)
    search_timeout_ms: int = Field(default=5000)


class Neo4jSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEO4J_")

    enabled: bool = Field(default=True)
    uri: str = Field(default="bolt://localhost:7687")
    user: str = Field(default="neo4j")
    password: str = Field(default="")
    database: str = Field(default="neo4j")
    query_timeout_ms: int = Field(default=30000, description="쿼리 타임아웃 (ms)")
    # retry_time_s 는 tx_timeout 보다 충분히 커야 일시 장애 (leader election, GC
    # pause 등) 가 retry 안에 흡수됨. 기존 구현은 ``query_timeout_ms / 1000`` 으
    # 로 둘이 같아서 한 번 timeout 나면 retry 시간도 동시 소진 → 재시도 의미 X.
    retry_time_s: int = Field(default=120, ge=0, le=600)
    max_connection_pool_size: int = Field(default=100, ge=1, le=1000)
    connection_acquisition_timeout_s: int = Field(default=60, ge=1, le=300)
    # connection_timeout = TCP-level 새 소켓 establish timeout (pool 에서 가져오
    # 는 것과 다름). Mac 슬립/NAT drop 후 재연결 시 30s default 가 짧아 실패 →
    # 60s 로 여유 둠.
    connection_timeout_s: int = Field(default=60, ge=5, le=300)
    # K8s NAT/load-balancer idle timeout 대응 — 장시간 ingestion 쿼리 중 connection
    # 이 silently drop 되는 것 방지.
    keep_alive: bool = Field(default=True)


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLLAMA_")

    base_url: str = Field(default="http://localhost:11434")
    model: str = Field(default=DEFAULT_LLM_MODEL)
    embedding_model: str = Field(default=DEFAULT_EMBEDDING_MODEL)
    timeout: int = Field(default=60, ge=10, le=300)
    context_length: int = Field(default=32768, ge=1024, le=32768)
    max_content_length: int = Field(default=4000, ge=100, le=32000)


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWLEDGE_BGE_")

    onnx_model_path: str = Field(default="")
    max_length: int = Field(default=512)
    colbert_max_tokens: int = Field(default=128)


class QualitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWLEDGE_QUALITY_")

    min_content_length: int = Field(default=50, ge=10, le=1000)
    stale_threshold_days: int = Field(default=730)
    stale_weight: float = Field(default=0.7, ge=0.0, le=1.0)


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWLEDGE_PIPELINE_")

    runtime_base_dir: str = Field(default=DEFAULT_RUNTIME_BASE_DIR)
    max_workers: int = Field(default=4, ge=1, le=16)
    # Ingest batch = 한 번에 처리할 문서/청크 묶음 크기. embedding encode batch
    # (config.weights.pipeline.embedding_batch_size, 모델 forward pass 단위) 와
    # 구분됨 — PR5 rename (PipelineSettings.batch_size → ingest_batch_size).
    ingest_batch_size: int = Field(default=50, ge=10, le=500)
    incremental_mode: bool = Field(default=True)
    force_rebuild: bool = Field(default=False)
    # PR-2 (D) — embedding retry 정책. exponential backoff with jitter.
    embed_max_retries: int = Field(default=4, ge=1, le=10)
    embed_initial_backoff_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    embed_max_backoff_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    # PR-4 (C) — 파일 단위 동시 ingest 개수 (CLI/Crawl). API 는 별도 Sem(4).
    file_parallel: int = Field(default=4, ge=1, le=32)
    # PR-3 (F) — OCR ProcessPool worker 수. 0 = min(4, cpu_count).
    ocr_pool_workers: int = Field(default=0, ge=0, le=16)

    @property
    def output_dir(self) -> str:
        return os.path.join(self.runtime_base_dir, "pipeline")

    @property
    def crawl_dir(self) -> str:
        return os.path.join(self.runtime_base_dir, "full_crawl")


class AuthSettings(BaseSettings):
    """Auth configuration. Set AUTH_ENABLED=true to activate."""

    model_config = SettingsConfigDict(env_prefix="AUTH_")

    enabled: bool = Field(default=False)
    provider: str = Field(default="local")  # local | keycloak | azure_ad | internal

    # Internal JWT (for AUTH_PROVIDER=internal)
    jwt_secret: str = Field(default="")  # Required for internal provider
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_expire_minutes: int = Field(default=60)
    jwt_refresh_expire_hours: int = Field(default=8)
    jwt_issuer: str = Field(default="axiomedge-api")
    cookie_secure: bool = Field(default=False)  # True in production (HTTPS)

    # Keycloak
    keycloak_url: str = Field(default="")
    keycloak_realm: str = Field(default="knowledge")
    keycloak_client_id: str = Field(default="knowledge-local")
    keycloak_client_secret: str = Field(default="")

    # Azure AD
    azure_ad_tenant_id: str = Field(default="")
    azure_ad_client_id: str = Field(default="")

    # Local dev API keys (JSON: {"key": {"email": "...", "name": "...", "roles": [...]}})
    local_api_keys: str = Field(default="{}")


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")

    url: str = Field(default="redis://localhost:6379")


class SecretBoxSettings(BaseSettings):
    """Secret-at-rest 설정 — connector token 등 사용자 입력 secret 의 암호화 저장.

    backend=fernet (default): application-level Fernet (cryptography) — KEY env
    필요. on-prem 본업이라 별도 인프라 0 으로 작동.

    backend=vault (옵션): HashiCorp Vault self-host — 큰 고객사 (FIPS-140,
    HSM, BYOK) 대응. hvac client 자동 활성화.

    KEY 회전: ``SECRET_BOX_KEY_PREVIOUS`` 에 옛 키 두면 MultiFernet 가 fallback
    decrypt. 모든 row re-encrypt 후 PREVIOUS 제거.
    """

    model_config = SettingsConfigDict(env_prefix="SECRET_BOX_")

    backend: str = Field(default="fernet")  # fernet | vault
    key: str = Field(default="")  # Fernet base64-url-safe 32byte. backend=fernet 시 필수.
    key_previous: str = Field(default="")  # 회전 진행 중 fallback decrypt 용

    # Vault (backend=vault 시 필수). hvac KV v2 기준.
    # 최종 경로: ``{mount_point}/data/{path_prefix}/{path}`` — path 는 호출자가
    # 넘기는 ``org/{org_id}/data-source/{source_id}`` 같은 namespace.
    vault_addr: str = Field(default="")
    vault_token: str = Field(default="")
    vault_mount_point: str = Field(default="secret")
    vault_path_prefix: str = Field(default="axiomedge")
    vault_namespace: str = Field(default="")  # Vault Enterprise namespace (옵션)


class ConfluenceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CONFLUENCE_")

    base_url: str = Field(default="https://wiki.gsretail.com")


class TeiSettings(BaseSettings):
    """TEI (Text Embeddings Inference) 서버 설정 — BGE-M3 embedding + reranker."""

    model_config = SettingsConfigDict(env_prefix="")

    embedding_url: str = Field(
        default="http://localhost:8080",
        alias="BGE_TEI_URL",
    )
    reranker_url: str = Field(
        default="",
        alias="RERANKER_TEI_URL",
    )


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)


class DashboardSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASHBOARD_")

    api_url: str = Field(default="http://localhost:8000")
    api_timeout: int = Field(default=30)
    search_timeout: int = Field(default=60)


class AwsSettings(BaseSettings):
    """AWS 인프라 설정 — SageMaker, S3 등."""

    model_config = SettingsConfigDict(env_prefix="")

    region: str = Field(default="ap-northeast-2", alias="AWS_REGION")
    profile: str = Field(default="", alias="AWS_PROFILE")
    sagemaker_endpoint: str = Field(
        default="", alias="SAGEMAKER_ENDPOINT_NAME",
        description="SageMaker endpoint name. Required when USE_SAGEMAKER_LLM=true.",
    )
    s3_model_bucket: str = Field(
        default="", alias="DISTILL_S3_BUCKET",
        description="S3 bucket for edge model artifacts. Required for distill deploy.",
    )
    # Bulk upload (presigned URL flow) 전용 설정 — distill 모델 bucket 과 분리.
    # MinIO (on-prem) 또는 AWS S3 (cloud) — endpoint_url 만 다르고 코드 동일.
    s3_endpoint_url: str = Field(
        default="", alias="AWS_S3_ENDPOINT_URL",
        description="S3 API endpoint override. MinIO 시 'http://minio:9000', AWS 시 빈 값.",
    )
    s3_uploads_bucket: str = Field(
        default="axiomedge-uploads", alias="UPLOADS_S3_BUCKET",
        description="Bulk upload presigned URL flow 의 S3 bucket.",
    )
    s3_uploads_prefix: str = Field(
        default="uploads/", alias="UPLOADS_S3_PREFIX",
        description="Bulk upload S3 key prefix (사용자 격리는 자동 user/{uid}/ 추가).",
    )
    s3_uploads_url_ttl: int = Field(
        default=3600, alias="UPLOADS_S3_URL_TTL",
        description="Presigned PUT URL 유효시간 (초). 기본 1시간.",
    )


class DistillSettings(BaseSettings):
    """Distill Plugin 인프라 설정. 프로필(학습 파라미터)은 distill.yaml/DB에서 관리."""

    model_config = SettingsConfigDict(env_prefix="DISTILL_")

    enabled: bool = Field(default=True, description="Distill 플러그인 활성화")
    config_path: str = Field(default="deploy/distill.yaml", description="프로필 YAML 경로")
    work_dir: str = Field(default="/tmp/distill", description="빌드 작업 디렉토리")
    llm_concurrency: int = Field(default=3, ge=1, le=10, description="Teacher LLM 동시 호출 수")
    llm_timeout_sec: int = Field(default=120, ge=10, description="Teacher LLM 호출 타임아웃")
    build_timeout_sec: int = Field(default=7200, ge=300, description="빌드 전체 타임아웃")
    log_full_context: bool = Field(default=False, description="usage_log에 answer+chunks 저장")
    rag_api_url: str = Field(default="http://localhost:8000", description="재학습 시 Teacher RAG URL")


class TreeIndexSettings(BaseSettings):
    """문서 구조 트리 인덱스 설정 (heading_path 기반 Neo4j 트리 + RAPTOR식 요약)."""

    model_config = SettingsConfigDict(env_prefix="TREE_INDEX_")

    enabled: bool = Field(default=True, description="트리 인덱스 활성화")
    # 수단 1: 형제 확장
    sibling_window: int = Field(default=2, ge=0, le=5, description="형제 청크 확장 범위")
    max_tree_chunks_per_hit: int = Field(default=4, ge=1, le=10, description="히트당 최대 확장 청크")
    max_context_chars: int = Field(default=8000, ge=1000, description="트리 확장 최대 문자 수")
    sibling_score_decay: float = Field(default=0.85, ge=0.5, le=1.0, description="확장 청크 점수 감소율")
    section_title_search: bool = Field(default=True, description="섹션 제목 fulltext 검색 활성화")
    # 수단 2: 요약 트리 (Phase 2)
    summary_enabled: bool = Field(default=False, description="RAPTOR식 요약 트리 생성")
    summary_max_layers: int = Field(default=3, ge=1, le=5, description="요약 트리 최대 계층")
    summary_cluster_min_chunks: int = Field(default=5, ge=2, description="클러스터링 최소 청크 수")
    summary_umap_dim: int = Field(default=10, ge=2, le=50, description="UMAP 축소 차원")
    # 수단 3: 리랭킹/CRAG
    section_bonus: float = Field(default=0.05, ge=0.0, le=0.2, description="같은 섹션 보너스")
    adaptive_depth: bool = Field(default=True, description="쿼리 분류 연동 적응형 깊이")


class NotificationSettings(BaseSettings):
    """Slack + alert thresholds for ops notifications (Phase 5b)."""

    model_config = SettingsConfigDict(env_prefix="NOTIF_", extra="ignore")

    slack_webhook_url: str | None = Field(default=None)
    candidate_pending_threshold: int = Field(default=50, ge=1)
    yaml_pr_stale_hours: int = Field(default=48, ge=1)
    bootstrap_failure_streak: int = Field(default=3, ge=1)
    # PR-6 (E) — Ingestion failure alert thresholds.
    ingestion_failure_streak: int = Field(default=3, ge=1)
    ingestion_failure_window_hours: int = Field(default=24, ge=1)
    ingestion_alert_dedup_minutes: int = Field(default=120, ge=10)


class Settings(BaseSettings):
    """Top-level aggregated settings."""

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    secret_box: SecretBoxSettings = Field(default_factory=SecretBoxSettings)
    confluence: ConfluenceSettings = Field(default_factory=ConfluenceSettings)
    tei: TeiSettings = Field(default_factory=TeiSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    aws: AwsSettings = Field(default_factory=AwsSettings)
    distill: DistillSettings = Field(default_factory=DistillSettings)
    tree_index: TreeIndexSettings = Field(default_factory=TreeIndexSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()
