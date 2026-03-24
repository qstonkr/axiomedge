"""QdrantClientProvider -- shared client, config, and payload helpers.

Standalone version extracted from oreo-ecosystem.
StatsD / Datadog metric calls removed; FeatureFlags replaced with config booleans.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config_weights import weights as _w

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inlined KnowledgeSearchResult (originally from domain/knowledge/interfaces)
# ---------------------------------------------------------------------------


@dataclass
class QdrantSearchResult:
    """Backend-agnostic search result from a knowledge vector store.

    Attributes:
        point_id: Unique vector point ID
        score: Similarity score (0.0 - 1.0, higher = more similar)
        content: Document chunk content
        metadata: Additional payload metadata
    """

    point_id: str
    score: float
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# RRF Fusion Weights
# ---------------------------------------------------------------------------

DEFAULT_DENSE_WEIGHT = _w.hybrid_search.dense_weight
DEFAULT_SPARSE_WEIGHT = _w.hybrid_search.sparse_weight
DEFAULT_COLBERT_WEIGHT = _w.hybrid_search.colbert_weight

# RRF constant (standard value)
RRF_K = 60

# Collection existence cache TTL (seconds)
COLLECTION_CACHE_TTL_S = float(os.getenv("QDRANT_COLLECTION_CACHE_TTL_S", "300"))

# Admin stats cache TTL (seconds)
ADMIN_STATS_CACHE_TTL_S = float(os.getenv("QDRANT_ADMIN_STATS_CACHE_TTL", "45"))

# Named vectors (BGE-M3 default)
DEFAULT_DENSE_VECTOR_NAME = "bge_dense"
DEFAULT_SPARSE_VECTOR_NAME = "bge_sparse"
RETRIEVAL_PAYLOAD_FIELDS: tuple[str, ...] = (
    "kb_id",
    "document_id",
    "source_uri",
)
DEFAULT_HYDRATION_EXCLUDE_FIELDS: tuple[str, ...] = (
    "colbert_vectors",
    "raw_content",
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class QdrantConfig:
    """Qdrant connection configuration."""

    url: str = ""
    api_key: str | None = None
    grpc_port: int = 6334
    prefer_grpc: bool = True
    dense_dimension: int = 1024
    dense_vector_name: str = DEFAULT_DENSE_VECTOR_NAME
    sparse_vector_name: str = DEFAULT_SPARSE_VECTOR_NAME
    collection_prefix: str = "kb"
    timeout: int = 120
    clone_batch_size: int = 64
    collection_name_overrides: dict[str, str] = field(default_factory=dict)
    # Replaces FeatureFlags.is_knowledge_embedding_version_tracking_enabled()
    embedding_version_tracking_enabled: bool = True
    # Replaces FeatureFlags.is_retrieval_projection_enabled()
    retrieval_projection_enabled: bool = True
    # Replaces FeatureFlags.get_qdrant_hybrid_prefetch_multiplier()
    hybrid_prefetch_multiplier: int = _w.hybrid_search.prefetch_multiplier
    # Replaces FeatureFlags.get_qdrant_hybrid_prefetch_max()
    hybrid_prefetch_max: int = _w.hybrid_search.prefetch_max
    # Replaces FeatureFlags.get_qdrant_colbert_rerank_candidate_multiplier()
    colbert_rerank_candidate_multiplier: int = _w.hybrid_search.colbert_rerank_candidate_multiplier

    @classmethod
    def from_env(cls) -> QdrantConfig:
        """Load config from environment variables."""
        overrides: dict[str, str] = {}
        raw = os.getenv("QDRANT_COLLECTION_MAPPING", "")
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                kb_id, col_name = pair.split(":", 1)
                overrides[kb_id.strip()] = col_name.strip()

        return cls(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            api_key=os.getenv("QDRANT_API_KEY"),
            grpc_port=int(os.getenv("QDRANT_GRPC_PORT", "6334")),
            prefer_grpc=os.getenv("QDRANT_PREFER_GRPC", "true").lower() == "true",
            dense_dimension=int(os.getenv("QDRANT_DENSE_DIMENSION", "1024")),
            dense_vector_name=os.getenv("QDRANT_DENSE_VECTOR_NAME", DEFAULT_DENSE_VECTOR_NAME),
            sparse_vector_name=os.getenv("QDRANT_SPARSE_VECTOR_NAME", DEFAULT_SPARSE_VECTOR_NAME),
            collection_prefix=os.getenv("QDRANT_COLLECTION_PREFIX", "kb"),
            timeout=int(os.getenv("QDRANT_TIMEOUT", "120")),
            clone_batch_size=int(os.getenv("QDRANT_CLONE_BATCH_SIZE", "64")),
            collection_name_overrides=overrides,
            embedding_version_tracking_enabled=(
                os.getenv("KNOWLEDGE_EMBEDDING_VERSION_TRACKING", "true").lower() == "true"
            ),
            retrieval_projection_enabled=(
                os.getenv("RETRIEVAL_PROJECTION_ENABLED", "true").lower() == "true"
            ),
            hybrid_prefetch_multiplier=int(os.getenv("QDRANT_HYBRID_PREFETCH_MULTIPLIER", str(_w.hybrid_search.prefetch_multiplier))),
            hybrid_prefetch_max=int(os.getenv("QDRANT_HYBRID_PREFETCH_MAX", str(_w.hybrid_search.prefetch_max))),
            colbert_rerank_candidate_multiplier=int(
                os.getenv("QDRANT_COLBERT_RERANK_CANDIDATE_MULTIPLIER", str(_w.hybrid_search.colbert_rerank_candidate_multiplier))
            ),
        )


# ---------------------------------------------------------------------------
# Client provider
# ---------------------------------------------------------------------------


class QdrantClientProvider:
    """Shared Qdrant client, configuration, and payload helpers.

    Metric emission methods are retained as no-ops so call-sites in search/store
    modules compile without changes. They simply do nothing.
    """

    def __init__(self, config: QdrantConfig | None = None) -> None:
        self.config = config or QdrantConfig.from_env()
        self._client: Any = None
        self._client_lock = asyncio.Lock()

    async def ensure_client(self) -> Any:
        """Lazy client initialization with double-checked locking."""
        if self._client is not None:
            return self._client

        async with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                from qdrant_client import AsyncQdrantClient

                self._client = AsyncQdrantClient(
                    url=self.config.url,
                    api_key=self.config.api_key,
                    grpc_port=self.config.grpc_port,
                    prefer_grpc=self.config.prefer_grpc,
                    timeout=self.config.timeout,
                )
                logger.info(
                    "Qdrant client initialized",
                    extra={"url": self.config.url, "timeout": self.config.timeout},
                )
                return self._client
            except ImportError as exc:
                raise ImportError(
                    "qdrant-client package required. Install: pip install qdrant-client>=1.12"
                ) from exc

    async def close(self) -> None:
        """Close the Qdrant client connection."""
        if self._client:
            await self._client.close()
            self._client = None

    # ==================== Metric helpers (no-ops) ====================

    def build_metric_tags(
        self,
        *,
        kb_id: str | None = None,
        collection_name: str | None = None,
        mode: str | None = None,
    ) -> list[str]:
        tags = [f"env:{os.getenv('ENVIRONMENT', 'local')}"]
        if kb_id:
            tags.append(f"kb_id:{kb_id}")
        if collection_name:
            tags.append(f"collection:{collection_name}")
        if mode:
            tags.append(f"mode:{mode}")
        return tags

    def emit_search_metric(
        self, metric_name: str, value: float | int, tags: list[str] | None = None
    ) -> None:
        """No-op: StatsD removed."""

    def emit_histogram(self, metric: str, value: float, *, tags: list[str]) -> None:
        """No-op: StatsD removed."""

    def emit_gauge(self, metric: str, value: float, *, tags: list[str]) -> None:
        """No-op: StatsD removed."""

    def emit_increment(self, metric: str, *, value: int = 1, tags: list[str]) -> None:
        """No-op: StatsD removed."""

    # ==================== Payload utilities ====================

    @staticmethod
    def build_hydration_payload_selector(
        exclude_fields: list[str] | tuple[str, ...] | None,
    ) -> Any | None:
        fields = [f for f in (exclude_fields or []) if f]
        if not fields:
            return True
        try:
            from qdrant_client.models import PayloadSelectorExclude

            return PayloadSelectorExclude(exclude=fields)
        except Exception:
            return True

    @staticmethod
    def build_retrieval_payload_selector(
        include_fields: list[str] | tuple[str, ...] | None,
    ) -> Any | None:
        fields = [f for f in (include_fields or []) if f]
        if not fields:
            return False
        try:
            from qdrant_client.models import PayloadSelectorInclude

            return PayloadSelectorInclude(include=fields)
        except Exception:
            return fields

    @staticmethod
    def estimate_result_payload_bytes(result: QdrantSearchResult) -> int:
        metadata_bytes = len(
            json.dumps(
                result.metadata,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        content_bytes = len((result.content or "").encode("utf-8"))
        return content_bytes + metadata_bytes

    @staticmethod
    def merge_search_result_payloads(
        base_result: QdrantSearchResult,
        hydrated_result: QdrantSearchResult | None,
    ) -> QdrantSearchResult:
        if hydrated_result is None:
            return base_result
        metadata = dict(hydrated_result.metadata)
        metadata.update(
            {
                key: value
                for key, value in base_result.metadata.items()
                if key not in metadata
            }
        )
        return QdrantSearchResult(
            point_id=base_result.point_id,
            score=base_result.score,
            content=hydrated_result.content or base_result.content,
            metadata=metadata,
        )

    @staticmethod
    def augment_payload_with_embedding_tracking(
        payload: dict[str, Any],
        *,
        dense_dimension: int | None = None,
        config: QdrantConfig | None = None,
    ) -> dict[str, Any]:
        """Inject embedding version-tracking metadata.

        Uses ``config.embedding_version_tracking_enabled`` instead of FeatureFlags.
        """
        enabled = True
        if config is not None:
            enabled = config.embedding_version_tracking_enabled

        if not enabled:
            return payload

        if not payload.get("embedding_model"):
            payload["embedding_model"] = (
                os.getenv("KNOWLEDGE_EMBEDDING_MODEL")
                or os.getenv("OLLAMA_EMBEDDING_MODEL")
                or "unknown"
            )
        if not payload.get("embedding_version"):
            payload["embedding_version"] = os.getenv("KNOWLEDGE_EMBEDDING_VERSION", "unknown")
        if dense_dimension is not None and not payload.get("embedding_dimension"):
            payload["embedding_dimension"] = int(dense_dimension)
        payload.setdefault("indexed_at", datetime.now(UTC).isoformat())
        return payload
