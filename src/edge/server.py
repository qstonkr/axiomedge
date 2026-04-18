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
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.responses import HTMLResponse
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


async def verify_api_key(key: Optional[str] = Security(api_key_header)) -> None:
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
_counter_lock = threading.Lock()
_query_count: int = 0
_success_count: int = 0
_recent_latencies: deque[int] = deque(maxlen=100)


def _get_llm() -> Any:
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


# 캐시: _read_version을 매 요청마다 디스크 읽기 대신 캐시
_cached_version: str = ""


def _get_cached_version() -> str:
    """캐시된 모델 버전. /reload 시 갱신."""
    global _cached_version
    if not _cached_version:
        _cached_version = _read_version()
    return _cached_version


def _sync_log_query(entry: dict) -> None:
    """동기 파일 쓰기 (to_thread용)."""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("Failed to write log: %s", e)


async def _log_query(query: str, answer: str, latency_ms: int, success: bool) -> None:
    import asyncio
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "store_id": STORE_ID,
        "query": query,
        "answer": answer,
        "latency_ms": latency_ms,
        "success": success,
        "model_version": _get_cached_version(),
    }
    await asyncio.to_thread(_sync_log_query, entry)


@app.on_event("startup")
def load_model() -> None:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
async def ask(request: AskRequest, _: None = Depends(verify_api_key)) -> AskResponse:
    llm = _get_llm()
    t0 = time.monotonic()
    try:
        # stop 파라미터는 필수: Gemma 3 는 턴 경계에 <end_of_turn> (token 106) 을
        # 생성하도록 학습됐지만, GGUF 메타의 eos_token_id 는 <eos> (token 1) 하나만
        # 담겨 llama.cpp 가 106 을 멈춤 조건으로 인식 못 한다. 결과로 모델이
        # <end_of_turn> 다음 학습 데이터 패턴인 <start_of_turn>user 를 계속
        # 이어붙여 무한 에코 루프가 된다. stop 문자열로 명시 차단.
        output = llm.create_chat_completion(
            messages=[{"role": "user", "content": request.query}],
            max_tokens=MAX_TOKENS,
            stop=["<end_of_turn>", "<start_of_turn>"],
        )
        answer = output["choices"][0]["message"]["content"].strip()
        latency_ms = int((time.monotonic() - t0) * 1000)
        success = True
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.error("Inference failed: %s", e)
        answer = ""
        latency_ms = int((time.monotonic() - t0) * 1000)
        success = False
    await _log_query(request.query, answer, latency_ms, success)
    global _query_count, _success_count
    with _counter_lock:
        _query_count += 1
        if success:
            _success_count += 1
        _recent_latencies.append(latency_ms)
    return AskResponse(answer=answer, latency_ms=latency_ms, success=success,
                       model_version=_get_cached_version())


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok" if _llm is not None else "no_model",
        store_id=STORE_ID, model_version=_read_version(),
        model_loaded=_llm is not None,
        uptime_sec=round(time.monotonic() - (_startup_time or time.monotonic()), 1),
    )


@app.get("/heartbeat")
async def heartbeat() -> dict:
    """sync.py가 호출하여 중앙에 push할 상태 정보 수집."""
    import platform

    manifest_path = Path(MODEL_PATH).parent / "manifest.json"
    model_sha = ""
    if manifest_path.exists():
        try:
            model_sha = json.loads(manifest_path.read_text()).get("sha256", "")
        except (json.JSONDecodeError, OSError):
            pass

    avg_lat = int(sum(_recent_latencies) / len(_recent_latencies)) if _recent_latencies else 0
    success_rate = (_success_count / _query_count) if _query_count > 0 else None

    return {
        "store_id": STORE_ID,
        "status": "online" if _llm else "no_model",
        "model_version": _read_version(),
        "model_sha256": model_sha,
        "app_version": os.getenv("APP_VERSION", "dev"),
        "os_type": platform.system().lower(),
        "cpu_info": platform.processor(),
        "ram_total_mb": _get_system_ram_total(),
        "ram_used_mb": _get_system_ram_used(),
        "disk_free_mb": _get_disk_free(),
        "avg_latency_ms": avg_lat,
        "total_queries": _query_count,
        "success_rate": round(success_rate, 4) if success_rate is not None else None,
        "uptime_sec": round(time.monotonic() - _startup_time, 1) if _startup_time else 0,
    }


