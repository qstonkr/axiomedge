#!/usr/bin/env bash
# backup_qdrant.sh — Qdrant 전체 collection snapshot 생성 + 다운로드
#
# Qdrant 의 snapshot API 를 호출해 각 collection 별 .snapshot 파일을 받아
# backups/qdrant/ 에 저장한다. 운영 중에도 동작 (online).
#
# Usage:
#   QDRANT_URL=http://localhost:6333 ./scripts/ops/backup_qdrant.sh
#   BACKUP_DIR=/var/backups/qdrant KEEP_COUNT=14 ./scripts/ops/backup_qdrant.sh
#
# 복구는 Qdrant snapshot recover API 또는 storage 디렉토리 교체 (downtime 필요).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups/qdrant}"
KEEP_COUNT="${KEEP_COUNT:-7}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

echo "[$(date -Iseconds)] Qdrant @ $QDRANT_URL — listing collections"

# Collection 목록 조회
COLLECTIONS=$(curl -fsS "$QDRANT_URL/collections" | python3 -c \
    "import json,sys; print('\n'.join(c['name'] for c in json.load(sys.stdin)['result']['collections']))")

if [ -z "$COLLECTIONS" ]; then
    echo "[$(date -Iseconds)] No collections found — nothing to back up"
    exit 0
fi

for COLL in $COLLECTIONS; do
    echo "[$(date -Iseconds)] [$COLL] creating snapshot…"
    SNAPSHOT_NAME=$(curl -fsS -X POST "$QDRANT_URL/collections/$COLL/snapshots" | python3 -c \
        "import json,sys; print(json.load(sys.stdin)['result']['name'])")
    OUT_FILE="$BACKUP_DIR/${COLL}_${TIMESTAMP}.snapshot"
    echo "[$(date -Iseconds)] [$COLL] downloading $SNAPSHOT_NAME → $OUT_FILE"
    curl -fsS "$QDRANT_URL/collections/$COLL/snapshots/$SNAPSHOT_NAME" -o "$OUT_FILE"
    SIZE=$(du -h "$OUT_FILE" | cut -f1)
    echo "[$(date -Iseconds)] [$COLL] saved ($SIZE)"
    # Snapshot 은 Qdrant 디스크에도 남으므로 서버에서 삭제해 디스크 사용량 관리
    curl -fsS -X DELETE "$QDRANT_URL/collections/$COLL/snapshots/$SNAPSHOT_NAME" >/dev/null
done

# Rotate: collection 별로 최신 KEEP_COUNT 만 유지
for COLL in $COLLECTIONS; do
    COUNT=$(find "$BACKUP_DIR" -name "${COLL}_*.snapshot" -type f | wc -l | tr -d ' ')
    if [ "$COUNT" -gt "$KEEP_COUNT" ]; then
        DELETE_COUNT=$((COUNT - KEEP_COUNT))
        echo "[$(date -Iseconds)] [$COLL] rotating — removing $DELETE_COUNT old snapshot(s)"
        find "$BACKUP_DIR" -name "${COLL}_*.snapshot" -type f | sort | head -n "$DELETE_COUNT" | xargs rm -f
    fi
done

echo "[$(date -Iseconds)] Qdrant backup complete — $BACKUP_DIR"
