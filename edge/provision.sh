#!/bin/bash
# 엣지 서버 출고 자동화 — 본사에서 장비에 실행하는 원커맨드 스크립트
#
# 대시보드에서 매장 등록 후 표시되는 명령어를 그대로 붙여넣기하면 됩니다.
# 가맹점주는 이 과정을 몰라도 됩니다. 전원 ON → 자동 시작.

set -e

echo "============================================"
echo "  GS 엣지 모델 서버 — 출고 설정"
echo "============================================"

: "${STORE_ID:?STORE_ID 필수}"
: "${MANIFEST_URL:?MANIFEST_URL 필수}"
: "${CENTRAL_API_URL:?CENTRAL_API_URL 필수}"
EDGE_API_KEY="${EDGE_API_KEY:-}"
EDGE_PORT="${EDGE_PORT:-8080}"
EDGE_HOME="${EDGE_HOME:-$HOME/.edge-model}"

echo "  매장: $STORE_ID"
echo "  경로: $EDGE_HOME"
echo ""

# 1. Python 확인 (3.9+ 필요, 3.10+ 권장)
echo "[1/6] Python 확인..."
if ! command -v python3 &>/dev/null; then
    echo "  Python3 설치 중..."
    if command -v apt-get &>/dev/null; then
        apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv
    elif command -v yum &>/dev/null; then
        yum install -y python3 python3-pip
    elif command -v brew &>/dev/null; then
        brew install python3
    else
        echo "  ❌ Python3를 수동으로 설치해주세요"
        exit 1
    fi
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  Python: $(python3 --version) ✓"
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    :
else
    echo "  ❌ Python 3.9 이상 필요 (현재 $PY_VER)"
    exit 1
fi

# 2. 디렉토리 + venv
echo "[2/6] 환경 설정..."
mkdir -p "$EDGE_HOME"/{models/current,logs,staging}

if [ ! -d "$EDGE_HOME/venv" ]; then
    echo "  가상환경 생성..."
    python3 -m venv "$EDGE_HOME/venv"
fi

# 3. 패키지 설치
echo "[3/6] 패키지 설치..."
"$EDGE_HOME/venv/bin/pip" install -q --upgrade pip
# eval_type_backport: Python 3.9에서 `str | None` 같은 PEP 604 union syntax를
# FastAPI/Pydantic이 런타임 파싱할 수 있게 해주는 백포트. 3.10+에서도 무해.
"$EDGE_HOME/venv/bin/pip" install -q \
    "llama-cpp-python>=0.3.0" \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.34.0" \
    "httpx>=0.27.0" \
    "pydantic>=2.0" \
    "eval_type_backport>=0.2.0"
echo "  패키지 설치 완료 ✓"

# 4. 서버 코드 다운로드
echo "[4/6] 서버 코드 다운로드..."
curl -sfL "$CENTRAL_API_URL/api/v1/distill/edge-files/server.py" -o "$EDGE_HOME/server.py" || {
    echo "  ❌ server.py 다운로드 실패 ($CENTRAL_API_URL 접속 확인)"
    exit 1
}
curl -sfL "$CENTRAL_API_URL/api/v1/distill/edge-files/sync.py" -o "$EDGE_HOME/sync.py" || {
    echo "  ❌ sync.py 다운로드 실패"
    exit 1
}
# 최초 설치 시점의 앱 버전 기록 (중앙 API 의 manifest.json 의 app_version 과 비교)
REMOTE_APP_VER=$(curl -sfL "$MANIFEST_URL" 2>/dev/null \
    | python3 -c "import json, sys; print(json.load(sys.stdin).get('app_version', ''))" 2>/dev/null || echo "")
echo "${REMOTE_APP_VER:-initial}" > "$EDGE_HOME/.app_version"
echo "  서버 코드 ✓ (app_version=${REMOTE_APP_VER:-initial})"

# 5. 환경변수 파일
echo "[5/6] 설정 파일 생성..."
cat > "$EDGE_HOME/.env" <<EOF
STORE_ID=$STORE_ID
EDGE_API_KEY=$EDGE_API_KEY
MANIFEST_URL=$MANIFEST_URL
CENTRAL_API_URL=$CENTRAL_API_URL
MODEL_DIR=$EDGE_HOME/models
MODEL_PATH=$EDGE_HOME/models/current/model.gguf
LOG_DIR=$EDGE_HOME/logs
EDGE_N_CTX=512
EDGE_N_THREADS=4
EDGE_MAX_TOKENS=256
EDGE_SERVER_URL=http://localhost:$EDGE_PORT
EDGE_HOME=$EDGE_HOME
EOF
echo "  .env 생성 ✓"

# 6. 서비스 등록 + 시작
echo "[6/6] 서비스 등록..."
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

if [ "$OS" = "linux" ] && command -v systemctl &>/dev/null; then
    cat > /etc/systemd/system/edge-server.service <<EOSVC
[Unit]
Description=GS Edge Model Server
After=network.target

[Service]
Type=simple
EnvironmentFile=$EDGE_HOME/.env
ExecStart=$EDGE_HOME/venv/bin/uvicorn server:app --host 0.0.0.0 --port $EDGE_PORT
WorkingDirectory=$EDGE_HOME
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOSVC

    cat > /etc/systemd/system/edge-sync.timer <<EOTIMER