def _get_system_ram_total() -> Optional[int]:
    try:
        import psutil
        return int(psutil.virtual_memory().total / 1024 / 1024)
    except ImportError:
        return None


def _get_system_ram_used() -> Optional[int]:
    try:
        import psutil
        return int(psutil.virtual_memory().used / 1024 / 1024)
    except ImportError:
        return None


def _get_disk_free() -> Optional[int]:
    try:
        import psutil
        return int(psutil.disk_usage("/").free / 1024 / 1024)
    except ImportError:
        return None


@app.get("/", response_class=HTMLResponse)
async def test_ui() -> str:
    """브라우저 테스트 + 관리 페이지."""
    version = _get_cached_version()
    status = "online" if _llm else "no_model"

    # manifest 정보
    manifest_info = ""
    manifest_path = Path(MODEL_PATH).parent / "manifest.json"
    if manifest_path.exists():
        try:
            import html
            m = json.loads(manifest_path.read_text())
            dl_url = html.escape(m.get("download_url", "N/A")[:80])
            s3_uri = html.escape(m.get("s3_uri", "N/A"))
            sha = m.get("sha256", "N/A")[:16]
            app_ver = html.escape(m.get("app_version", "N/A"))
            manifest_info = (
                f"S3: {s3_uri}<br>"
                f"Download: {dl_url}...<br>"
                f"SHA256: {sha}...<br>"
                f"App version (latest): {app_ver}"
            )
        except (json.JSONDecodeError, OSError):
            manifest_info = "manifest 파싱 실패"
    else:
        manifest_info = "manifest.json 없음"

    return TEST_PAGE_HTML.format(
        store_id=STORE_ID, version=version, status=status,
        query_count=_query_count, manifest_info=manifest_info,
        model_path=MODEL_PATH, log_dir=str(LOG_DIR),
        n_ctx=N_CTX, n_threads=N_THREADS, max_tokens=MAX_TOKENS,
        app_version=os.getenv("APP_VERSION", "dev"),
    )


@app.post("/reload", response_model=ReloadResponse, dependencies=[Depends(verify_api_key)])
async def reload_model() -> ReloadResponse:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.error("Reload failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Model reload failed: {e}")
    try:
        _llm.create_chat_completion(
            messages=[{"role": "user", "content": "테스트"}], max_tokens=5,
            stop=["<end_of_turn>", "<start_of_turn>"],
        )
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.error("Health check after reload failed: %s", e)
        _llm = None
        raise HTTPException(status_code=500, detail=f"Post-reload health check failed: {e}")
    global _cached_version
    _cached_version = _read_version()
    version = _cached_version
    logger.info("Model reloaded successfully: %s", version)
    return ReloadResponse(status="reloaded", model_version=version)


# ---------------------------------------------------------------------------
# Test Page HTML
# ---------------------------------------------------------------------------

