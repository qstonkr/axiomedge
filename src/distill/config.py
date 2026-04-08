"""Distill 빌드 프로필 설정 관리.

distill.yaml 로드/저장/검증. DB 저장 시 시드 데이터로 사용.

인프라 설정(work_dir, timeout 등)은 src/config.py의 DistillSettings(SSOT).
이 파일은 프로필(학습 파라미터) 스키마만 담당.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 빌드 상태 상수 (SSOT — 매직 스트링 제거)
# ---------------------------------------------------------------------------

BUILD_STATUS_PENDING = "pending"
BUILD_STATUS_GENERATING = "generating"
BUILD_STATUS_TRAINING = "training"
BUILD_STATUS_EVALUATING = "evaluating"
BUILD_STATUS_QUANTIZING = "quantizing"
BUILD_STATUS_DEPLOYING = "deploying"
BUILD_STATUS_COMPLETED = "completed"
BUILD_STATUS_FAILED = "failed"
BUILD_STATUSES_RUNNING = (
    BUILD_STATUS_GENERATING, BUILD_STATUS_TRAINING,
    BUILD_STATUS_EVALUATING, BUILD_STATUS_QUANTIZING, BUILD_STATUS_DEPLOYING,
)
VALID_BUILD_STEPS = frozenset({"generate", "train", "evaluate", "quantize", "deploy"})

# 데이터 생성 상수
MIN_CHUNK_LENGTH = 50
ESTIMATED_CHARS_PER_TOKEN = 2.0


# ---------------------------------------------------------------------------
# Config Models
# ---------------------------------------------------------------------------

class LoRAConfig(BaseModel):
    r: int = Field(8, ge=4, le=64)
    alpha: int = Field(16, ge=8, le=128)
    dropout: float = Field(0.05, ge=0.0, le=0.5)
    target_modules: list[str] = Field(
        default_factory=lambda: ["q_proj", "v_proj", "k_proj", "o_proj"],
    )


class TrainingConfig(BaseModel):
    epochs: int = Field(3, ge=1, le=50)
    batch_size: int = Field(4, ge=1, le=128)
    gradient_accumulation: int = Field(8, ge=1, le=64)
    learning_rate: float = Field(2e-4, gt=0)
    max_seq_length: int = Field(512, ge=128, le=4096)


class QAStyleConfig(BaseModel):
    mode: str = "concise"  # concise | detailed
    max_answer_tokens: int = Field(256, ge=64, le=2048)
    answer_only_ratio: float = Field(0.8, ge=0.0, le=1.0)
    mix_ratio: dict[str, float] = Field(
        default_factory=lambda: {"memorize": 0.6, "rag_reference": 0.4},
    )


class DataQualityConfig(BaseModel):
    self_consistency_samples: int = 3
    self_consistency_threshold: float = 0.75
    enable_self_consistency: bool = True
    augmentation_count: int = 3


class DeployConfig(BaseModel):
    s3_bucket: str = "oreo-dev-ml-artifacts"
    s3_prefix: str = "models/edge/"
    app_s3_prefix: str = "apps/edge/"  # 앱 바이너리 저장 경로
    auto_update_cron: str = "0 3 * * 1"
    quantize: str = "q4_k_m"


class EvalThreshold(BaseModel):
    faithfulness: float = 0.55
    relevancy: float = 0.65


class DistillDefaults(BaseModel):
    teacher_model: str = "exaone-sagemaker"
    quantize: str = "q4_k_m"
    min_training_samples: int = 5000
    eval_threshold: EvalThreshold = Field(default_factory=EvalThreshold)
    training_backend: str = "local"  # local | sagemaker
    build_timeout_sec: int = 7200


class DistillProfile(BaseModel):
    enabled: bool = False
    description: str = ""
    search_group: str
    base_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    lora: LoRAConfig = Field(default_factory=LoRAConfig)
    training: TrainingConfig = Field(default_factory=TrainingConfig)
    qa_style: QAStyleConfig = Field(default_factory=QAStyleConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    deploy: DeployConfig = Field(default_factory=DeployConfig)


class DistillConfig(BaseModel):
    defaults: DistillDefaults = Field(default_factory=DistillDefaults)
    profiles: dict[str, DistillProfile] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------

def load_config(path: Path | None = None) -> DistillConfig:
    """distill.yaml 로드. 경로 미지정 시 Settings.distill.config_path 사용."""
    if path is None:
        from src.config import get_settings
        path = Path(get_settings().distill.config_path)
    if not path.exists():
        logger.info("distill.yaml not found at %s, using empty config", path)
        return DistillConfig()
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        config = DistillConfig(**data)
        logger.info("Loaded distill config: %d profiles", len(config.profiles))
        return config
    except Exception as e:
        logger.error("Failed to load distill.yaml: %s", e)
        return DistillConfig()


def save_config(config: DistillConfig, path: Path | None = None) -> None:
    """설정을 distill.yaml로 저장."""
    if path is None:
        from src.config import get_settings
        path = Path(get_settings().distill.config_path)
    data = config.model_dump()
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info("Saved distill config to %s", path)


def profile_to_dict(profile: DistillProfile) -> dict[str, Any]:
    """프로필을 dict로 변환 (DB 저장/API 응답용)."""
    return profile.model_dump()


def dict_to_profile(data: dict[str, Any]) -> DistillProfile:
    """dict에서 프로필 생성 (DB 로드/API 요청용)."""
    return DistillProfile(**data)
