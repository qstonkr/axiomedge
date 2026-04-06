"""Distill Plugin DB 모델.

별도 Base 사용 — RAG 코어 models.py와 독립. 테이블 생성도 별도 스크립트.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


def _utc_now():
    return datetime.now(timezone.utc)


class DistillBase(DeclarativeBase):
    """Distill 전용 Base. RAG 코어와 분리."""

    pass


# ---------------------------------------------------------------------------
# Profiles — 빌드 설정 (distill.yaml → DB 이관)
# ---------------------------------------------------------------------------

class DistillProfileModel(DistillBase):
    """빌드 프로필 설정."""

    __tablename__ = "distill_profiles"

    name = Column(String(100), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    description = Column(Text, default="")
    search_group = Column(String(100), nullable=False)
    base_model = Column(String(200), nullable=False, default="Qwen/Qwen2.5-0.5B-Instruct")
    config = Column(Text, nullable=False, default="{}")  # JSON: lora, training, qa_style 등
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)


# ---------------------------------------------------------------------------
# Builds — 빌드/학습 이력
# ---------------------------------------------------------------------------

class DistillBuildModel(DistillBase):
    """빌드 (학습) 실행 이력."""

    __tablename__ = "distill_builds"

    id = Column(String(36), primary_key=True)
    profile_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    # pending → generating → training → evaluating → quantizing → deploying → completed / failed
    version = Column(String(50), nullable=False)
    search_group = Column(String(100), nullable=False)
    base_model = Column(String(200), nullable=False)
    config_snapshot = Column(Text, default="{}")  # JSON: 빌드 시점 전체 설정

    # 데이터 생성
    training_samples = Column(Integer, default=0)
    data_sources = Column(Text, default="{}")  # JSON: {"chunk_qa": N, "usage_log": N, "retrain": N}

    # 학습 메트릭
    train_loss = Column(Float, nullable=True)
    eval_loss = Column(Float, nullable=True)
    training_duration_sec = Column(Integer, nullable=True)

    # 평가 메트릭
    eval_faithfulness = Column(Float, nullable=True)
    eval_relevancy = Column(Float, nullable=True)
    eval_passed = Column(Boolean, nullable=True)

    # 양자화
    gguf_size_mb = Column(Float, nullable=True)
    quantize_method = Column(String(20), nullable=True)

    # 배포
    s3_uri = Column(String(500), nullable=True)
    deployed_at = Column(DateTime(timezone=True), nullable=True)

    # 에러
    error_message = Column(Text, nullable=True)
    error_step = Column(String(30), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        Index("idx_distill_build_profile", "profile_name"),
        Index("idx_distill_build_status", "status"),
    )


# ---------------------------------------------------------------------------
# Edge Logs — 엣지 서버 사용 로그
# ---------------------------------------------------------------------------

class DistillEdgeLogModel(DistillBase):
    """엣지 서버에서 수집된 사용 로그."""

    __tablename__ = "distill_edge_logs"

    id = Column(String(36), primary_key=True)
    profile_name = Column(String(100), nullable=False)
    store_id = Column(String(100), nullable=False)
    query = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    success = Column(Boolean, default=True)
    model_version = Column(String(50), nullable=True)
    edge_timestamp = Column(DateTime(timezone=True), nullable=False)
    collected_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        Index("idx_edge_log_profile", "profile_name"),
        Index("idx_edge_log_store", "store_id"),
        Index("idx_edge_log_success", "success"),
    )


# ---------------------------------------------------------------------------
# Training Data — 학습 데이터
# ---------------------------------------------------------------------------

class DistillTrainingDataModel(DistillBase):
    """학습 데이터 (QA 쌍)."""

    __tablename__ = "distill_training_data"

    id = Column(String(36), primary_key=True)
    profile_name = Column(String(100), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    source_type = Column(String(20), nullable=False)  # chunk_qa | usage_log | retrain | manual
    source_id = Column(String(255), nullable=True)
    kb_id = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="approved")  # pending | approved | rejected
    used_in_build = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        Index("idx_train_data_profile", "profile_name"),
        Index("idx_train_data_source", "source_type"),
        Index("idx_train_data_status", "status"),
    )
