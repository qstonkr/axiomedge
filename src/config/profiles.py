"""Distill 빌드 프로필 Pydantic 모델 (SSOT for 학습 파라미터).

PR11 config 디렉터리 재편의 일환 — 프로필 모델을 `src/config/` 에 중앙화.
YAML I/O · build status 상수 등 distill-운영 코드는 `src/distill/config.py` 에
남고, 이 파일에서 import 해서 사용한다.

기존 import 경로 `from src.distill.config import DistillProfile, ...` 는 facade
re-export 로 유지된다 (backward-compat).

### 포함 대상

- `LoRAConfig`, `TrainingConfig`, `QAStyleConfig`, `DataQualityConfig`,
  `DeployConfig`, `EvalThreshold`
- `DistillDefaults`, `DistillProfile`, `DistillConfig`

### 포함 대상 아님

- 빌드 상태 상수 (`BUILD_STATUS_*`) → `src/distill/config.py`
- YAML I/O (`load_config`, `save_config`) → `src/distill/config.py`
- 인프라 timeout / work_dir → `src/config/settings.py::DistillSettings`
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoRAConfig(BaseModel):
    # r=16/alpha=32: 1B instruction-tuned 모델에 필요한 최소 학습 capacity.
    # 기존 r=8/alpha=16은 너무 작아서 학습 효과 없음 (검증됨).
    r: int = Field(16, ge=4, le=64)
    alpha: int = Field(32, ge=8, le=128)
    dropout: float = Field(0.05, ge=0.0, le=0.5)
    # Gemma 3 / LLaMA / Qwen 등 modern decoder 모델에서 factual 지식은 대부분
    # FFN (gate_proj / up_proj / down_proj) 에 저장된다. Attention 만 target
    # 하면 표면 패턴만 학습되고 학습 데이터 내용을 주입 못한다 (train_loss 가
    # 1.5~2.0 에서 정체되는 증상으로 나타남). Unsloth · QLoRA · HuggingFace
    # PEFT 공식 튜토리얼은 모두 attention + FFN 7 개를 target 한다.
    target_modules: list[str] = Field(
        default_factory=lambda: [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
    )


class TrainingConfig(BaseModel):
    # learning_rate=5e-5: -it (instruction-tuned) 모델에 2e-4는 과함 —
    # pretrained 가중치 교란 후 수렴 실패. 5e-5 가 안전선.
    # epochs=5: 3 epochs는 953 샘플 기준 부족. 5~7 권장.
    # max_seq_length=512: Reformatter (2문단 ~200~350자 포맷) 적용 후 Gemma 3
    # tokenizer 실측 결과 p99=347, max=405 tokens. 512 는 p99 대비 1.47배 여유
    # 로 0% truncation. 1024 는 63% padding 낭비 (이전 데이터 기준이었음).
    # 과거 1024 근거였던 "p99=1007 tokens"는 RAG style 긴 답변이었고, 지금은
    # reformatter 가 답변을 압축해서 적용 불가.
    epochs: int = Field(5, ge=1, le=50)
    batch_size: int = Field(4, ge=1, le=128)
    gradient_accumulation: int = Field(8, ge=1, le=64)
    learning_rate: float = Field(5e-5, gt=0)
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
    # ── 레거시 augmentation (dataset_builder.augment_questions) ──
    # NOTE: 이 값은 레거시 auto-generation 경로에서만 사용. 신규 경로는
    # question_augmenter_count 를 사용한다.
    augmentation_count: int = 3

    # ── Phase 1.5: Answer reformatter ──
    # 기존 긴 RAG 답변을 1B 모델이 학습하기 쉬운 2문단 포맷으로 재작성.
    # Reformatter 모듈: src/distill/data_gen/reformatter.py
    reformat_enabled: bool = False  # 신규 프로필은 True 권장

    # ── Phase 1.5: Question augmenter (LLM judge verification) ──
    # 하나의 fact 에 대해 N 개 질문 표현 생성 → exposures 증가로 memorization
    # 효과 극대화. Physics of LMs Part 3.3 의 "100 exposures for half capacity"
    # 이론에 기반.
    # 모듈: src/distill/data_gen/question_augmenter.py
    # 0 이면 신규 augmenter 비활성화 (레거시 augmentation_count 만 사용).
    question_augmenter_count: int = 0
    question_augmenter_verify: bool = True  # LLM judge (semantic + leak 검출)
    question_augmenter_concurrency: int = 4


class DeployConfig(BaseModel):
    s3_bucket: str = ""  # Required: set via DISTILL_S3_BUCKET env or distill.yaml
    s3_prefix: str = "models/edge/"
    app_s3_prefix: str = "apps/edge/"  # 앱 바이너리 저장 경로
    auto_update_cron: str = "0 3 * * 1"
    quantize: str = "q4_k_m"


class EvalThreshold(BaseModel):
    faithfulness: float = 0.55
    relevancy: float = 0.65


class DistillDefaults(BaseModel):
    """프로필 defaults — 빌드 품질/데이터 관련 설정만.

    인프라 설정 (``build_timeout_sec``, ``llm_timeout_sec``, ``work_dir`` 등)
    은 ``src/config/settings.py::DistillSettings`` (env ``DISTILL_*``) 가 SSOT.
    여기서는 중복 선언 금지 — 드리프트 원인.
    """

    teacher_model: str = "exaone-sagemaker"
    quantize: str = "q4_k_m"
    # 파일럿 환경 최소값 (distill.yaml 과 일치). 과거 5000 기본값은 대규모
    # 학습 셋 가정이라 실제 프로필이 매번 yaml 에서 200 으로 override 하던
    # 드리프트의 원인이었음. 기본값을 실제 사용값으로 맞춤.
    min_training_samples: int = 200
    eval_threshold: EvalThreshold = Field(default_factory=EvalThreshold)
    training_backend: str = "local"  # local | sagemaker
    # 직전 deployed 빌드 대비 faithfulness 회귀 허용 폭. 더 떨어지면 fail-closed
    # (force_deploy 로 우회 가능). 0.05 = 5% 점수 하락까지 OK.
    max_regression_delta: float = Field(0.05, ge=0.0, le=1.0)


class DistillProfile(BaseModel):
    enabled: bool = False
    description: str = ""
    search_group: str
    # 필수 필드 — 디폴트 하드코딩 금지. 선택은 distill_base_models 레지스트리
    # (SSOT) 에서 대시보드/API 가 주어야 한다. Pydantic 레벨에서 강제.
    base_model: str = Field(..., min_length=1, max_length=200)
    lora: LoRAConfig = Field(default_factory=LoRAConfig)
    training: TrainingConfig = Field(default_factory=TrainingConfig)
    qa_style: QAStyleConfig = Field(default_factory=QAStyleConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    deploy: DeployConfig = Field(default_factory=DeployConfig)


class DistillConfig(BaseModel):
    defaults: DistillDefaults = Field(default_factory=DistillDefaults)
    profiles: dict[str, DistillProfile] = Field(default_factory=dict)


__all__ = [
    "DataQualityConfig",
    "DeployConfig",
    "DistillConfig",
    "DistillDefaults",
    "DistillProfile",
    "EvalThreshold",
    "LoRAConfig",
    "QAStyleConfig",
    "TrainingConfig",
]
