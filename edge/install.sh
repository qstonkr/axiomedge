#!/bin/bash
# GS Edge Model Server 설치 스크립트 (Linux / macOS)
#
# Usage:
#   STORE_ID=gangnam-01 \
#   EDGE_API_KEY=your-key \
#   MANIFEST_URL=https://s3.../manifest.json \
#   CENTRAL_API_URL=https://knowledge-api.gs.internal \
#   bash install.sh

set -e

# 필수 환경변수 체크
: "${STORE_ID:?STORE_ID is required}"
: "${MANIFEST_URL:?MANIFEST_URL is required}"
: "${CENTRAL_API_URL:?CENTRAL_API_URL is required}"
EDGE_API_KEY="${EDGE_API_KEY:-}"

EDGE_HOME="/opt/edge-model"
EDGE_PORT="${EDGE_PORT:-8080}"

echo "[install] Installing GS Edge Model Server..."
echo "  Store: $STORE_ID"
echo "  Home:  $EDGE_HOME"

# 1. OS/arch 감지
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
    x86_64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
esac
PLATFORM="${OS}-${ARCH}"
echo "  Platform: $PLATFORM"

# 2. manifest에서 다운로드 URL 추출
MANIFEST=$(curl -sf "$MANIFEST_URL")
if [ -z "$MANIFEST" ]; then
    echo "[install] ERROR: Failed to fetch manifest"
    exit 1
fi

APP_URL=$(echo "$MANIFEST" | python3 -c "
import json, sys
m = json.load(sys.stdin)
dl = m.get('app_downloads', {}).get('$PLATFORM', {})
print(dl.get('url', ''))
" 2>/dev/null)

MODEL_URL=$(echo "$MANIFEST" | python3 -c "
import json, sys; print(json.load(sys.stdin).get('download_url', ''))
" 2>/dev/null)

if [ -z "$APP_URL" ]; then
    echo "[install] ERROR: No app download for platform $PLATFORM"
    exit 1
fi

# 3. 디렉토리 생성
mkdir -p "$EDGE_HOME"/{current-app,models/current,logs,staging}

# 4. 바이너리 다운로드
echo "[install] Downloading app binary..."
curl -fSL "$APP_URL" -o "$EDGE_HOME/current-app/edge-server"
chmod +x "$EDGE_HOME/current-app/edge-server"

# 5. 모델 다운로드
if [ -n "$MODEL_URL" ]; then
    echo "[install] Downloading model..."
    curl -fSL "$MODEL_URL" -o "$EDGE_HOME/models/current/model.gguf"
    echo "$MANIFEST" > "$EDGE_HOME/models/current/manifest.json"
fi

# 6. 환경변수 파일
cat > "$EDGE_HOME/.env" <<EOF
STORE_ID=$STORE_ID
EDGE_API_KEY=$EDGE_API_KEY
MANIFEST_URL=$MANIFEST_URL
CENTRAL_API_URL=$CENTRAL_API_URL
MODEL_PATH=$EDGE_HOME/models/current/model.gguf
LOG_DIR=$EDGE_HOME/logs
EDGE_SERVER_URL=http://localhost:$EDGE_PORT
APP_DIR=$EDGE_HOME
EDGE_HOME=$EDGE_HOME
EOF

# 7. 서비스 등록
if [ "$OS" = "linux" ] && command -v systemctl &>/dev/null; then
    echo "[install] Registering systemd services..."
    cat > /etc/systemd/system/edge-server.service <<EOSVC
[Unit]
Description=GS Edge Model Server
After=network.target

[Service]
Type=simple
EnvironmentFile=$EDGE_HOME/.env
ExecStart=$EDGE_HOME/current-app/edge-server
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOSVC

    cat > /etc/systemd/system/edge-sync.service <<EOSYNC
[Unit]
Description=GS Edge Sync

[Service]
Type=oneshot
EnvironmentFile=$EDGE_HOME/.env
ExecStart=$EDGE_HOME/current-app/edge-server --sync
ExecStartPost=$EDGE_HOME/update-edge.sh
EOSYNC

    cat > /etc/systemd/system/edge-sync.timer <<EOTIMER
[Unit]
Description=GS Edge Sync Timer

[Timer]
OnBootSec=60
OnUnitActiveSec=300

[Install]
WantedBy=timers.target
EOTIMER

    cp "$(dirname "$0")/update-edge.sh" "$EDGE_HOME/update-edge.sh" 2>/dev/null || true
    chmod +x "$EDGE_HOME/update-edge.sh" 2>/dev/null || true

    systemctl daemon-reload
    systemctl enable --now edge-server
    systemctl enable --now edge-sync.timer

elif [ "$OS" = "darwin" ]; then
    echo "[install] Registering launchd services..."
    echo "  Please manually create launchd plist files (see edge/service-templates/macos/)"
fi

echo "[install] Installation complete!"
echo "  Server: http://localhost:$EDGE_PORT/health"
echo "  Logs:   $EDGE_HOME/logs/"
