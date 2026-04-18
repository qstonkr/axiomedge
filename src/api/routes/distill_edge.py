"""Distill edge server management endpoints.

Extracted from distill.py for SRP. Handles edge server registration,
heartbeat, provisioning, fleet management, and manifest delivery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/distill", tags=["Distill Edge"])


def _get_state() -> dict[str, Any]:
    from src.api.app import _get_state as _inner
    return _inner()


def _get_distill_repo() -> Any:
    repo = _get_state().get("distill_repo")
    if not repo:
        raise HTTPException(status_code=503, detail="Distill plugin not initialized")
    return repo


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ServerUpdateRequest(BaseModel):
    update_type: str = "both"  # model | app | both


class BulkServerUpdateRequest(BaseModel):
    profile_name: str
    update_type: str = "both"


class HeartbeatRequest(BaseModel):
    store_id: str
    status: str = "online"
    model_version: str | None = None
    model_sha256: str | None = None
    app_version: str | None = None
    os_type: str | None = None
    cpu_info: str | None = None
    ram_total_mb: int | None = None
    ram_used_mb: int | None = None
    disk_free_mb: int | None = None
    avg_latency_ms: int | None = None
    total_queries: int = 0
    success_rate: float | None = None
    server_ip: str | None = None
    profile_name: str | None = None
    display_name: str | None = None


class StoreRegisterRequest(BaseModel):
    """매장 사전 등록 요청."""
    store_id: str
    profile_name: str
    display_name: str = ""


@router.post("/edge-servers/register")
async def register_edge_server(
    request: StoreRegisterRequest,
) -> dict[str, str]:
    """매장 사전 등록 — 본사에서 장비 출고 전 등록.

    API key를 자동 발급하고, 장비에 세팅할 완전한 출고 명령어를 반환.
    """
    import hashlib
    import re
    import secrets

    if not re.match(r"^[a-z0-9][a-z0-9_-]{1,48}[a-z0-9]$", request.store_id):
        raise HTTPException(
            status_code=400,
            detail="store_id는 영소문자, 숫자, 하이픈만 가능 (3~50자)",
        )

    repo = _get_distill_repo()

    existing = await repo.get_edge_server(request.store_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Store '{request.store_id}' already registered")

    api_key = f"edge-{secrets.token_urlsafe(24)}"
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    try:
        await repo.register_edge_server(
            store_id=request.store_id,
            profile_name=request.profile_name,
            display_name=request.display_name or request.store_id,
            api_key_hash=api_key_hash,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # 출고 설정 생성 (API key 포함)
    provision = _build_provision_config(request.store_id, request.profile_name, api_key)

    return {
        "store_id": request.store_id,
        "api_key": api_key,
        "profile_name": request.profile_name,
        "status": "pending",
        "provision_command": provision["command"],
        "message": "매장 등록 완료. 아래 출고 명령어를 장비에서 실행하세요.",
    }


def _build_provision_config(
    store_id: str, profile_name: str, api_key: str | None = None,
) -> dict[str, Any]:
    """출고 설정 생성 (내부 헬퍼)."""
    import os
    import socket
    # 환경변수 우선, 없으면 로컬 IP 자동 감지
    api_url = os.getenv("EXTERNAL_API_URL", "")
    if not api_url:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            api_url = f"http://{local_ip}:8000"
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            # Local IP 해결 실패 → localhost fallback. edge server 는 실제
            # 네트워크 IP 가 필요하므로 이 fallback 이 발동되면 provision
            # command 가 로컬 테스트만 가능. 운영팀이 인지할 수 있도록 warning.
            logger.warning(
                "Failed to resolve local IP for provision URL, "
                "falling back to http://localhost:8000: %s", e,
            )
            api_url = "http://localhost:8000"

    # S3 직접 접근이 아닌 API를 통해 manifest 제공 (S3 퍼블릭 차단 대응)
    manifest_url = f"{api_url}/api/v1/distill/manifest/{profile_name}"

    parts = [
        f"STORE_ID={store_id}",
        f"MANIFEST_URL={manifest_url}",
        f"CENTRAL_API_URL={api_url}",
    ]
    if api_key:
        parts.insert(1, f"EDGE_API_KEY={api_key}")

    command = (
        f"curl -sfL {api_url}/api/v1/distill/provision.sh -o /tmp/provision.sh && \\\n  "
        + " \\\n  ".join(parts)
        + " \\\n  bash /tmp/provision.sh"
    )

    return {
        "store_id": store_id,
        "profile_name": profile_name,
        "env": {
            "STORE_ID": store_id,
            "EDGE_API_KEY": api_key or "(등록 시 발급된 키 사용)",
            "MANIFEST_URL": manifest_url,
            "CENTRAL_API_URL": api_url,
        },
        "command": command,
    }


@router.get("/edge-servers/{store_id}/provision")
async def provision_edge_server(store_id: str) -> dict[str, Any]:
    """출고 설정 — 장비에 세팅할 환경 설정 반환.

    ⚠ EDGE_API_KEY는 등록 시 1회만 발급. 분실 시 삭제 후 재등록 필요.
    """
    repo = _get_distill_repo()
    server = await repo.get_edge_server(store_id)
    if not server:
        raise HTTPException(status_code=404, detail="Store not found")

    config = _build_provision_config(
        store_id, server.get("profile_name", ""),
    )
    return config


@router.post("/edge-servers/heartbeat")
async def edge_server_heartbeat(
    request: HeartbeatRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """heartbeat 수신 (등록 겸용, Bearer 인증 필수)."""
    repo = _get_distill_repo()

    api_key = ""
    if authorization and authorization.startswith("Bearer "):
        api_key = authorization[7:]
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing Authorization Bearer token")

    try:
        result = await repo.upsert_heartbeat(request.model_dump(), api_key)
        return result
    except PermissionError:
        raise HTTPException(status_code=401, detail="Invalid API key")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/edge-servers")
async def list_edge_servers(
    profile_name: str | None = None,
    status: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """등록된 엣지 서버 목록."""
    repo = _get_distill_repo()
    servers = await repo.list_edge_servers(profile_name=profile_name, status=status)
    return {"items": servers}


@router.get("/manifest/{profile_name}")
async def get_manifest(profile_name: str) -> dict[str, Any]:
    """매니페스트 프록시 — S3에서 가져오고 download_url을 매 호출마다 재서명.

    저장된 download_url은 임시 STS 세션 만료 시 함께 무효화되므로,
    엣지가 조회할 때마다 fresh한 pre-signed URL을 발급해 응답에 주입한다.
    """
    import json as _json

    from src.distill.deployer import _parse_s3_uri, _s3_client

    repo = _get_distill_repo()
    profile = await repo.get_profile(profile_name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    config = profile.get("config", "{}")
    if isinstance(config, str):
        config = _json.loads(config) if config else {}
    deploy = config.get("deploy", {})
    bucket = deploy.get("s3_bucket", "gs-knowledge-models")
    prefix = deploy.get("s3_prefix", f"{profile_name}/")
    manifest_key = f"{prefix}manifest.json"

    try:
        s3 = _s3_client()
        obj = s3.get_object(Bucket=bucket, Key=manifest_key)
        manifest = _json.loads(obj["Body"].read().decode())
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        raise HTTPException(status_code=404, detail=f"Manifest not found: {e}")

    s3_uri = manifest.get("s3_uri", "")
    if s3_uri.startswith("s3://"):
        try:
            model_bucket, model_key = _parse_s3_uri(s3_uri)
            manifest["download_url"] = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": model_bucket, "Key": model_key},
                ExpiresIn=86400,
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Failed to refresh presigned URL for %s: %s", s3_uri, e)

    return manifest


class AppVersionRequest(BaseModel):
    version: str


@router.post("/profiles/{profile_name}/app-version")
async def set_app_version(
    profile_name: str, request: AppVersionRequest,
) -> dict[str, Any]:
    """엣지 앱 소스 버전 태그를 갱신.

    실제 바이너리/패키지 배포는 하지 않는다. S3 manifest.json 의
    `app_version` 필드만 업데이트. 엣지는 다음 heartbeat 시 이 값을
    자기 `.app_version` 과 비교해 차이가 있으면 중앙 API 의 edge-files
    엔드포인트에서 server.py / sync.py 를 재다운로드 (source-file update).
    """
    import json as _json

    from src.distill.deployer import _s3_client

    repo = _get_distill_repo()
    profile = await repo.get_profile(profile_name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    config = profile.get("config", "{}")
    if isinstance(config, str):
        config = _json.loads(config) if config else {}
    deploy = config.get("deploy", {})
    bucket = deploy.get("s3_bucket", "gs-knowledge-models")
    prefix = deploy.get("s3_prefix", f"{profile_name}/")
    manifest_key = f"{prefix}manifest.json"

    def _update() -> dict[str, Any]:
        s3 = _s3_client()
        try:
            obj = s3.get_object(Bucket=bucket, Key=manifest_key)
            manifest = _json.loads(obj["Body"].read().decode())
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            # 기존 manifest 가 없거나 접근 실패하면 새로 시작. 하지만 기존
            # 값이 있는데 조용히 날리면 버전 히스토리 손실이므로 로그 남김.
            logger.warning(
                "Failed to fetch existing manifest s3://%s/%s — starting fresh: %s",
                bucket, manifest_key, e,
            )
            manifest = {}
        manifest["app_version"] = request.version
        s3.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=_json.dumps(manifest, ensure_ascii=False, indent=2),
            ContentType="application/json",
        )
        return manifest

    try:
        updated = await asyncio.to_thread(_update)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to update app version: {e}")

    return {
        "success": True,
        "profile_name": profile_name,
        "app_version": request.version,
        "manifest": updated,
    }


@router.get("/provision.sh")
async def download_provision_script() -> Any:
    """출고 스크립트 다운로드."""
    from pathlib import Path
    from fastapi.responses import FileResponse
    script = Path(__file__).resolve().parents[3] / "src" / "edge" / "provision.sh"
    if not script.exists():
        raise HTTPException(status_code=404, detail="provision.sh not found")
    return FileResponse(script, media_type="text/plain", filename="provision.sh")


@router.get("/edge-files/{filename}")
async def download_edge_file(filename: str) -> Any:
    """엣지 서버 코드 파일 다운로드 (server.py, sync.py)."""
    from pathlib import Path
    from fastapi.responses import FileResponse
    allowed = {"server.py", "sync.py"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    filepath = Path(__file__).resolve().parents[3] / "src" / "edge" / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    return FileResponse(filepath, media_type="text/plain", filename=filename)


@router.get("/edge-servers/fleet-stats")
async def fleet_stats(profile_name: str) -> dict[str, Any]:
    """fleet 현황 통계."""
    repo = _get_distill_repo()
    return await repo.get_fleet_stats(profile_name)


@router.get("/edge-servers/{store_id}")
async def get_edge_server(store_id: str) -> dict[str, Any]:
    """서버 상세."""
    repo = _get_distill_repo()
    server = await repo.get_edge_server(store_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.delete("/edge-servers/{store_id}")
async def delete_edge_server(store_id: str) -> dict[str, bool]:
    """서버 등록 해제."""
    repo = _get_distill_repo()
    deleted = await repo.delete_edge_server(store_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"success": True}


@router.post("/edge-servers/{store_id}/request-update")
async def request_server_update(
    store_id: str, request: ServerUpdateRequest,
) -> dict[str, Any]:
    """엣지 서버 업데이트 요청 (다음 sync 주기에 반영)."""
    repo = _get_distill_repo()
    try:
        return await repo.request_server_update(store_id, request.update_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/edge-servers/bulk-request-update")
async def bulk_request_update(
    request: BulkServerUpdateRequest,
) -> dict[str, int]:
    """구버전 서버 전체 업데이트 요청."""
    repo = _get_distill_repo()
    count = await repo.bulk_request_server_update(
        request.profile_name, request.update_type,
    )
    return {"updated": count}
