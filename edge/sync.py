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
CENTRAL_API_URL = os.getenv("CENTRAL_API_URL", "")
APP_DIR = Path(os.getenv("APP_DIR", "/opt/edge-model"))
APP_VERSION_FILE = APP_DIR / ".app_version"
LAUNCHCTL_LABEL = os.getenv("LAUNCHCTL_LABEL", "com.gs.edge-server")

CURRENT_DIR = MODEL_DIR / "current"
STAGING_DIR = MODEL_DIR / "staging"
ROLLBACK_DIR = MODEL_DIR / "rollback"

# 엣지가 중앙 API에서 받아오는 소스 파일 (venv는 그대로 유지, 소스만 교체).
EDGE_SOURCE_FILES = ("server.py", "sync.py")


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
    except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
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

    # 기존 current가 실제 모델을 담고 있으면 rollback으로 보관, 빈 디렉토리면 그냥 삭제.
    # (최초 설치 시 current는 provision.sh가 만든 빈 디렉토리 → 유효 rollback이 아님)
    try:
        if ROLLBACK_DIR.exists():
            shutil.rmtree(ROLLBACK_DIR)
        if CURRENT_DIR.exists():
            if (CURRENT_DIR / "model.gguf").exists():
                CURRENT_DIR.rename(ROLLBACK_DIR)
            else:
                shutil.rmtree(CURRENT_DIR)
        STAGING_DIR.rename(CURRENT_DIR)
    except OSError as e:
        logger.error("Model swap failed: %s", e)
        return False

    try:
        headers = {"X-API-Key": EDGE_API_KEY} if EDGE_API_KEY else {}
        resp = httpx.post(f"{EDGE_SERVER_URL}/reload", headers=headers, timeout=60)
        resp.raise_for_status()
        logger.info("Server reloaded: %s", resp.json())
        return True
    except httpx.ConnectError as e:
        # edge-server가 꺼져 있거나 아직 안 떠 있음. 새 모델은 그대로 두고
        # 서버가 다음 기동 시 자동으로 적재하게 함 — rollback하면 방금 받은 모델이 사라짐.
        logger.warning(
            "Reload skipped (server not reachable: %s). "
            "New model left in place — will be loaded on next edge-server startup.",
            e,
        )
        return True
    except Exception as e:  # noqa: BLE001
        # 서버는 떠있는데 reload 자체가 실패한 경우 — 새 모델이 로드 불가 상태일 수 있음.
        # rollback 디렉토리에 유효 모델이 있을 때만 복원 시도.
        logger.error("Reload failed: %s", e)
        if (ROLLBACK_DIR / "model.gguf").exists():
            logger.info("Attempting rollback to previous model")
            _rollback()
        else:
            logger.warning("No valid rollback target — leaving new model in place")
        return False


def _rollback() -> None:
    """이전 모델로 복원. rollback 디렉토리에 실제 model.gguf가 있을 때만 호출할 것."""
    if not (ROLLBACK_DIR / "model.gguf").exists():
        logger.error("No valid rollback model (rollback dir empty or missing)")
        return
    try:
        if CURRENT_DIR.exists():
            shutil.rmtree(CURRENT_DIR)
        ROLLBACK_DIR.rename(CURRENT_DIR)
        headers = {"X-API-Key": EDGE_API_KEY} if EDGE_API_KEY else {}
        try:
            httpx.post(f"{EDGE_SERVER_URL}/reload", headers=headers, timeout=60)
        except Exception as e:  # noqa: BLE001
            logger.warning("Rollback reload ping failed (server may be down): %s", e)
        logger.info("Rolled back to previous model")
    except Exception as e:  # noqa: BLE001
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

    with open(upload_file, encoding="utf-8") as f:
        line_count = sum(1 for _ in f)
    try:
        with open(upload_file, "rb") as f:
            resp = httpx.put(LOG_UPLOAD_URL, content=f.read(), timeout=60)
            resp.raise_for_status()
        logger.info("Uploaded %d entries", line_count)
        upload_file.unlink()
        return line_count
    except Exception as e:  # noqa: BLE001
        logger.error("Upload failed: %s", e)
        try:
            with open(upload_file, encoding="utf-8") as src, \
                 open(log_file, "a", encoding="utf-8") as dst:
                dst.write(src.read())
            upload_file.unlink()
        except OSError:
            pass
        return 0


