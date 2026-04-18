"""EC2 GPU 인스턴스에서 실행되는 LoRA SFT 학습 + GGUF 양자화 스크립트.

EC2 user-data 가 매 부팅 시 S3 (`s3://gs-knowledge-models/scripts/train_on_gpu.py`)
에서 다운로드해 실행한다. 이 파일을 수정한 뒤에는 반드시 같은 위치에 업로드해야 함:

    AWS_PROFILE=$AWS_PROFILE AWS_REGION=ap-northeast-2 \\
      aws s3 cp scripts/train_on_gpu.py \\
      s3://gs-knowledge-models/scripts/train_on_gpu.py

흐름:
    1. data_dir/{train.jsonl, config.json} 읽기
    2. llama.cpp docker image 확보 (첫 실행만 pull, 이후 EBS 캐시)
    3. base 모델 로드 (S3 캐시 → HuggingFace fallback)
    4. LoRA SFT 학습 (trl SFTTrainer, completion_only_loss=True)
    5. LoRA 어댑터 base 에 merge 후 model_merged/ 저장
    6. 2단계 양자화 (docker run): safetensors → f16 GGUF → 목표 quant
    7. result.json 저장 (학습 메트릭 + GGUF 메타데이터)

GGUF 양자화 환경 (2026-04-14 픽스):
- 이전에 AMI 의 구버전 llama.cpp 를 cmake 로 master 빌드 시도 → 4번 실패
  (DLAMI gcc 11 vs llama.cpp CI 가 요구하는 gcc-14 호환성 등)
- 해결: llama.cpp 공식 docker image (`ghcr.io/ggml-org/llama.cpp:full-cuda`) 사용.
  공식 CI 가 직접 빌드해 GHCR 에 배포. 이미지 안에 convert_hf_to_gguf.py +
  llama-quantize + gguf-py 모두 포함. nvidia-container-toolkit 으로 GPU 마운트 가능.
  사전 검증 완료 (debug/docker-verify-20260414-0832.log).
- 어느 단계든 실패하면 명시적 status="failed" + error_log_tail 로 보고
  (silent failure 방지).

trainer 설정도 src/distill/trainer.py 와 동기화:
- bf16=True (Gemma-3 는 bf16 네이티브, fp16 은 수치 불안정)
- completion_only_loss=True (user 토큰은 loss 에서 제외, 'echo' 학습 방지)
- gradient_checkpointing=False (PEFT 와 간헐 충돌)
- max_grad_norm=1.0 (gradient clipping)

추가로 unsafe 하이퍼파라미터 자동 promote 가드:
- LoRA r >= 16, alpha >= 32, epochs >= 5
- instruction-tuned 모델은 lr <= 1e-4 강제
- max_seq_length >= 384 (reformatter 적용 후 p99=347 토큰 기준)
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

# llama.cpp 공식 Docker 이미지 — Gemma3 Q4_K_M 양자화 환경.
#
# 이전에 cmake 소스 빌드 (gcc-14 vs DLAMI 11.x 호환성 등) 로 4번 실패 후,
# llama.cpp 공식 CI 가 직접 빌드/배포하는 Docker 이미지로 전환.
# 이미지 사용 검증 완료 (2026-04-14):
#   - Tesla T4 GPU 가 컨테이너 내 nvidia-smi 로 인식됨 (nvidia-container-toolkit OK)
#   - convert_hf_to_gguf.py 경로: /app/convert_hf_to_gguf.py
#   - llama-quantize binary 경로: /app/llama-quantize
#   - gguf-py 경로: /app/gguf-py
#   - 이미지 크기 ~3.3 GB, 첫 pull 후 EBS 에 캐시 영속
LLAMA_CPP_DOCKER_IMAGE = os.getenv(
    "LLAMA_CPP_DOCKER_IMAGE", "ghcr.io/ggml-org/llama.cpp:full-cuda",
)


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
    # Reformatter (2문단 압축 포맷) 적용 후 실측: p99=347, max=405 tokens.
    # 384 floor → 0.2% truncate (borderline OK), 512 권장 → 0% truncate.
    # 과거 floor 1024 는 이전 RAG 긴 답변(p99=1007) 시대 기준.
    min_max_seq_len = 384
    # FFN 이 빠진 target_modules 는 학습 효과 없음 (train_loss 정체).
    # service.py 가 구 코드라 config.json 에 4개만 들어올 수 있으니 여기서
    # 방어적으로 7개로 승격. 기존에 7개면 그대로 유지.
    required_ffn_modules = {"gate_proj", "up_proj", "down_proj"}
    full_target_modules = [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ]

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
    if training_config.get("max_seq_length", 0) < min_max_seq_len:
        logger.warning(
            "max_seq_length=%s below safe min %d — promoting "
            "(42.3%% of pbu-store samples exceed 512 tokens)",
            training_config.get("max_seq_length"), min_max_seq_len,
        )
        training_config = {**training_config, "max_seq_length": min_max_seq_len}

    # target_modules force-promote: config.json 에 4개만 있어도 7개로 확장.
    # service.py 가 구 코드 (API restart 전) 로 4개를 보낼 수 있어서 방어.
    current_tm = set(lora_config.get("target_modules", []))
    if not required_ffn_modules.issubset(current_tm):
        missing = sorted(required_ffn_modules - current_tm)
        logger.warning(
            "target_modules missing FFN layers %s — forcing full 7-module set",
            missing,
        )
        lora_config = {**lora_config, "target_modules": full_target_modules}

    return lora_config, training_config


def _ensure_docker_image() -> None:
    """llama.cpp 공식 Docker 이미지 확보 — 양자화 환경.

    배경:
        AMI 사전 빌드된 llama.cpp 는 Gemma3 미지원, 소스 빌드 (cmake) 는 DLAMI 의
        gcc 11 vs llama.cpp CI 의 gcc-14 호환 문제로 4번 실패.
        해결: llama.cpp 공식 CI 가 직접 빌드/배포하는 Docker 이미지 사용.
        이미지에 convert_hf_to_gguf.py + llama-quantize + gguf-py 모두 포함.

    Idempotency:
        Docker 가 이미지를 EBS 에 캐시. 두 번째 실행부터 pull 안 함.
        DeleteOnTermination=false 면 인스턴스 재시작 후에도 캐시 유지.

    실패 처리:
        pull 실패 시 RuntimeError → 상위 train() 에서 잡아 result.json 에
        status="failed" + error_log_tail 로 기록.
    """
    image = LLAMA_CPP_DOCKER_IMAGE

    # 이미 pull 됐는지 확인
    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True, text=True, timeout=30,
    )
    if inspect.returncode == 0:
        logger.info("Docker image 이미 캐시됨: %s", image)
        return

    logger.info("=== Docker pull %s (~3.3 GB, first run only) ===", image)
    result = subprocess.run(
        ["docker", "pull", image],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-1500:]
        stdout_tail = (result.stdout or "")[-1500:]
        logger.error(
            "docker pull failed (rc=%d):\nstderr: %s\nstdout: %s",
            result.returncode, stderr_tail, stdout_tail,
        )
        raise RuntimeError(f"docker pull failed for {image}")
    logger.info("Docker pull 완료: %s", image)


def _log_environment_diagnostics() -> None:
    """빌드 환경 진단 — Docker, host GPU, 이미지 캐시 상태.

    실패 시 어떤 환경에서 동작했는지 추후 추적 가능하도록 result.json 에 캡처.
    """
    logger.info("=== Environment diagnostics ===")

    # Docker 버전
    try:
        result = subprocess.run(
            ["docker", "--version"], capture_output=True, text=True, timeout=10,
        )
        logger.info("Docker: %s", (result.stdout or result.stderr).strip())
    except Exception as e:
        logger.info("Docker check failed: %s", e)

    # Host GPU (DLAMI 의 nvidia-smi)
    # nvidia-container-toolkit 의 GPU 마운트는 _quantize_gguf 의 docker run --gpus all
    # 에서 실패해야 알 수 있음. 여기서 별도 컨테이너 띄워 검증하면 nvidia/cuda 이미지를
    # 불필요하게 pull 하므로, host 만 확인 (시간 절약).
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            logger.info("Host GPU: %s", result.stdout.strip())
        else:
            logger.warning("nvidia-smi failed: %s", result.stderr[:300])
    except Exception as e:
        logger.warning("nvidia-smi check error: %s", e)

    # llama.cpp 이미지 캐시 상태
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", LLAMA_CPP_DOCKER_IMAGE,
             "--format", "{{.Id}} ({{.Size}} bytes)"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            logger.info("llama.cpp image cached: %s", result.stdout.strip())
        else:
            logger.info("llama.cpp image not yet cached (will pull)")
    except Exception as e:
        logger.info("Image inspect error: %s", e)


def _tail_boot_log(max_lines: int = 150) -> str | None:
    """distill-boot.log 의 tail 을 result.json 에 실어 중앙에서 읽을 수 있도록.

    SSM/SSH 가 막혀 있어서 실패 시 EC2 내 로그를 직접 읽을 수 없음 — result.json
    에 포함시켜 S3 로 올라오게 해야 중앙 대시보드/DB 에서 디버깅 가능.
    """
    log_path = Path("/var/log/distill-boot.log")
    if not log_path.exists():
        return None
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:])
    except OSError as e:
        return f"<failed to read boot log: {e}>"


def _quantize_gguf(
    merged_dir: str, output_dir: str, quantize_method: str,
) -> str | None:
    """2단계 GGUF 양자화 — 공식 docker 이미지 사용.

    Step 1: HF safetensors → f16 GGUF (convert_hf_to_gguf.py)
    Step 2: f16 GGUF → 목표 quant (llama-quantize)
    둘 다 ghcr.io/ggml-org/llama.cpp:full-cuda 컨테이너 안에서 실행.

    호스트 경로 마운트:
        -v {output_dir}:/work
        merged_dir 가 output_dir 내부에 있어야 한 번의 마운트로 둘 다 접근 가능.
        (현재 train() 가 merged_dir = output_dir/model_merged 로 만들어서 OK)

    Returns:
        성공 시 최종 GGUF 경로, 실패 시 None (호출자가 명시적 실패 처리).
    """
    output_dir_p = Path(output_dir).resolve()
    merged_dir_p = Path(merged_dir).resolve()
    if not str(merged_dir_p).startswith(str(output_dir_p)):
        logger.error(
            "merged_dir (%s) must be under output_dir (%s) for docker volume mount",
            merged_dir_p, output_dir_p,
        )
        return None

    rel_merged = merged_dir_p.relative_to(output_dir_p)
    f16_name = "model_f16.gguf"
    final_name = "model.gguf"
    f16_path = output_dir_p / f16_name
    final_path = output_dir_p / final_name

    # Step 3a: safetensors → f16 GGUF (docker)
    logger.info("Step 3a: convert HF → f16 GGUF (docker)")
    cmd_convert = [
        "docker", "run", "--rm", "--gpus", "all",
        "-v", f"{output_dir_p}:/work",
        "--entrypoint", "python3",
        LLAMA_CPP_DOCKER_IMAGE,
        "/app/convert_hf_to_gguf.py",
        f"/work/{rel_merged}",
        "--outfile", f"/work/{f16_name}",
        "--outtype", "f16",
    ]
    logger.info("$ %s", " ".join(cmd_convert))
    try:
        result = subprocess.run(
            cmd_convert, capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        logger.error("convert_hf_to_gguf timed out (15min)")
        return None
    except Exception as e:
        logger.error("convert_hf_to_gguf unexpected error: %s", e)
        return None

    if result.returncode != 0:
        logger.error(
            "convert_hf_to_gguf failed (rc=%d):\nstdout (tail): %s\nstderr (tail): %s",
            result.returncode,
            (result.stdout or "")[-2000:],
            (result.stderr or "")[-2000:],
        )
        return None
    tail = "\n".join((result.stdout or "").strip().split("\n")[-5:])
    logger.info("convert_hf_to_gguf stdout (tail):\n%s", tail)

    if not f16_path.exists():
        logger.error("f16 GGUF not produced at %s", f16_path)
        return None
    f16_size_mb = f16_path.stat().st_size / (1024 * 1024)
    logger.info("f16 GGUF: %.1f MB", f16_size_mb)

    # 목표가 f16 면 그대로 사용
    if quantize_method == "f16":
        f16_path.rename(final_path)
        return str(final_path)

    # Step 3b: f16 → 목표 quant (docker)
    logger.info("Step 3b: quantize f16 GGUF → %s (docker)", quantize_method)
    cmd_quant = [
        "docker", "run", "--rm",
        "-v", f"{output_dir_p}:/work",
        "--entrypoint", "/app/llama-quantize",
        LLAMA_CPP_DOCKER_IMAGE,
        f"/work/{f16_name}",
        f"/work/{final_name}",
        quantize_method.upper(),
    ]
    logger.info("$ %s", " ".join(cmd_quant))
    try:
        result = subprocess.run(
            cmd_quant, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        logger.error("llama-quantize timed out (10min)")
        return None
    except Exception as e:
        logger.error("llama-quantize unexpected error: %s", e)
        return None

    if result.returncode != 0:
        logger.error(
            "llama-quantize failed (rc=%d):\nstdout (tail): %s\nstderr (tail): %s",
            result.returncode,
            (result.stdout or "")[-2000:],
            (result.stderr or "")[-2000:],
        )
        return None
    tail = "\n".join((result.stdout or "").strip().split("\n")[-5:])
    logger.info("llama-quantize stdout (tail):\n%s", tail)

    if not final_path.exists():
        logger.error("Final GGUF not produced at %s", final_path)
        return None

    # f16 임시 파일 정리 (디스크 절약)
    try:
        f16_path.unlink()
    except OSError:
        pass

    return str(final_path)


def train(data_dir: str, output_dir: str, build_id: str) -> dict:
    """LoRA SFT 학습 + GGUF 양자화."""
    config_path = os.path.join(data_dir, "config.json")
    data_path = os.path.join(data_dir, "train.jsonl")

    # llama.cpp 공식 docker image 확보 — Gemma3 양자화에 필요한 도구 일체 포함.
    # 첫 실행만 ~3.3GB pull, 이후 EBS 캐시 (DeleteOnTermination=false 면 영속).
    _ensure_docker_image()
    _log_environment_diagnostics()

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
            # try_to_load_from_cache 는 str | None | _CACHED_NO_EXIST 반환.
            # sentinel 은 truthy 하지만 str 아님 — os.path.exists 에서 TypeError.
            if isinstance(cached, str):
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
        # SSM/SSH 막혀서 EC2 로그 직접 조회 불가 — tail 을 result 에 실어 보냄.
        boot_tail = _tail_boot_log(max_lines=150)
        if boot_tail:
            result["error_log_tail"] = boot_tail

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
