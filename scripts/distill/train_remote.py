"""원격 GPU 학습 스크립트 — EC2/K8s에서 실행.

EC2 g4dn.xlarge 또는 K8s GPU Pod에서 실행됨.
학습 데이터(JSONL) + 설정을 받아 LoRA SFT 학습 후 모델 저장.

Usage:
    python3 train_remote.py --data-dir /data --output-dir /output --build-id xxx

    환경변수:
    BASE_MODEL, LORA_R, LORA_ALPHA, EPOCHS, BATCH_SIZE, LEARNING_RATE, MAX_SEQ_LENGTH
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Remote LoRA SFT training")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--build-id", required=True)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 설정 로드
    config_path = data_dir / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text())
    else:
        config = {}

    base_model = os.getenv("BASE_MODEL", config.get("base_model", "google/gemma-3-1b-it"))
    lora_r = int(os.getenv("LORA_R", config.get("lora", {}).get("r", 8)))
    lora_alpha = int(os.getenv("LORA_ALPHA", config.get("lora", {}).get("alpha", 16)))
    lora_dropout = float(config.get("lora", {}).get("dropout", 0.05))
    epochs = int(os.getenv("EPOCHS", config.get("training", {}).get("epochs", 3)))
    batch_size = int(os.getenv("BATCH_SIZE", config.get("training", {}).get("batch_size", 4)))
    learning_rate = float(os.getenv("LEARNING_RATE", config.get("training", {}).get("learning_rate", 2e-4)))
    max_length = int(os.getenv("MAX_SEQ_LENGTH", config.get("training", {}).get("max_seq_length", 512)))

    train_path = data_dir / "train.jsonl"
    if not train_path.exists():
        logger.error("Training data not found: %s", train_path)
        return

    logger.info("=== Remote Training Config ===")
    logger.info("Build: %s", args.build_id)
    logger.info("Model: %s", base_model)
    logger.info("LoRA: r=%d alpha=%d dropout=%.2f", lora_r, lora_alpha, lora_dropout)
    logger.info("Training: epochs=%d batch=%d lr=%s max_len=%d", epochs, batch_size, learning_rate, max_length)

    # 학습
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    logger.info("Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    logger.info("Loading model...")
    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=torch.float16, device_map="auto", trust_remote_code=True,
    )

    lora_config = LoraConfig(
        r=lora_r, lora_alpha=lora_alpha, lora_dropout=lora_dropout,
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    logger.info("Loading dataset...")
    dataset = load_dataset("json", data_files=str(train_path), split="train")
    split = dataset.train_test_split(test_size=0.1)

    sft_config = SFTConfig(
        output_dir=str(output_dir / "checkpoints"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=8,
        learning_rate=learning_rate,
        max_length=max_length,
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch",
        fp16=torch.cuda.is_available(),
        report_to="none",
    )

    t0 = time.monotonic()
    trainer = SFTTrainer(
        model=model, args=sft_config,
        train_dataset=split["train"], eval_dataset=split["test"],
        processing_class=tokenizer,
    )

    logger.info("Starting training...")
    train_result = trainer.train()
    duration = int(time.monotonic() - t0)

    logger.info("Training completed in %ds", duration)
    logger.info("Train loss: %.4f", train_result.training_loss)

    # 모델 병합 + 저장
    logger.info("Merging LoRA and saving...")
    merged = model.merge_and_unload()
    merged_path = str(output_dir / "merged")
    merged.save_pretrained(merged_path)
    tokenizer.save_pretrained(merged_path)

    # 메트릭 저장
    metrics = {
        "build_id": args.build_id,
        "train_loss": train_result.training_loss,
        "duration_sec": duration,
        "epochs": epochs,
        "samples": len(split["train"]),
    }
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    logger.info("Saved metrics: %s", metrics)
    logger.info("=== Training Complete ===")


if __name__ == "__main__":
    main()
