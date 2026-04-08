#!/bin/bash
# 앱 바이너리 교체 스크립트 (Linux/macOS)
# sync.py가 staging에 다운로드한 바이너리를 교체.
# cron/systemd timer로 주기적 실행 또는 수동 실행.

set -e

EDGE_HOME="${EDGE_HOME:-/opt/edge-model}"
STAGING="$EDGE_HOME/staging"
CURRENT="$EDGE_HOME/current-app"
ROLLBACK="$EDGE_HOME/rollback-app"
HEALTH_URL="http://localhost:8080/health"

if [ ! -f "$STAGING/UPDATE_READY" ]; then
    exit 0
fi

NEW_VERSION=$(cat "$STAGING/UPDATE_READY")
echo "[update-edge] Updating app to $NEW_VERSION..."

# 1. 서비스 중지
if command -v systemctl &>/dev/null; then
    systemctl stop edge-server 2>/dev/null || true
elif command -v launchctl &>/dev/null; then
    launchctl unload /Library/LaunchDaemons/com.gs.edge-server.plist 2>/dev/null || true
fi

# 2. rollback 보존
[ -d "$ROLLBACK" ] && rm -rf "$ROLLBACK"
[ -d "$CURRENT" ] && cp -r "$CURRENT" "$ROLLBACK"
mkdir -p "$CURRENT"

# 3. staging → current
cp "$STAGING/edge-server" "$CURRENT/edge-server"
chmod +x "$CURRENT/edge-server"
rm -f "$STAGING/UPDATE_READY"

# 4. 서비스 시작
if command -v systemctl &>/dev/null; then
    systemctl start edge-server
elif command -v launchctl &>/dev/null; then
    launchctl load /Library/LaunchDaemons/com.gs.edge-server.plist
fi

# 5. 헬스체크 (5초 대기)
sleep 5
if ! curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    echo "[update-edge] Health check failed, rolling back..."
    cp "$ROLLBACK/edge-server" "$CURRENT/edge-server"
    if command -v systemctl &>/dev/null; then
        systemctl restart edge-server
    elif command -v launchctl &>/dev/null; then
        launchctl kickstart -k system/com.gs.edge-server
    fi
    echo "[update-edge] Rolled back"
    exit 1
fi

echo "[update-edge] Updated successfully to $NEW_VERSION"
