"""Distill Plugin DB 모델.

별도 Base 사용 — RAG 코어 models.py와 독립. 테이블 생성도 별도 스크립트.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DistillBase(DeclarativeBase):
    """Distill 전용 Base. RAG 코어와 분리."""

    pass


# ---------------------------------------------------------------------------
# Base Models — 프로필 생성 시 선택 가능한 베이스 모델 레지스트리
# ---------------------------------------------------------------------------

class DistillBaseModelEntry(DistillBase):
    """베이스 모델 후보 레지스트리.

    대시보드 드롭다운의 SSOT. 모델 추가/비활성화를 코드 배포 없이 DB 에서
    관리할 수 있게 하고, 검증 상태/라이선스/주의사항 메타데이터를 함께 보관.
    """

    __tablename__ = "distill_base_models"

    hf_id = Column(String(200), primary_key=True)  # e.g. "google/gemma-3-4b-it"
    display_name = Column(String(200), nullable=False)  # e.g. "Gemma 3 4B it"
    params = Column(String(20), nullable=True)  # e.g. "4B"
    license = Column(String(100), nullable=True)  # e.g. "Gemma", "Apache 2.0"
    commercial_use = Column(Boolean, nullable=False, default=False)  # 상업 배포 허용
    verified = Column(Boolean, nullable=False, default=False)  # 엣지 스택 검증 완료
    notes = Column(Text, default="")  # 주의사항/라벨 (e.g. "research-only", "미검증")
    enabled = Column(Boolean, nullable=False, default=True)  # 드롭다운 노출 여부
    sort_order = Column(Integer, nullable=False, default=0)  # 표시 순서
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)


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
    # 디폴트 하드코딩 금지. 베이스 모델 선택은 distill_base_models 레지스트리
    # (SSOT). 호출자가 반드시 값을 지정해야 함 (nullable=False).
    base_model = Column(String(200), nullable=False)
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
    gguf_sha256 = Column(String(64), nullable=True)
    model_name = Column(String(100), nullable=True)  # 베이스 모델명 (예: Qwen2.5-0.5B)

    # 배포
    s3_uri = Column(String(500), nullable=True)
    deployed_at = Column(DateTime(timezone=True), nullable=True)
    rollback_from = Column(String(36), nullable=True)  # 롤백 시 이전 빌드 id
    # 평가 게이트 우회 — eval 데이터/baseline 부재 또는 의도된 회귀 빌드용.
    # 운영자가 명시 set 해야만 _evaluate 가 fail-closed 를 우회.
    force_deploy = Column(Boolean, nullable=False, default=False, server_default="false")

    # 에러
    error_message = Column(Text, nullable=True)
    error_step = Column(String(30), nullable=True)

    # 0008_distill_build_gpu_metadata.py — async sweeper 패턴 메타.
    # gpu_instance_id NULL = 신구조 미적용 (기존 fire-and-forget build 보호 위해
    # sweeper 가 NULL row 는 건드리지 않음).
    gpu_instance_id = Column(String(64), nullable=True)
    gpu_started_at = Column(DateTime(timezone=True), nullable=True)
    s3_result_key = Column(String(255), nullable=True)
    last_sweep_at = Column(DateTime(timezone=True), nullable=True)
    gpu_finished_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        Index("idx_distill_build_profile", "profile_name"),
        Index("idx_distill_build_status", "status"),
        # sweeper hot query — status='training' 중 sweep 안 된 row 빠르게.
        Index("idx_distill_build_status_sweep", "status", "last_sweep_at"),
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

    # 품질 점수 (큐레이션)
    consistency_score = Column(Float, nullable=True)
    generality_score = Column(Float, nullable=True)
    augmentation_verified = Column(Boolean, nullable=True)

    # 계보
    augmented_from = Column(String(36), nullable=True)  # 원본 QA id (변형인 경우)
    generation_batch_id = Column(String(36), nullable=True)
    # 원본 chunk 의 deterministic fingerprint — chunk-level train/test partition.
    # qa_generator.chunk_fingerprint(content)[:16].
    source_chunk_fp = Column(String(16), nullable=True)

    # 리뷰
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_comment = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_train_data_profile", "profile_name"),
        Index("idx_train_data_source", "source_type"),
        Index("idx_train_data_status", "status"),
        Index("idx_train_data_batch", "generation_batch_id"),
    )


# ---------------------------------------------------------------------------
# Edge Servers — 등록된 엣지 서버 (매장)
# ---------------------------------------------------------------------------

class DistillEdgeServerModel(DistillBase):
    """등록된 엣지 서버 (매장)."""

    __tablename__ = "distill_edge_servers"

    id = Column(String(36), primary_key=True)
    store_id = Column(String(100), nullable=False, unique=True)
    profile_name = Column(String(100), nullable=False)
    display_name = Column(String(200), nullable=True)

    # 서버 상태 (heartbeat push로 갱신)
    status = Column(String(20), nullable=False, default="unknown")
    last_heartbeat = Column(DateTime(timezone=True), nullable=True)
    server_ip = Column(String(45), nullable=True)
    os_type = Column(String(20), nullable=True)  # linux | windows | darwin

    # 앱 버전 (PyInstaller 바이너리)
    app_version = Column(String(50), nullable=True)

    # 모델 버전
    model_version = Column(String(50), nullable=True)
    model_sha256 = Column(String(64), nullable=True)

    # 시스템 정보 (heartbeat에서 수신)
    cpu_info = Column(String(100), nullable=True)
    ram_total_mb = Column(Integer, nullable=True)
    ram_used_mb = Column(Integer, nullable=True)
    disk_free_mb = Column(Integer, nullable=True)

    # 성능 (heartbeat에서 수신)
    avg_latency_ms = Column(Integer, nullable=True)
    total_queries = Column(Integer, default=0)
    success_rate = Column(Float, nullable=True)

    # 업데이트 요청 (중앙에서 설정, 엣지가 다음 sync 시 확인)
    pending_model_update = Column(Boolean, nullable=False, default=False)
    pending_app_update = Column(Boolean, nullable=False, default=False)

    # 인증
    api_key_hash = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        Index("idx_edge_server_store", "store_id"),
        Index("idx_edge_server_profile", "profile_name"),
        Index("idx_edge_server_status", "status"),
    )
