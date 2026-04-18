#!/usr/bin/env bash
# backup_neo4j.sh — Neo4j 데이터베이스 dump (docker exec neo4j-admin)
#
# Community edition 은 online backup 미지원 — neo4j-admin dump 는 DB 가 stop 상태여야 함.
# 운영 중 사용을 원하면 APOC 의 apoc.export.cypher.all 사용 (별도 스크립트).
#
# 이 스크립트는 docker exec 로 neo4j-admin dump 를 호출 → host 로 cp 한다.
# 컨테이너가 다른 환경(K8s 등)이면 해당 환경에 맞게 수정 필요.
#
# Usage:
#   NEO4J_CONTAINER=knowledge-local-neo4j-1 ./scripts/ops/backup_neo4j.sh
#   NEO4J_DB=neo4j BACKUP_DIR=/var/backups/neo4j KEEP_COUNT=14 ./scripts/ops/backup_neo4j.sh
#
# 복구: docker exec <container> neo4j-admin database load <db> --from-path=/backups --overwrite-destination

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-knowledge-local-neo4j-1}"
NEO4J_DB="${NEO4J_DB:-neo4j}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups/neo4j}"
KEEP_COUNT="${KEEP_COUNT:-7}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

if ! docker ps --format '{{.Names}}' | grep -q "^${NEO4J_CONTAINER}$"; then
    echo "[$(date -Iseconds)] ERROR: container '$NEO4J_CONTAINER' not running" >&2
    echo "  Set NEO4J_CONTAINER env or check 'docker ps'" >&2
    exit 1
fi

echo "[$(date -Iseconds)] Neo4j dump — container=$NEO4J_CONTAINER db=$NEO4J_DB"

# Stop DB inside container (community edition requires offline dump)
echo "[$(date -Iseconds)] Stopping database $NEO4J_DB"
docker exec "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "${NEO4J_PASSWORD:-neo4j}" \
    "STOP DATABASE \`$NEO4J_DB\`" >/dev/null 2>&1 || true

# Run dump (output goes to /backups inside container, mounted to host or copied out)
DUMP_NAME="${NEO4J_DB}_${TIMESTAMP}.dump"
docker exec "$NEO4J_CONTAINER" neo4j-admin database dump "$NEO4J_DB" \
    --to-path=/tmp --overwrite-destination=true

# Copy out + rename with timestamp
docker cp "$NEO4J_CONTAINER:/tmp/${NEO4J_DB}.dump" "$BACKUP_DIR/$DUMP_NAME"
docker exec "$NEO4J_CONTAINER" rm -f "/tmp/${NEO4J_DB}.dump"

# Restart DB
echo "[$(date -Iseconds)] Starting database $NEO4J_DB"
docker exec "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "${NEO4J_PASSWORD:-neo4j}" \
    "START DATABASE \`$NEO4J_DB\`" >/dev/null 2>&1 || true

SIZE=$(du -h "$BACKUP_DIR/$DUMP_NAME" | cut -f1)
echo "[$(date -Iseconds)] Backup saved: $BACKUP_DIR/$DUMP_NAME ($SIZE)"

# Rotate
COUNT=$(find "$BACKUP_DIR" -name "${NEO4J_DB}_*.dump" -type f | wc -l | tr -d ' ')
if [ "$COUNT" -gt "$KEEP_COUNT" ]; then
    DELETE_COUNT=$((COUNT - KEEP_COUNT))
    echo "[$(date -Iseconds)] Rotating — removing $DELETE_COUNT old dump(s)"
    find "$BACKUP_DIR" -name "${NEO4J_DB}_*.dump" -type f | sort | head -n "$DELETE_COUNT" | xargs rm -f
fi

echo "[$(date -Iseconds)] Neo4j backup complete"
