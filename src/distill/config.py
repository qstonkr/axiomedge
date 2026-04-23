"""Distill 빌드 운영 유틸 — 빌드 상태 상수 + distill.yaml YAML I/O.

### PR11 config 재편 (2026-04-23)

Profile Pydantic 모델 (``DistillProfile`` 등) 은 ``src/config/profiles.py`` 로
이동됐다. 이 파일은 **facade re-export** 로 backward-compat 유지.

### Config 3파일 경계

| 파일 | 역할 |
|---|---|
| ``src/config/settings.py`` | **인프라** — DB, Qdrant, Ollama, timeout, 연결 풀 |
| ``src/config/weights/`` | **하이퍼파라미터** — 검색 가중치, threshold, chunk 크기 |
| ``src/config/profiles.py`` | **Distill 프로필** — LoRA, training, QA style, deploy |
| ``src/distill/config.py`` (이 파일) | **운영** — 빌드 상태 상수, YAML I/O |

인프라 설정(work_dir, timeout 등)은 ``src/config/settings.py::DistillSettings``
(SSOT).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

# Facade re-export (PR11) — 프로필 Pydantic 모델은 src/config/profiles.py 가 SSOT
from src.config.profiles import (
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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


__all__ = [
    "BUILD_STATUSES_RUNNING",
    "BUILD_STATUS_COMPLETED",
    "BUILD_STATUS_DEPLOYING",
    "BUILD_STATUS_EVALUATING",
    "BUILD_STATUS_FAILED",
    "BUILD_STATUS_GENERATING",
    "BUILD_STATUS_PENDING",
    "BUILD_STATUS_QUANTIZING",
    "BUILD_STATUS_TRAINING",
    "DataQualityConfig",
    "DeployConfig",
    "DistillConfig",
    "DistillDefaults",
    "DistillProfile",
    "ESTIMATED_CHARS_PER_TOKEN",
    "EvalThreshold",
    "LoRAConfig",
    "MIN_CHUNK_LENGTH",
    "QAStyleConfig",
    "TrainingConfig",
    "VALID_BUILD_STEPS",
    "dict_to_profile",
    "load_config",
    "profile_to_dict",
    "save_config",
]