[Unit]
Description=GS Edge Model Sync

[Timer]
OnBootSec=60
OnUnitActiveSec=300

[Install]
WantedBy=timers.target
EOTIMER

    cat > /etc/systemd/system/edge-sync.service <<EOSYNC
[Unit]
Description=GS Edge Sync

[Service]
Type=oneshot
EnvironmentFile=$EDGE_HOME/.env
ExecStart=$EDGE_HOME/venv/bin/python3 $EDGE_HOME/sync.py
WorkingDirectory=$EDGE_HOME
EOSYNC

    systemctl daemon-reload
    systemctl enable --now edge-server 2>/dev/null || true
    systemctl enable --now edge-sync.timer 2>/dev/null || true
    echo "  systemd 서비스 등록 ✓"

elif [ "$OS" = "darwin" ]; then
    echo "  macOS: 백그라운드 서비스 등록..."

    # LaunchAgent plist 생성 (로그인 시 자동 시작)
    PLIST_DIR="$HOME/Library/LaunchAgents"
    mkdir -p "$PLIST_DIR"
    cat > "$PLIST_DIR/com.gs.edge-server.plist" <<EOPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.gs.edge-server</string>
    <key>ProgramArguments</key>
    <array>
        <string>$EDGE_HOME/venv/bin/uvicorn</string>
        <string>server:app</string>
        <string>--host</string><string>0.0.0.0</string>
        <string>--port</string><string>$EDGE_PORT</string>
    </array>
    <key>WorkingDirectory</key><string>$EDGE_HOME</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>STORE_ID</key><string>$STORE_ID</string>
        <key>EDGE_API_KEY</key><string>$EDGE_API_KEY</string>
        <key>MODEL_DIR</key><string>$EDGE_HOME/models</string>
        <key>MODEL_PATH</key><string>$EDGE_HOME/models/current/model.gguf</string>
        <key>LOG_DIR</key><string>$EDGE_HOME/logs</string>
        <key>CENTRAL_API_URL</key><string>$CENTRAL_API_URL</string>
        <key>MANIFEST_URL</key><string>$MANIFEST_URL</string>
        <key>EDGE_SERVER_URL</key><string>http://localhost:$EDGE_PORT</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$EDGE_HOME/logs/server.log</string>
    <key>StandardErrorPath</key><string>$EDGE_HOME/logs/server.err</string>
</dict>
</plist>
EOPLIST

    launchctl unload "$PLIST_DIR/com.gs.edge-server.plist" 2>/dev/null || true
    launchctl load "$PLIST_DIR/com.gs.edge-server.plist"
    echo "  edge-server LaunchAgent 등록 + 시작 ✓"

    # Sync LaunchAgent — 5분마다 sync.py 실행 (모델/앱 업데이트 자동 반영)
    cat > "$PLIST_DIR/com.gs.edge-sync.plist" <<EOSYNCPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.gs.edge-sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>$EDGE_HOME/venv/bin/python3</string>
        <string>$EDGE_HOME/sync.py</string>
    </array>
    <key>WorkingDirectory</key><string>$EDGE_HOME</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>STORE_ID</key><string>$STORE_ID</string>
        <key>EDGE_API_KEY</key><string>$EDGE_API_KEY</string>
        <key>MODEL_DIR</key><string>$EDGE_HOME/models</string>
        <key>MODEL_PATH</key><string>$EDGE_HOME/models/current/model.gguf</string>
        <key>LOG_DIR</key><string>$EDGE_HOME/logs</string>
        <key>CENTRAL_API_URL</key><string>$CENTRAL_API_URL</string>
        <key>MANIFEST_URL</key><string>$MANIFEST_URL</string>
        <key>EDGE_SERVER_URL</key><string>http://localhost:$EDGE_PORT</string>
        <key>APP_DIR</key><string>$EDGE_HOME</string>
    </dict>
    <key>StartInterval</key><integer>300</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$EDGE_HOME/logs/sync.log</string>
    <key>StandardErrorPath</key><string>$EDGE_HOME/logs/sync.err</string>
</dict>
</plist>
EOSYNCPLIST

    launchctl unload "$PLIST_DIR/com.gs.edge-sync.plist" 2>/dev/null || true
    launchctl load "$PLIST_DIR/com.gs.edge-sync.plist"
    echo "  edge-sync LaunchAgent 등록 + 시작 ✓ (5분 주기)"
fi

# 모델 초기 다운로드 시도
echo ""
echo "  모델 다운로드 시도 중..."
if ( set -a && . "$EDGE_HOME/.env" && set +a && \
     "$EDGE_HOME/venv/bin/python3" "$EDGE_HOME/sync.py" --check-only ); then
    echo "  모델 다운로드 ✓"
else
    echo "  ⚠ 모델 다운로드 실패 — 로그 확인 또는 배포 상태 확인 필요"
fi

# 헬스체크
sleep 3
if curl -sf "http://localhost:$EDGE_PORT/health" >/dev/null 2>&1; then
    echo "  서버 정상 ✓"
else
    echo "  ⚠ 모델 다운로드 후 자동 시작됩니다"
fi

echo ""
echo "============================================"
echo "  설치 완료!"
echo "  매장: $STORE_ID"
echo "  서버: http://localhost:$EDGE_PORT/health"
echo ""
echo "  전원만 켜면 자동으로 동작합니다."
echo "============================================"
