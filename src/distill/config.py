"""Distill 빌드 프로필 설정 관리.

distill.yaml 로드/저장/검증. DB 저장 시 시드 데이터로 사용.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DISTILL_YAML_PATH = Path("distill.yaml")


# ---------------------------------------------------------------------------
# Config Models
# ---------------------------------------------------------------------------

class LoRAConfig(BaseModel):
    r: int = 8
    alpha: int = 16
    dropout: float = 0.05
    target_modules: list[str] = Field(
        default_factory=lambda: ["q_proj", "v_proj", "k_proj", "o_proj"],
    )


class TrainingConfig(BaseModel):
    epochs: int = 3
    batch_size: int = 4
    gradient_accumulation: int = 8
    learning_rate: float = 2e-4
    max_seq_length: int = 512


class QAStyleConfig(BaseModel):
    mode: str = "concise"  # concise | detailed
    max_answer_tokens: int = 256
    answer_only_ratio: float = 0.8
    mix_ratio: dict[str, float] = Field(
        default_factory=lambda: {"memorize": 0.6, "rag_reference": 0.4},
    )


class DataQualityConfig(BaseModel):
    self_consistency_samples: int = 3
    self_consistency_threshold: float = 0.75
    enable_self_consistency: bool = True
    augmentation_count: int = 3


class DeployConfig(BaseModel):
    s3_bucket: str = "gs-knowledge-models"
    s3_prefix: str = ""
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

def load_config(path: Path = DISTILL_YAML_PATH) -> DistillConfig:
    """distill.yaml 로드. 파일 없으면 빈 설정 반환."""
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


def save_config(config: DistillConfig, path: Path = DISTILL_YAML_PATH) -> None:
    """설정을 distill.yaml로 저장."""
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
