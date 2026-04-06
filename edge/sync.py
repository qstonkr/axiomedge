"""엣지 모델 S3 동기화 + 로그 업로드.

cron으로 주기 실행. Pre-signed URL 방식으로 S3 자격증명 불필요.

Usage:
    python edge/sync.py                    # 전체 (모델 체크 + 로그 업로드)
    python edge/sync.py --check-only       # 모델 체크만
    python edge/sync.py --upload-logs-only  # 로그 업로드만
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger("edge.sync")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MANIFEST_URL = os.getenv("MANIFEST_URL", "")
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/models"))
LOG_DIR = Path(os.getenv("LOG_DIR", "/logs"))
STORE_ID = os.getenv("STORE_ID", "unknown")
EDGE_API_KEY = os.getenv("EDGE_API_KEY", "")
EDGE_SERVER_URL = os.getenv("EDGE_SERVER_URL", "http://localhost:8080")
LOG_UPLOAD_URL = os.getenv("LOG_UPLOAD_URL", "")

CURRENT_DIR = MODEL_DIR / "current"
STAGING_DIR = MODEL_DIR / "staging"
ROLLBACK_DIR = MODEL_DIR / "rollback"


def _read_local_version() -> str:
    manifest = CURRENT_DIR / "manifest.json"
    if manifest.exists():
        try:
            return json.loads(manifest.read_text()).get("version", "")
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def check_and_update() -> bool:
    """S3 manifest 확인 → 새 버전이면 다운로드 → 교체 → reload."""
    if not MANIFEST_URL:
        logger.warning("MANIFEST_URL not set, skipping")
        return False
    try:
        resp = httpx.get(MANIFEST_URL, timeout=30)
        resp.raise_for_status()
        remote = resp.json()
    except Exception as e:
        logger.error("Failed to fetch manifest: %s", e)
        return False

    remote_version = remote.get("version", "")
    local_version = _read_local_version()
    if remote_version == local_version:
        logger.info("Up to date: %s", local_version)
        return False

    logger.info("New version: %s → %s", local_version, remote_version)
    download_url = remote.get("download_url", "")
    if not download_url:
        logger.error("No download_url in manifest")
        return False

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    gguf_path = STAGING_DIR / "model.gguf"
    try:
        logger.info("Downloading model (%s)...", remote_version)
        with httpx.stream("GET", download_url, timeout=600) as r:
            r.raise_for_status()
            with open(gguf_path, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        logger.info("Download complete: %.1f MB", gguf_path.stat().st_size / 1e6)
    except Exception as e:
        logger.error("Download failed: %s", e)
        shutil.rmtree(STAGING_DIR, ignore_errors=True)
        return False

    expected_sha = remote.get("sha256", "")
    if expected_sha:
        actual_sha = _sha256_file(gguf_path)
        if actual_sha != expected_sha:
            logger.error("SHA256 mismatch")
            shutil.rmtree(STAGING_DIR, ignore_errors=True)
            return False

    (STAGING_DIR / "manifest.json").write_text(json.dumps(remote, ensure_ascii=False))

    try:
        if ROLLBACK_DIR.exists():
            shutil.rmtree(ROLLBACK_DIR)
        if CURRENT_DIR.exists():
            CURRENT_DIR.rename(ROLLBACK_DIR)
        STAGING_DIR.rename(CURRENT_DIR)
    except OSError as e:
        logger.error("Model swap failed: %s", e)
        return False

    try:
        headers = {"X-API-Key": EDGE_API_KEY} if EDGE_API_KEY else {}
        resp = httpx.post(f"{EDGE_SERVER_URL}/reload", headers=headers, timeout=60)
        resp.raise_for_status()
        logger.info("Server reloaded: %s", resp.json())
    except Exception as e:
        logger.error("Reload failed, rolling back: %s", e)
        _rollback()
        return False
    return True


def _rollback() -> None:
    if not ROLLBACK_DIR.exists():
        logger.error("No rollback directory")
        return
    try:
        if CURRENT_DIR.exists():
            shutil.rmtree(CURRENT_DIR)
        ROLLBACK_DIR.rename(CURRENT_DIR)
        headers = {"X-API-Key": EDGE_API_KEY} if EDGE_API_KEY else {}
        httpx.post(f"{EDGE_SERVER_URL}/reload", headers=headers, timeout=60)
        logger.info("Rolled back")
    except Exception as e:
        logger.error("Rollback failed: %s", e)


def upload_logs() -> int:
    """로컬 로그 → 업로드 (rename 방식으로 race condition 방지)."""
    log_file = LOG_DIR / "queries.jsonl"
    if not log_file.exists() or log_file.stat().st_size == 0:
        return 0
    if not LOG_UPLOAD_URL:
        logger.warning("LOG_UPLOAD_URL not set")
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    upload_file = LOG_DIR / f"upload_{STORE_ID}_{ts}.jsonl"
    try:
        log_file.rename(upload_file)
    except OSError as e:
        logger.error("Rename failed: %s", e)
        return 0

    line_count = sum(1 for _ in open(upload_file, encoding="utf-8"))
    try:
        with open(upload_file, "rb") as f:
            resp = httpx.put(LOG_UPLOAD_URL, content=f.read(), timeout=60)
            resp.raise_for_status()
        logger.info("Uploaded %d entries", line_count)
        upload_file.unlink()
        return line_count
    except Exception as e:
        logger.error("Upload failed: %s", e)
        try:
            with open(upload_file, encoding="utf-8") as src, \
                 open(log_file, "a", encoding="utf-8") as dst:
                dst.write(src.read())
            upload_file.unlink()
        except OSError:
            pass
        return 0


def main():
    parser = argparse.ArgumentParser(description="Edge model sync")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--upload-logs-only", action="store_true")
    args = parser.parse_args()

    if args.upload_logs_only:
        upload_logs()
    elif args.check_only:
        check_and_update()
    else:
        check_and_update()
        upload_logs()


if __name__ == "__main__":
    main()
