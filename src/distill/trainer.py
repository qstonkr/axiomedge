# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportPrivateImportUsage=false, reportCallIssue=false
"""LoRA SFT Trainer.

Qwen2.5-0.5B 등 소형 모델을 LoRA로 fine-tuning.
peft + trl 의존. 별도 프로세스(subprocess)에서 실행 권장.

Usage:
    trainer = DistillTrainer(profile, output_dir="/tmp/distill/model")
    dataset = trainer.prepare_dataset("train.jsonl")
    result = trainer.train(dataset)
    trainer.merge_and_save("/tmp/distill/model/merged")
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# SSL 우회 — HuggingFace만 (사내망 프록시 self-signed cert 대응)
os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFY", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 캐시된 모델만 사용
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")  # HF requests용

from src.distill.config import DistillProfile

logger = logging.getLogger(__name__)


@dataclass
class TrainOutput:
    training_loss: float
    eval_loss: float | None
    duration_sec: int
    output_dir: str


class DistillTrainer:
    """LoRA SFT trainer wrapper."""

    def __init__(self, profile: DistillProfile, output_dir: str) -> None:
        self.profile = profile
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def _save_tokenizer_with_model(self, tokenizer, output_path: str | Path) -> None:
        # FastTokenizer의 save_pretrained()는 tokenizer.json만 저장하고
        # tokenizer.model (SentencePiece raw)을 저장하지 않는다. llama.cpp의
        # convert_hf_to_gguf.py는 Gemma3의 경우 tokenizer.model 존재 여부로
        # SentencePiece 경로/gpt2 BPE 경로를 분기하는데, 없으면 BPE fallback으로
        # 떨어져서 한국어 multi-byte 출력이 깨진다. HF cache 또는 base model
        # 경로에서 복사해서 이 경로 분기를 정상 경로로 고정한다.
        output_path = Path(output_path)
        tokenizer.save_pretrained(str(output_path))
        tm_target = output_path / "tokenizer.model"
        if tm_target.exists():
            return
        base_ref = self.profile.base_model
        try:
            from huggingface_hub import try_to_load_from_cache
            cached = try_to_load_from_cache(base_ref, "tokenizer.model")
            # try_to_load_from_cache 는 str | None | _CACHED_NO_EXIST 반환.
            # sentinel 은 truthy 라 Path() 에서 TypeError — isinstance 로 필터.
            if isinstance(cached, str) and Path(cached).exists():
                shutil.copy(cached, tm_target)
                logger.info("Copied tokenizer.model from HF cache (%s)", base_ref)
                return
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("HF cache lookup for tokenizer.model failed: %s", e)
        try:
            base_path = Path(base_ref)
            if base_path.is_dir() and (base_path / "tokenizer.model").exists():
                shutil.copy(base_path / "tokenizer.model", tm_target)
                logger.info("Copied tokenizer.model from local base path %s", base_path)
                return
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Local base path lookup for tokenizer.model failed: %s", e)
        logger.warning(
            "tokenizer.model not found for base=%s — GGUF conversion will "
            "fall back to gpt2 BPE and break Gemma3 Korean output",
            base_ref,
        )

    def prepare_dataset(self, data_path: str) -> Any:
        """JSONL → HuggingFace Dataset (전체 학습용, 평가는 Teacher가 별도 수행)."""
        from datasets import load_dataset

        dataset = load_dataset("json", data_files=data_path, split="train")
        logger.info("Dataset: %d samples", len(dataset))
        return dataset

    def train(self, dataset) -> TrainOutput:
        """LoRA SFT 학습 실행. 평가는 DistillEvaluator(Teacher)가 별도 수행."""
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig, SFTTrainer

        t0 = time.monotonic()
        model_id = self.profile.base_model
        lora_cfg = self.profile.lora
        train_cfg = self.profile.training

        # 방어선: DB에 저장된 구 프로필 값이 학습 안 되는 범위(r=8, lr=2e-4, epochs=3)
        # 여도 자동으로 safe 한 하한선으로 올림. 경고 로그로 승격 사실 알림.
        # 과거 학습 실패(train_loss≈2.087, echo 증상) 재현 방지.
        #
        # -it (instruction-tuned) 모델 기준 검증된 하한선:
        #   LoRA r >= 16, alpha >= 32
        #   epochs >= 5
        #   learning_rate <= 1e-4 (너무 크면 기반 가중치 교란)
        MIN_LORA_R = 16
        MIN_LORA_ALPHA = 32
        MIN_EPOCHS = 5
        MAX_LR_FOR_IT = 1e-4
        # Reformatter (2문단 압축 포맷) 적용 후 실측: p99=347, max=405 tokens.
        # 384 floor 면 0.2% 만 truncate (borderline OK), 512 면 0% truncate.
        # 과거 floor 1024 는 RAG style 긴 답변(p99=1007) 시대 기준이라 더 이상
        # 적용 안 됨. 그래도 누군가 256 같은 너무 작은 값을 넣었을 때를 대비해
        # 384 를 안전망 floor 로 유지 (256 은 45.7% truncate 라 위험).
        MIN_MAX_SEQ_LEN = 384
        IS_INSTRUCTION_TUNED = any(
            s in model_id.lower() for s in ("-it", "-instruct", "-chat")
        )

        if lora_cfg.r < MIN_LORA_R:
            logger.warning(
                "LoRA r=%d is below safe minimum %d — promoting",
                lora_cfg.r, MIN_LORA_R,
            )
            lora_cfg = lora_cfg.model_copy(update={"r": MIN_LORA_R})
        if lora_cfg.alpha < MIN_LORA_ALPHA:
            logger.warning(
                "LoRA alpha=%d is below safe minimum %d — promoting",
                lora_cfg.alpha, MIN_LORA_ALPHA,
            )
            lora_cfg = lora_cfg.model_copy(update={"alpha": MIN_LORA_ALPHA})
        if train_cfg.epochs < MIN_EPOCHS:
            logger.warning(
                "epochs=%d is below safe minimum %d — promoting",
                train_cfg.epochs, MIN_EPOCHS,
            )
            train_cfg = train_cfg.model_copy(update={"epochs": MIN_EPOCHS})
        if IS_INSTRUCTION_TUNED and train_cfg.learning_rate > MAX_LR_FOR_IT:
            logger.warning(
                "learning_rate=%.0e is too high for instruction-tuned model %s — "
                "clamping to %.0e to avoid weight corruption",
                train_cfg.learning_rate, model_id, MAX_LR_FOR_IT,
            )
            train_cfg = train_cfg.model_copy(update={"learning_rate": MAX_LR_FOR_IT})
        if train_cfg.max_seq_length < MIN_MAX_SEQ_LEN:
            logger.warning(
                "max_seq_length=%d is below safe minimum %d — promoting "
                "(42.3%% of pbu-store samples exceed 512 tokens)",
                train_cfg.max_seq_length, MIN_MAX_SEQ_LEN,
            )
            train_cfg = train_cfg.model_copy(update={"max_seq_length": MIN_MAX_SEQ_LEN})

        logger.info(
            "Final training config: lora(r=%d, alpha=%d), "
            "epochs=%d, lr=%.0e, batch=%d, grad_accum=%d, max_seq=%d",
            lora_cfg.r, lora_cfg.alpha,
            train_cfg.epochs, train_cfg.learning_rate,
            train_cfg.batch_size, train_cfg.gradient_accumulation,
            train_cfg.max_seq_length,
        )

        logger.info("Loading base model: %s", model_id)
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype="auto", device_map="auto", trust_remote_code=True,
        )

        # LoRA 설정
        peft_config = LoraConfig(
            r=lora_cfg.r,
            lora_alpha=lora_cfg.alpha,
            lora_dropout=lora_cfg.dropout,
            target_modules=lora_cfg.target_modules,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, peft_config)
        model.print_trainable_parameters()

        # SFT 설정 — eval 없음 (Teacher가 별도 평가)
        #
        # 핵심 설정:
        # - completion_only_loss=True: user 질문 토큰은 loss에서 제외, assistant 응답만 학습.
        #   False이면 (구버전 trl 기본) user 질문도 예측하도록 학습돼서 "질문을 echo하는"
        #   모델이 만들어짐 (Gemma-3-1b-it 에서 확인된 실제 증상).
        # - bf16=True: Gemma-3 는 bf16 네이티브. fp16 쓰면 수치 불안정으로 학습 정체.
        # - gradient_checkpointing=False: PEFT + gradient_checkpointing 조합은 간헐적으로
        #   학습 정지 버그가 있어서 명시적으로 끔.
        # - max_grad_norm=1.0: LoRA 에서도 gradient explosion 방지.
        sft_config = SFTConfig(
            output_dir=self.output_dir,
            num_train_epochs=train_cfg.epochs,
            per_device_train_batch_size=train_cfg.batch_size,
            gradient_accumulation_steps=train_cfg.gradient_accumulation,
            learning_rate=train_cfg.learning_rate,
            max_length=train_cfg.max_seq_length,
            logging_steps=5,
            save_strategy="no",
            eval_strategy="no",
            warmup_ratio=0.05,
            bf16=True,
            fp16=False,
            gradient_checkpointing=False,
            max_grad_norm=1.0,
            completion_only_loss=True,
            report_to="none",
        )

        trainer = SFTTrainer(
            model=model,
            args=sft_config,
            train_dataset=dataset,
            processing_class=tokenizer,
        )

        logger.info("Starting training: epochs=%d, batch=%d, lr=%s",
                     train_cfg.epochs, train_cfg.batch_size, train_cfg.learning_rate)
        result = trainer.train()

        duration = int(time.monotonic() - t0)
        train_loss = result.training_loss

        # 어댑터 저장
        adapter_path = Path(self.output_dir) / "adapter"
        model.save_pretrained(str(adapter_path))
        self._save_tokenizer_with_model(tokenizer, adapter_path)

        logger.info("Training complete: loss=%.4f, duration=%ds", train_loss, duration)

        return TrainOutput(
            training_loss=train_loss,
            eval_loss=None,
            duration_sec=duration,
            output_dir=self.output_dir,
        )

    def merge_and_save(self, output_path: str) -> str:
        """LoRA 어댑터를 base model에 merge하고 저장."""
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        adapter_path = Path(self.output_dir) / "adapter"
        logger.info("Merging adapter from %s", adapter_path)

        base_model = AutoModelForCausalLM.from_pretrained(
            self.profile.base_model, torch_dtype="auto", trust_remote_code=True,
        )
        model = PeftModel.from_pretrained(base_model, str(adapter_path))
        merged = model.merge_and_unload()

        Path(output_path).mkdir(parents=True, exist_ok=True)
        merged.save_pretrained(output_path)

        tokenizer = AutoTokenizer.from_pretrained(str(adapter_path))
        self._save_tokenizer_with_model(tokenizer, output_path)

        logger.info("Merged model saved to %s", output_path)
        return output_path