TEST_PAGE_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GS Edge LLM - {store_id}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; padding: 20px; max-width: 800px; margin: 0 auto; }}
  .header {{ background: #1a73e8; color: white; padding: 16px 24px; border-radius: 12px;
             margin-bottom: 20px; }}
  .header h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .header .meta {{ font-size: 13px; opacity: 0.85; }}
  .status {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
             font-size: 12px; font-weight: 600; }}
  .status.online {{ background: #34a853; }}
  .status.no_model {{ background: #ea4335; }}
  .chat {{ background: white; border-radius: 12px; padding: 20px;
           box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 16px; }}
  .messages {{ min-height: 200px; max-height: 500px; overflow-y: auto;
               margin-bottom: 16px; }}
  .msg {{ padding: 10px 14px; border-radius: 12px; margin-bottom: 8px;
          max-width: 85%; line-height: 1.5; font-size: 14px; }}
  .msg.user {{ background: #e3f2fd; margin-left: auto; text-align: right; }}
  .msg.bot {{ background: #f0f0f0; }}
  .msg .latency {{ font-size: 11px; color: #888; margin-top: 4px; }}
  .input-row {{ display: flex; gap: 8px; }}
  .input-row input {{ flex: 1; padding: 12px 16px; border: 1px solid #ddd;
                      border-radius: 8px; font-size: 14px; outline: none; }}
  .input-row input:focus {{ border-color: #1a73e8; }}
  .input-row button {{ padding: 12px 24px; background: #1a73e8; color: white;
                       border: none; border-radius: 8px; font-size: 14px;
                       cursor: pointer; white-space: nowrap; }}
  .input-row button:hover {{ background: #1557b0; }}
  .input-row button:disabled {{ background: #ccc; cursor: not-allowed; }}
  .loading {{ display: none; text-align: center; padding: 12px; color: #888; }}
</style>
</head>
<body>
<div class="header">
  <h1>GS Edge LLM Server</h1>
  <div class="meta">
    매장: {store_id} |
    모델: {version} |
    <span class="status {status}">{status}</span> |
    질의: {query_count}건 | 앱: {app_version}
  </div>
</div>
<details style="background:white;border-radius:12px;padding:16px;margin-bottom:16px;
  box-shadow:0 1px 3px rgba(0,0,0,0.1)">
  <summary style="cursor:pointer;font-weight:600;font-size:14px">시스템 정보 / Manifest</summary>
  <div style="font-size:13px;color:#555;margin-top:12px;line-height:1.8">
    <b>모델 경로:</b> {model_path}<br>
    <b>로그 디렉토리:</b> {log_dir}<br>
    <b>설정:</b> n_ctx={n_ctx}, n_threads={n_threads}, max_tokens={max_tokens}<br>
    <hr style="margin:8px 0">
    <b>Manifest:</b><br>{manifest_info}
  </div>
</details>
<div class="chat">
  <div class="messages" id="messages"></div>
  <div class="loading" id="loading">응답 생성 중...</div>
  <div class="input-row" style="margin-bottom:8px">
    <input type="password" id="apikey" placeholder="API Key (선택)"
           style="max-width:200px; font-size:12px"
           onfocus="this.type='text'" onblur="this.type='password'">
  </div>
  <div class="input-row">
    <input type="text" id="query" placeholder="질문을 입력하세요..."
           onkeydown="if(event.key==='Enter')ask()">
    <button onclick="ask()" id="btn">전송</button>
  </div>
</div>
<script>
async function ask() {{
  const input = document.getElementById('query');
  const q = input.value.trim();
  if (!q) return;
  input.value = '';
  const btn = document.getElementById('btn');
  btn.disabled = true;
  addMsg(q, 'user');
  document.getElementById('loading').style.display = 'block';
  try {{
    const t0 = performance.now();
    const apiKey = document.getElementById('apikey').value;
    const hdrs = {{'Content-Type': 'application/json'}};
    if (apiKey) hdrs['X-API-Key'] = apiKey;
    const res = await fetch('/ask', {{
      method: 'POST',
      headers: hdrs,
      body: JSON.stringify({{query: q}})
    }});
    const data = await res.json();
    const clientMs = Math.round(performance.now() - t0);
    addMsg(data.answer || '(응답 없음)',  'bot',
           `서버: ${{data.latency_ms}}ms | 클라이언트: ${{clientMs}}ms | 모델: ${{data.model_version}}`);
  }} catch(e) {{
    addMsg('오류: ' + e.message, 'bot');
  }}
  document.getElementById('loading').style.display = 'none';
  btn.disabled = false;
  input.focus();
}}
function addMsg(text, cls, meta) {{
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  if (meta) {{
    const span = document.createElement('div');
    span.className = 'latency';
    span.textContent = meta;
    div.appendChild(span);
  }}
  const msgs = document.getElementById('messages');
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}}
</script>
</body>
</html>"""