def push_heartbeat() -> None:
    """엣지 → 중앙 서버로 heartbeat push. 응답에 따라 업데이트 수행."""
    if not CENTRAL_API_URL:
        logger.debug("CENTRAL_API_URL not set, skipping heartbeat")
        return

    # 로컬 서버에서 상태 수집
    try:
        resp = httpx.get(f"{EDGE_SERVER_URL}/heartbeat", timeout=5)
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to collect heartbeat: %s", e)
        return

    # 중앙에 push
    headers = {"Authorization": f"Bearer {EDGE_API_KEY}"} if EDGE_API_KEY else {}
    try:
        resp = httpx.post(
            f"{CENTRAL_API_URL}/api/v1/distill/edge-servers/heartbeat",
            json=data, headers=headers, timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("Heartbeat push failed: %s", e)
        return

    # 응답에 따라 업데이트
    if result.get("pending_model_update"):
        logger.info("Model update requested by central server")
        check_and_update()
    if result.get("pending_app_update"):
        logger.info("App update requested by central server")
        update_source_files()


def _read_local_app_version() -> str:
    if APP_VERSION_FILE.exists():
        try:
            return APP_VERSION_FILE.read_text().strip()
        except OSError:
            pass
    return ""


def _remote_app_version() -> str:
    """manifest.json 의 app_version 필드 조회 (sync.py는 MANIFEST_URL로 중앙 API 경유)."""
    if not MANIFEST_URL:
        return ""
    try:
        resp = httpx.get(MANIFEST_URL, timeout=30)
        resp.raise_for_status()
        return resp.json().get("app_version", "")
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to fetch manifest for app version: %s", e)
        return ""


def _restart_edge_server() -> None:
    """플랫폼별 엣지 서버 재시작."""
    import platform
    import subprocess
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHCTL_LABEL}"],
                check=True, timeout=10,
            )
        elif system == "Linux":
            subprocess.run(
                ["systemctl", "restart", "edge-server"],
                check=True, timeout=10,
            )
        else:
            logger.warning("Unsupported platform for auto-restart: %s", system)
    except Exception as e:
        logger.error("Edge server restart failed: %s", e)
        raise


def _health_check(retries: int = 10, delay: float = 1.0) -> bool:
    """재시작 후 헬스체크. 최대 retries*delay 초 동안 대기."""
    import time
    for _ in range(retries):
        try:
            resp = httpx.get(f"{EDGE_SERVER_URL}/health", timeout=3)
            if resp.status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(delay)
    return False


def update_source_files() -> bool:
    """중앙 API에서 server.py / sync.py 재다운로드 → 원자적 교체 → 재시작 → 헬스체크.

    실패 시 .bak 파일로 롤백. venv / 패키지는 건드리지 않음.
    """
    if not CENTRAL_API_URL:
        logger.warning("CENTRAL_API_URL not set, skipping app update")
        return False

    remote_ver = _remote_app_version()
    local_ver = _read_local_app_version()
    if remote_ver and remote_ver == local_ver:
        logger.info("App already up to date: %s", local_ver)
        return False

    logger.info("App update: %s → %s", local_ver or "(none)", remote_ver or "(unknown)")

    # 1. 새 파일을 .new 로 받기 (실패해도 기존 파일 무사)
    staged: list[tuple[Path, Path]] = []
    try:
        for fname in EDGE_SOURCE_FILES:
            url = f"{CENTRAL_API_URL}/api/v1/distill/edge-files/{fname}"
            target = APP_DIR / fname
            new_path = APP_DIR / f"{fname}.new"
            logger.info("Downloading %s...", fname)
            with httpx.stream("GET", url, timeout=60) as r:
                r.raise_for_status()
                with open(new_path, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=8192):
                        f.write(chunk)
            staged.append((target, new_path))
    except Exception as e:  # noqa: BLE001
        logger.error("Source download failed: %s", e)
        for _, new_path in staged:
            new_path.unlink(missing_ok=True)
        return False

    # 2. 기존 파일을 .bak 로 이동하고 .new → 원본 원자적 교체
    backups: list[tuple[Path, Path]] = []
    try:
        for target, new_path in staged:
            if target.exists():
                bak_path = APP_DIR / f"{target.name}.bak"
                bak_path.unlink(missing_ok=True)
                target.rename(bak_path)
                backups.append((target, bak_path))
            new_path.rename(target)
    except OSError as e:
        logger.error("Source swap failed: %s — restoring backups", e)
        for target, bak_path in backups:
            if bak_path.exists():
                target.unlink(missing_ok=True)
                bak_path.rename(target)
        for _, new_path in staged:
            new_path.unlink(missing_ok=True)
        return False

    # 3. 엣지 서버 재시작
    try:
        _restart_edge_server()
    except Exception:  # noqa: BLE001
        _restore_backups(backups)
        return False

    # 4. 헬스체크 — 실패 시 .bak 복구 + 재시작
    if not _health_check():
        logger.error("Health check failed after app update — rolling back")
        _restore_backups(backups)
        try:
            _restart_edge_server()
        except Exception as e:  # noqa: BLE001
            logger.error("Rollback restart failed: %s", e)
        return False

    # 5. 성공: .bak 정리 + 버전 파일 기록
    for _, bak_path in backups:
        bak_path.unlink(missing_ok=True)
    try:
        APP_VERSION_FILE.write_text(remote_ver or "unknown")
    except OSError as e:
        logger.warning("Failed to write app version file: %s", e)
    logger.info("App updated successfully to %s", remote_ver)
    return True


def _restore_backups(backups: list[tuple[Path, Path]]) -> None:
    """교체 실패 시 .bak → 원본 복구."""
    import shutil
    for target, bak_path in backups:
        if bak_path.exists():
            try:
                target.unlink(missing_ok=True)
                shutil.move(str(bak_path), str(target))
            except OSError as e:
                logger.error("Failed to restore backup %s: %s", bak_path, e)


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
        push_heartbeat()
        upload_logs()


if __name__ == "__main__":
    main()
