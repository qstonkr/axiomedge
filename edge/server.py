"""엣지 추론 서버 (llama-cpp-python 기반).

POS기 → 엣지 서버 질의 응답. 로컬 GGUF 모델로 추론.
사용 로그를 JSONL로 저장하여 중앙 수집 대상으로 제공.

Usage:
    STORE_ID=gangnam EDGE_API_KEY=secret MODEL_PATH=/models/current/model.gguf \
        uv run uvicorn edge.server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import gc
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

logger = logging.getLogger("edge.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STORE_ID = os.getenv("STORE_ID", "unknown")
EDGE_API_KEY = os.getenv("EDGE_API_KEY", "")
MODEL_PATH = os.getenv("MODEL_PATH", "/models/current/model.gguf")
LOG_DIR = Path(os.getenv("LOG_DIR", "/logs"))
N_CTX = int(os.getenv("EDGE_N_CTX", "512"))
N_THREADS = int(os.getenv("EDGE_N_THREADS", "4"))
MAX_TOKENS = int(os.getenv("EDGE_MAX_TOKENS", "256"))

LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "queries.jsonl"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str | None = Security(api_key_header)):
    if not EDGE_API_KEY:
        return
    if key != EDGE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="GS Edge LLM Server", version="0.1.0")
_llm = None
_startup_time: float = 0


def _get_llm():
    if _llm is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return _llm


def _read_version() -> str:
    manifest_path = Path(MODEL_PATH).parent / "manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text()).get("version", "unknown")
        except (json.JSONDecodeError, OSError):
            pass
    return "unknown"


def _log_query(query: str, answer: str, latency_ms: int, success: bool) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "store_id": STORE_ID,
        "query": query,
        "answer": answer,
        "latency_ms": latency_ms,
        "success": success,
        "model_version": _read_version(),
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("Failed to write log: %s", e)


@app.on_event("startup")
def load_model():
    global _llm, _startup_time
    t0 = time.monotonic()
    model_path = Path(MODEL_PATH)
    if not model_path.exists():
        logger.error("Model file not found: %s", MODEL_PATH)
        return
    try:
        from llama_cpp import Llama
        _llm = Llama(model_path=str(model_path), n_ctx=N_CTX, n_threads=N_THREADS, verbose=False)
        _startup_time = time.monotonic() - t0
        logger.info("Model loaded in %.1fs: %s (n_ctx=%d, threads=%d)",
                     _startup_time, model_path.name, N_CTX, N_THREADS)
    except Exception as e:
        logger.error("Failed to load model: %s", e)


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    query: str = Field(..., max_length=500)


class AskResponse(BaseModel):
    answer: str
    latency_ms: int
    success: bool
    model_version: str


class HealthResponse(BaseModel):
    status: str
    store_id: str
    model_version: str
    model_loaded: bool
    uptime_sec: float


class ReloadResponse(BaseModel):
    status: str
    model_version: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest, _: None = Depends(verify_api_key)):
    llm = _get_llm()
    t0 = time.monotonic()
    try:
        output = llm.create_chat_completion(
            messages=[{"role": "user", "content": request.query}],
            max_tokens=MAX_TOKENS,
        )
        answer = output["choices"][0]["message"]["content"].strip()
        latency_ms = int((time.monotonic() - t0) * 1000)
        success = True
    except Exception as e:
        logger.error("Inference failed: %s", e)
        answer = ""
        latency_ms = int((time.monotonic() - t0) * 1000)
        success = False
    _log_query(request.query, answer, latency_ms, success)
    return AskResponse(answer=answer, latency_ms=latency_ms, success=success,
                       model_version=_read_version())


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok" if _llm is not None else "no_model",
        store_id=STORE_ID, model_version=_read_version(),
        model_loaded=_llm is not None,
        uptime_sec=round(time.monotonic() - (_startup_time or time.monotonic()), 1),
    )


@app.post("/reload", response_model=ReloadResponse, dependencies=[Depends(verify_api_key)])
async def reload_model():
    global _llm
    model_path = Path(MODEL_PATH)
    if not model_path.exists():
        raise HTTPException(status_code=400, detail=f"Model file not found: {MODEL_PATH}")
    logger.info("Reloading model: %s", model_path)
    old_llm = _llm
    _llm = None
    del old_llm
    gc.collect()
    try:
        from llama_cpp import Llama
        _llm = Llama(model_path=str(model_path), n_ctx=N_CTX, n_threads=N_THREADS, verbose=False)
    except Exception as e:
        logger.error("Reload failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Model reload failed: {e}")
    try:
        _llm.create_chat_completion(
            messages=[{"role": "user", "content": "테스트"}], max_tokens=5,
        )
    except Exception as e:
        logger.error("Health check after reload failed: %s", e)
        _llm = None
        raise HTTPException(status_code=500, detail=f"Post-reload health check failed: {e}")
    version = _read_version()
    logger.info("Model reloaded successfully: %s", version)
    return ReloadResponse(status="reloaded", model_version=version)
