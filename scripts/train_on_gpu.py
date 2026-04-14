"""EC2 GPU 인스턴스에서 실행되는 LoRA SFT 학습 + GGUF 양자화 스크립트.

EC2 user-data 가 매 부팅 시 S3 (`s3://gs-knowledge-models/scripts/train_on_gpu.py`)
에서 다운로드해 실행한다. 이 파일을 수정한 뒤에는 반드시 같은 위치에 업로드해야 함:

    AWS_PROFILE=jeongbeomkim AWS_REGION=ap-northeast-2 \\
      aws s3 cp scripts/train_on_gpu.py \\
      s3://gs-knowledge-models/scripts/train_on_gpu.py

흐름:
    1. data_dir/{train.jsonl, config.json} 읽기
    2. base 모델 로드 (S3 캐시 → HuggingFace fallback)
    3. LoRA SFT 학습 (trl SFTTrainer, completion_only_loss=True)
    4. LoRA 어댑터 base 에 merge 후 model_merged/ 저장
    5. 2단계 양자화: safetensors → f16 GGUF → 목표 quant (q4_k_m 등)
    6. result.json 저장 (학습 메트릭 + GGUF 메타데이터)

과거 버그 (2026-04-13 픽스):
- 단일 호출 `python3 -m llama_cpp.convert ... --outtype q4_k_m` 시도 →
  llama_cpp.convert 모듈 없음 + q4_k_m 은 convert 단계 outtype 아님.
- Fallback `llama-quantize` 가 존재하지 않는 `merged_dir/ggml-model-f16.gguf`
  를 입력으로 기대 → 항상 실패.
- 양쪽 다 실패해도 `gguf_path = None` 으로 조용히 처리 → result.json 에
  `gguf_size_mb: null` 만 남고 빌드는 "completed" 로 표시됨 (silent failure).

지금 구조:
- `convert_hf_to_gguf.py` (llama.cpp main repo, AMI 의 /opt/llama.cpp/) 로 f16 변환
- `llama-quantize` 바이너리로 q4_k_m 등 추가 양자화
- 어느 단계든 실패하면 명시적 실패로 result.json 에 status="failed" 작성

trainer 설정도 src/distill/trainer.py 와 동기화:
- bf16=True (Gemma-3 는 bf16 네이티브, fp16 은 수치 불안정)
- completion_only_loss=True (user 토큰은 loss 에서 제외, 'echo' 학습 방지)
- gradient_checkpointing=False (PEFT 와 간헐 충돌)
- max_grad_norm=1.0 (gradient clipping)

추가로 unsafe 하이퍼파라미터 자동 promote 가드:
- LoRA r >= 16, alpha >= 32, epochs >= 5
- instruction-tuned 모델은 lr <= 1e-4 강제
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# SSL 우회 (사내망)
os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFY", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

S3_MODEL_BUCKET = os.getenv("DISTILL_S3_BUCKET", "gs-knowledge-models")
S3_MODEL_PREFIX = "models"
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")

# llama.cpp 경로 (EC2 AMI 에 빌드/설치된 위치)
LLAMA_CPP_DIR = Path(os.getenv("LLAMA_CPP_DIR", "/opt/llama.cpp"))
CONVERT_SCRIPT = LLAMA_CPP_DIR / "convert_hf_to_gguf.py"
LLAMA_QUANTIZE_BIN = LLAMA_CPP_DIR / "build" / "bin" / "llama-quantize"


def _resolve_model_path(base_model: str) -> str:
    """모델 경로 결정: S3 캐시 → HuggingFace ID fallback."""
    local_path = f"/opt/distill/models/{base_model.replace('/', '_')}"
    s3_path = f"s3://{S3_MODEL_BUCKET}/{S3_MODEL_PREFIX}/{base_model}/"

    if os.path.exists(local_path) and os.listdir(local_path):
        logger.info("Model cached locally: %s", local_path)
        return local_path

    try:
        check = subprocess.run(
            ["aws", "s3", "ls", s3_path, "--region", AWS_REGION],
            capture_output=True, text=True, timeout=10,
        )
        if check.returncode == 0 and check.stdout.strip():
            logger.info("Downloading model from S3: %s", s3_path)
            os.makedirs(local_path, exist_ok=True)
            subprocess.run(
                ["aws", "s3", "sync", s3_path, local_path, "--region", AWS_REGION],
                check=True, timeout=600,
            )
            return local_path
    except Exception as e:
        logger.warning("S3 model download failed: %s, falling back to HuggingFace", e)

    return base_model


def _is_instruction_tuned(model_id: str) -> bool:
    return any(s in model_id.lower() for s in ("-it", "-instruct", "-chat"))


def _clamp_hyperparameters(
    lora_config: dict, training_config: dict, base_model: str,
) -> tuple[dict, dict]:
    """DB 프로필 값이 unsafe 범위면 safe 하한선으로 promote.

    중앙 trainer.py 의 guardrail 과 동일 — EC2 에서 동작하는 train_on_gpu.py
    에도 같은 가드를 둬야 누군가 직접 config 를 만들어도 안전.
    """
    min_lora_r = 16
    min_lora_alpha = 32
    min_epochs = 5
    max_lr_for_it = 1e-4

    if lora_config.get("r", 0) < min_lora_r:
        logger.warning(
            "LoRA r=%s below safe min %d — promoting",
            lora_config.get("r"), min_lora_r,
        )
        lora_config = {**lora_config, "r": min_lora_r}
    if lora_config.get("alpha", 0) < min_lora_alpha:
        logger.warning(
            "LoRA alpha=%s below safe min %d — promoting",
            lora_config.get("alpha"), min_lora_alpha,
        )
        lora_config = {**lora_config, "alpha": min_lora_alpha}
    if training_config.get("epochs", 0) < min_epochs:
        logger.warning(
            "epochs=%s below safe min %d — promoting",
            training_config.get("epochs"), min_epochs,
        )
        training_config = {**training_config, "epochs": min_epochs}
    if _is_instruction_tuned(base_model) and training_config.get("learning_rate", 0) > max_lr_for_it:
        logger.warning(
            "learning_rate=%.0e too high for instruction-tuned model — clamping to %.0e",
            training_config.get("learning_rate"), max_lr_for_it,
        )
        training_config = {**training_config, "learning_rate": max_lr_for_it}

    return lora_config, training_config


def _quantize_gguf(
    merged_dir: str, output_dir: str, quantize_method: str,
) -> str | None:
    """2단계 GGUF 양자화: HF safetensors → f16 GGUF → 목표 quant.

    Returns:
        성공 시 최종 GGUF 경로, 실패 시 None (호출자가 명시적 실패 처리).
    """
    f16_path = os.path.join(output_dir, "model_f16.gguf")
    final_path = os.path.join(output_dir, "model.gguf")

    if not CONVERT_SCRIPT.exists():
        logger.error("convert_hf_to_gguf.py not found at %s", CONVERT_SCRIPT)
        return None
    if quantize_method != "f16" and not LLAMA_QUANTIZE_BIN.exists():
        logger.error("llama-quantize not found at %s", LLAMA_QUANTIZE_BIN)
        return None

    # Step 3a: safetensors → f16 GGUF
    logger.info("Step 3a: convert HF safetensors → f16 GGUF")
    try:
        result = subprocess.run(
            [
                "python3", str(CONVERT_SCRIPT),
                merged_dir,
                "--outfile", f16_path,
                "--outtype", "f16",
            ],
            check=True, capture_output=True, text=True, timeout=900,
        )
        tail = "\n".join(result.stdout.strip().split("\n")[-3:])
        logger.info("convert_hf_to_gguf stdout (last lines):\n%s", tail)
    except subprocess.CalledProcessError as e:
        logger.error(
            "convert_hf_to_gguf failed (rc=%d):\nstdout: %s\nstderr: %s",
            e.returncode, e.stdout, e.stderr,
        )
        return None
    except Exception as e:
        logger.error("convert_hf_to_gguf unexpected error: %s", e)
        return None

    if not os.path.exists(f16_path):
        logger.error("f16 GGUF not produced at %s", f16_path)
        return None
    f16_size_mb = os.path.getsize(f16_path) / (1024 * 1024)
    logger.info("f16 GGUF: %.1f MB", f16_size_mb)

    # 목표가 f16 면 그대로 사용
    if quantize_method == "f16":
        shutil.move(f16_path, final_path)
        return final_path

    # Step 3b: f16 → 목표 quant
    logger.info("Step 3b: quantize f16 GGUF → %s", quantize_method)
    try:
        result = subprocess.run(
            [str(LLAMA_QUANTIZE_BIN), f16_path, final_path, quantize_method],
            check=True, capture_output=True, text=True, timeout=600,
        )
        tail = "\n".join(result.stdout.strip().split("\n")[-3:])
        logger.info("llama-quantize stdout (last lines):\n%s", tail)
    except subprocess.CalledProcessError as e:
        logger.error(
            "llama-quantize failed (rc=%d):\nstdout: %s\nstderr: %s",
            e.returncode, e.stdout, e.stderr,
        )
        return None
    except Exception as e:
        logger.error("llama-quantize unexpected error: %s", e)
        return None

    if not os.path.exists(final_path):
        logger.error("Final GGUF not produced at %s", final_path)
        return None

    # f16 임시 파일 정리 (디스크 절약)
    try:
        os.remove(f16_path)
    except OSError:
        pass

    return final_path


def train(data_dir: str, output_dir: str, build_id: str) -> dict:
    """LoRA SFT 학습 + GGUF 양자화."""
    config_path = os.path.join(data_dir, "config.json")
    data_path = os.path.join(data_dir, "train.jsonl")

    with open(config_path) as f:
        config = json.load(f)

    base_model = config.get("base_model", "Qwen/Qwen2.5-0.5B-Instruct")
    lora_config = config.get("lora", {})
    training_config = config.get("training", {})
    quantize_method = config.get("quantize", "q4_k_m")

    # 안전망: unsafe 하이퍼파라미터 자동 promote
    lora_config, training_config = _clamp_hyperparameters(
        lora_config, training_config, base_model,
    )

    logger.info(
        "Final config: model=%s lora(r=%s alpha=%s) epochs=%s lr=%.0e quantize=%s",
        base_model, lora_config.get("r"), lora_config.get("alpha"),
        training_config.get("epochs"), training_config.get("learning_rate"),
        quantize_method,
    )

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    merged_dir = os.path.join(output_dir, "model_merged")
    t0 = time.time()

    model_path = _resolve_model_path(base_model)
    logger.info("Using model path: %s", model_path)

    # ---------- Step 1: LoRA SFT 학습 ----------
    logger.info("=== Step 1: LoRA SFT Training ===")
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        trust_remote_code=True,
        device_map="auto",
        torch_dtype=torch.bfloat16,
    )

    # attention Q/K/V/O + FFN gate/up/down — Gemma3/LLaMA/Qwen 모든 decoder
    # 모델에서 factual 지식은 FFN 에 저장되므로 attention 만 target 하면
    # train_loss 가 1.5~2.0 에서 정체되고 학습 데이터 내용을 못 주입한다.
    # Unsloth · QLoRA · HuggingFace PEFT 공식 튜토리얼 표준.
    default_target_modules = [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ]
    peft_config = LoraConfig(
        r=lora_config.get("r", 16),
        lora_alpha=lora_config.get("alpha", 32),
        lora_dropout=lora_config.get("dropout", 0.05),
        target_modules=lora_config.get("target_modules", default_target_modules),
        bias="none",
        task_type="CAUSAL_LM",
    )
    logger.info("LoRA target_modules: %s", peft_config.target_modules)
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()

    dataset = load_dataset("json", data_files=data_path, split="train")
    logger.info("Dataset: %d samples", len(dataset))

    max_seq = training_config.get("max_seq_length", 512)
    sft_config = SFTConfig(
        output_dir=os.path.join(output_dir, "checkpoints"),
        num_train_epochs=training_config.get("epochs", 5),
        per_device_train_batch_size=training_config.get("batch_size", 1),
        gradient_accumulation_steps=training_config.get("gradient_accumulation", 16),
        learning_rate=training_config.get("learning_rate", 5e-5),
        max_length=max_seq,
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

    train_result = trainer.train()
    train_loss = train_result.training_loss
    logger.info("Training done: loss=%.4f", train_loss)

    # ---------- Step 2: LoRA merge + save ----------
    logger.info("=== Step 2: Merge LoRA ===")
    model = model.merge_and_unload()
    model.save_pretrained(merged_dir)
    tokenizer.save_pretrained(merged_dir)

    # tokenizer.model (SentencePiece raw) 복사 — FastTokenizer.save_pretrained()는
    # tokenizer.json만 저장하고 이 파일을 누락시키는데, llama.cpp의 convert_hf_to_gguf.py
    # Gemma3 경로는 이 파일 유무로 SentencePiece/gpt2 BPE 분기를 결정한다.
    # 없으면 BPE fallback으로 떨어져 한국어 출력이 깨짐 — 반드시 복사해야 함.
    tm_target = os.path.join(merged_dir, "tokenizer.model")
    if not os.path.exists(tm_target):
        tm_src_candidates = [
            os.path.join(model_path, "tokenizer.model"),  # 로컬 base model 경로
        ]
        try:
            from huggingface_hub import try_to_load_from_cache
            cached = try_to_load_from_cache(base_model, "tokenizer.model")
            if cached:
                tm_src_candidates.insert(0, cached)
        except Exception as e:
            logger.debug("HF cache lookup for tokenizer.model failed: %s", e)
        for src in tm_src_candidates:
            if src and os.path.exists(src):
                shutil.copy(src, tm_target)
                logger.info("Copied tokenizer.model from %s", src)
                break
        else:
            logger.warning(
                "tokenizer.model not found for %s — GGUF conversion will fall back "
                "to gpt2 BPE and break Gemma3 Korean output",
                base_model,
            )

    # ---------- Step 3: GGUF 양자화 (2단계, 명시적 실패 처리) ----------
    logger.info("=== Step 3: GGUF Quantization (%s) ===", quantize_method)
    gguf_path = _quantize_gguf(merged_dir, output_dir, quantize_method)
    quantize_failed = gguf_path is None

    duration = int(time.time() - t0)

    # ---------- Step 4: SHA256 + 메타데이터 ----------
    gguf_sha256 = None
    gguf_size_mb = None
    if gguf_path and os.path.exists(gguf_path):
        sha = hashlib.sha256()
        with open(gguf_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        gguf_sha256 = sha.hexdigest()
        gguf_size_mb = round(os.path.getsize(gguf_path) / (1024 * 1024), 1)

    # ---------- Step 5: result.json (양자화 실패 시 status="failed" 명시) ----------
    result = {
        "status": "failed" if quantize_failed else "completed",
        "build_id": build_id,
        "train_loss": round(train_loss, 4),
        "duration_sec": duration,
        "gguf_size_mb": gguf_size_mb,
        "gguf_sha256": gguf_sha256,
        "quantize_method": quantize_method,
        "base_model": base_model,
    }
    if quantize_failed:
        result["error"] = "GGUF quantization failed (see logs)"

    result_path = os.path.join(output_dir, "result.json")
    with open(result_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info(
        "=== Done in %ds: loss=%.4f, gguf=%sMB, status=%s ===",
        duration, train_loss, gguf_size_mb or "FAILED", result["status"],
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--build-id", required=True)
    args = parser.parse_args()
    result = train(args.data_dir, args.output_dir, args.build_id)
    sys.exit(0 if result["status"] == "completed" else 1)
