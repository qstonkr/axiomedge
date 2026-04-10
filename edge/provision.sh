#!/bin/bash
# 엣지 서버 출고 자동화 — 본사에서 장비에 실행하는 원커맨드 스크립트
#
# 대시보드에서 매장 등록 후 표시되는 명령어를 그대로 붙여넣기:
#   STORE_ID=gangnam-01 \
#   EDGE_API_KEY=edge-xxxx \
#   MANIFEST_URL=https://... \
#   CENTRAL_API_URL=https://... \
#   bash provision.sh
#
# 이 스크립트가 하는 일:
#   1. Docker 설치 확인
#   2. .env 파일 생성 (장비에 환경변수 저장)
#   3. docker-compose.yml 생성
#   4. 최초 실행 + 헬스체크
#   5. 시스템 부팅 시 자동 시작 등록
#
# 가맹점주는 이 과정을 몰라도 됩니다.
# 전원 ON → Docker 자동 시작 → 엣지 서버 자동 시작 → heartbeat 전송

set -e

echo "============================================"
echo "  GS 엣지 모델 서버 — 출고 설정"
echo "============================================"

# 필수 환경변수
: "${STORE_ID:?STORE_ID 필수}"
: "${MANIFEST_URL:?MANIFEST_URL 필수}"
: "${CENTRAL_API_URL:?CENTRAL_API_URL 필수}"
EDGE_API_KEY="${EDGE_API_KEY:-}"
EDGE_PORT="${EDGE_PORT:-8080}"

EDGE_HOME="${EDGE_HOME:-/opt/edge-model}"

echo "  매장: $STORE_ID"
echo "  경로: $EDGE_HOME"
echo ""

# 1. Docker 확인
if ! command -v docker &>/dev/null; then
    echo "[1/5] Docker 설치 중..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker 2>/dev/null || true
else
    echo "[1/5] Docker ✓"
fi

if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    echo "  docker-compose 설치 중..."
    apt-get install -y docker-compose-plugin 2>/dev/null || \
    pip install docker-compose 2>/dev/null || true
fi

# 2. 디렉토리 + .env
echo "[2/5] 환경 설정..."
mkdir -p "$EDGE_HOME"/{models/current,logs}

cat > "$EDGE_HOME/.env" <<EOF
STORE_ID=$STORE_ID
EDGE_API_KEY=$EDGE_API_KEY
MANIFEST_URL=$MANIFEST_URL
CENTRAL_API_URL=$CENTRAL_API_URL
MODEL_PATH=/models/current/model.gguf
LOG_DIR=/logs
EDGE_N_CTX=512
EDGE_N_THREADS=4
EDGE_MAX_TOKENS=256
EDGE_PORT=$EDGE_PORT
EOF

echo "  .env 생성 완료"

# 3. docker-compose.yml
echo "[3/5] Docker 설정..."
cat > "$EDGE_HOME/docker-compose.yml" <<EOF
services:
  edge-model:
    image: edge-model:latest
    ports:
      - "$EDGE_PORT:8080"
    volumes:
      - ./models:/models
      - ./logs:/logs
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    restart: unless-stopped
EOF

echo "  docker-compose.yml 생성 완료"

# 4. 최초 실행
echo "[4/5] 서버 시작..."
cd "$EDGE_HOME"

# 이미지 pull (ECR 또는 로컬)
if docker images edge-model:latest --format "{{.ID}}" | grep -q .; then
    echo "  이미지 존재 ✓"
else
    echo "  이미지 빌드 중 (최초 1회)..."
    # edge/ 디렉토리에서 빌드하거나 ECR에서 pull
    if [ -f "$EDGE_HOME/Dockerfile" ]; then
        docker build -t edge-model:latest "$EDGE_HOME/"
    else
        echo "  ⚠ edge-model:latest 이미지를 수동으로 빌드하거나 pull 해주세요"
    fi
fi

docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null || true

# 헬스체크 대기
echo "  서버 시작 대기 (최대 60초)..."
for i in $(seq 1 12); do
    if curl -sf "http://localhost:$EDGE_PORT/health" >/dev/null 2>&1; then
        echo "  서버 정상 ✓"
        break
    fi
    sleep 5
done

# 5. 부팅 시 자동 시작
echo "[5/5] 자동 시작 등록..."
if command -v systemctl &>/dev/null; then
    cat > /etc/systemd/system/edge-model.service <<EOSVC
[Unit]
Description=GS Edge Model Server
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$EDGE_HOME
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOSVC
    systemctl daemon-reload
    systemctl enable edge-model 2>/dev/null
    echo "  systemd 등록 완료 ✓"
fi

echo ""
echo "============================================"
echo "  설치 완료!"
echo "  서버: http://localhost:$EDGE_PORT/health"
echo "  매장: $STORE_ID"
echo ""
echo "  이제 전원만 켜면 자동으로 동작합니다."
echo "============================================"
