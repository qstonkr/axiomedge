"""엣지 서버 바이너리 빌드 + S3 업로드.

PyInstaller로 edge/server.py + sync.py를 OS별 단일 바이너리로 패키징.

Usage:
    # 현재 OS용 빌드
    uv run python scripts/build_edge_binary.py --version v1.0.0

    # 특정 OS용 (크로스 컴파일은 해당 OS에서 실행 필요)
    uv run python scripts/build_edge_binary.py --version v1.0.0 --os linux

    # S3 업로드 포함
    uv run python scripts/build_edge_binary.py --version v1.0.0 --upload
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
EDGE_DIR = PROJECT_ROOT / "edge"
DIST_DIR = PROJECT_ROOT / "dist"

S3_BUCKET = os.getenv("EDGE_S3_BUCKET", "oreo-dev-ml-artifacts")
S3_PREFIX = os.getenv("EDGE_S3_PREFIX", "apps/edge/")


def get_platform_key() -> str:
    """현재 OS/arch → platform key."""
    os_name = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        arch = machine
    return f"{os_name}-{arch}"


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_binary(version: str, platform_key: str | None = None) -> Path:
    """PyInstaller로 엣지 서버 바이너리 빌드."""
    if platform_key is None:
        platform_key = get_platform_key()

    binary_name = f"edge-server-{version}-{platform_key}"
    if platform.system() == "Windows":
        binary_name += ".exe"

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DIST_DIR / binary_name

    logger.info("Building %s ...", binary_name)

    # PyInstaller 실행
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", binary_name,
        "--distpath", str(DIST_DIR),
        "--workpath", str(DIST_DIR / "build"),
        "--specpath", str(DIST_DIR / "build"),
        "--hidden-import", "llama_cpp",
        "--hidden-import", "httpx",
        "--hidden-import", "uvicorn",
        "--hidden-import", "fastapi",
        str(EDGE_DIR / "server.py"),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error("PyInstaller failed: %s\n%s", e.stdout, e.stderr)
        raise

    if not output_path.exists():
        # PyInstaller가 다른 이름으로 생성했을 수 있음
        candidates = list(DIST_DIR.glob(f"edge-server-{version}*"))
        if candidates:
            output_path = candidates[0]
        else:
            raise FileNotFoundError(f"Build output not found: {output_path}")

    size_mb = output_path.stat().st_size / 1024 / 1024
    sha256 = compute_sha256(output_path)
    logger.info("Built: %s (%.1f MB, SHA256: %s)", output_path.name, size_mb, sha256[:16])

    # 메타데이터 저장
    meta = {
        "version": version,
        "platform": platform_key,
        "filename": output_path.name,
        "size_mb": round(size_mb, 1),
        "sha256": sha256,
    }
    meta_path = output_path.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2))

    return output_path


def upload_to_s3(binary_path: Path, version: str) -> dict:
    """바이너리를 S3에 업로드하고 pre-signed URL 생성."""
    import boto3

    s3 = boto3.client("s3")
    platform_key = get_platform_key()

    s3_key = f"{S3_PREFIX}{version}/{binary_path.name}"
    logger.info("Uploading %s → s3://%s/%s", binary_path.name, S3_BUCKET, s3_key)

    s3.upload_file(str(binary_path), S3_BUCKET, s3_key)

    # Pre-signed URL (24시간)
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": s3_key},
        ExpiresIn=86400,
    )

    sha256 = compute_sha256(binary_path)

    return {
        "platform": platform_key,
        "url": url,
        "sha256": sha256,
        "s3_uri": f"s3://{S3_BUCKET}/{s3_key}",
        "size_mb": round(binary_path.stat().st_size / 1024 / 1024, 1),
    }


def update_manifest(version: str, app_downloads: dict) -> None:
    """기존 manifest.json에 app_version + app_downloads 추가.

    DB에서 프로필 목록을 API로 가져옴 (YAML 사용하지 않음).
    """
    import boto3

    import httpx

    # DB에서 프로필 가져오기 (API 경유)
    api_url = os.getenv("API_URL", "http://localhost:8000")
    try:
        resp = httpx.get(f"{api_url}/api/v1/distill/profiles", timeout=10)
        profiles = resp.json().get("profiles", {})
    except Exception as e:
        logger.error("Failed to fetch profiles from API: %s", e)
        logger.info("Falling back to S3 bucket/prefix from env vars")
        profiles = {"default": {"config": json.dumps({
            "deploy": {"s3_bucket": S3_BUCKET, "s3_prefix": S3_PREFIX.replace("apps/edge/", "models/edge/")},
        })}}

    s3 = boto3.client("s3")

    for name, profile in profiles.items():
        config = profile.get("config", "{}")
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (ValueError, TypeError):
                config = {}
        deploy = config.get("deploy", {})
        bucket = deploy.get("s3_bucket", S3_BUCKET)
        prefix = deploy.get("s3_prefix", "models/edge/")

        manifest_key = f"{prefix}manifest.json"
        try:
            resp = s3.get_object(Bucket=bucket, Key=manifest_key)
            manifest = json.loads(resp["Body"].read())
        except Exception:
            manifest = {}

        manifest["app_version"] = version
        manifest["app_downloads"] = app_downloads

        s3.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, ensure_ascii=False, indent=2),
            ContentType="application/json",
        )
        logger.info("Updated manifest: s3://%s/%s", bucket, manifest_key)


def main():
    parser = argparse.ArgumentParser(description="Build edge server binary")
    parser.add_argument("--version", required=True, help="Version tag (e.g. v1.0.0)")
    parser.add_argument("--os", help="Target OS (linux/windows/darwin)")
    parser.add_argument("--upload", action="store_true", help="Upload to S3")
    parser.add_argument("--update-manifest", action="store_true", help="Update S3 manifest")
    args = parser.parse_args()

    platform_key = args.os or None
    binary_path = build_binary(args.version, platform_key)

    if args.upload:
        result = upload_to_s3(binary_path, args.version)
        logger.info("Uploaded: %s", json.dumps(result, indent=2))

        if args.update_manifest:
            update_manifest(args.version, {result["platform"]: result})


if __name__ == "__main__":
    main()
