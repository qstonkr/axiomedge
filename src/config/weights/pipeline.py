"""Pipeline, chunking, dedup, and OCR parameters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OCRConfig:
    """OCR processing parameters."""

    paddle_model: str = "korean_PP-OCRv5_mobile_rec"
    use_gpu: bool = False
    enable_orientation: bool = True
    min_confidence: float = 0.3

    min_text_chars_per_page: int = 30
    max_image_dimension: int = 2048
    cv_max_workers: int = 2
    enable_vision_analysis: bool = False


@dataclass(frozen=True)
class ChunkingConfig:
    """Text chunking parameters."""

    max_chunk_chars: int = 2500
    overlap_sentences: int = 1
    max_chunks_per_document: int = 500
    graphrag_max_document_length: int = 15000
    kss_max_chars: int = 2000  # KSS 호출당 최대 문자수 (pecab hang 방지)
    ocr_correction_chunk_size: int = 2000  # OCR 교정 chunk 크기


@dataclass(frozen=True)
class PipelineConfig:
    """Ingestion pipeline parameters."""

    max_chunk_chars: int = 2500
    chunk_overlap_sentences: int = 1
    max_chunks_per_document: int = 500
    max_payload_content_length: int = 8000

    embedding_batch_size: int = 32
    qdrant_upsert_batch_size: int = 64
    neo4j_batch_size: int = 5000

    # 200MB → 5GB (5120MB) — 대용량 PDF/PPTX 동영상 첨부 등 수용.
    # ⚠️ 메모리 위험: ``ingest.py`` 의 ``await file.read()`` 가 전체를 RAM 로드 —
    # 동시 업로드 N건 시 5N GB. 운영 시 streaming upload 또는 ASGI/nginx
    # body cap 별도 검토 권장. ingestion_gate IG-07 은 본 값 그대로 사용.
    max_file_size_mb: int = 5120
    max_workers: int = 4


@dataclass(frozen=True)
class DedupConfig:
    """4-Stage dedup pipeline thresholds."""

    near_duplicate_threshold: float = 0.80
    semantic_duplicate_threshold: float = 0.90
    stage3_skip_threshold: float = 0.85
    enable_stage4: bool = True
    bloom_expected_items: int = 100000
    bloom_false_positive_rate: float = 0.01
