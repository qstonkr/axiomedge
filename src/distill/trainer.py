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
import time
from dataclasses import dataclass
from pathlib import Path

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

    def __init__(self, profile: DistillProfile, output_dir: str):
        self.profile = profile
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def prepare_dataset(self, data_path: str, eval_ratio: float = 0.1):
        """JSONL → HuggingFace Dataset (train/eval split)."""
        from datasets import load_dataset

        dataset = load_dataset("json", data_files=data_path, split="train")
        split = dataset.train_test_split(test_size=eval_ratio, seed=42)
        logger.info(
            "Dataset: %d train, %d eval",
            len(split["train"]), len(split["test"]),
        )
        return split

    def train(self, dataset) -> TrainOutput:
        """LoRA SFT 학습 실행."""
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig, SFTTrainer

        t0 = time.monotonic()
        model_id = self.profile.base_model
        lora_cfg = self.profile.lora
        train_cfg = self.profile.training

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

        # SFT 설정
        sft_config = SFTConfig(
            output_dir=self.output_dir,
            num_train_epochs=train_cfg.epochs,
            per_device_train_batch_size=train_cfg.batch_size,
            gradient_accumulation_steps=train_cfg.gradient_accumulation,
            learning_rate=train_cfg.learning_rate,
            max_seq_length=train_cfg.max_seq_length,
            logging_steps=10,
            save_strategy="epoch",
            eval_strategy="epoch" if "test" in dataset else "no",
            warmup_ratio=0.1,
            fp16=True,
            report_to="none",
        )

        trainer = SFTTrainer(
            model=model,
            args=sft_config,
            train_dataset=dataset["train"],
            eval_dataset=dataset.get("test"),
            processing_class=tokenizer,
        )

        logger.info("Starting training: epochs=%d, batch=%d, lr=%s",
                     train_cfg.epochs, train_cfg.batch_size, train_cfg.learning_rate)
        result = trainer.train()

        duration = int(time.monotonic() - t0)
        train_loss = result.training_loss
        eval_loss = None

        if "test" in dataset:
            eval_result = trainer.evaluate()
            eval_loss = eval_result.get("eval_loss")

        # 어댑터 저장
        adapter_path = Path(self.output_dir) / "adapter"
        model.save_pretrained(str(adapter_path))
        tokenizer.save_pretrained(str(adapter_path))

        logger.info(
            "Training complete: loss=%.4f, eval_loss=%s, duration=%ds",
            train_loss, f"{eval_loss:.4f}" if eval_loss else "N/A", duration,
        )

        return TrainOutput(
            training_loss=train_loss,
            eval_loss=eval_loss,
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
        tokenizer.save_pretrained(output_path)

        logger.info("Merged model saved to %s", output_path)
        return output_path
