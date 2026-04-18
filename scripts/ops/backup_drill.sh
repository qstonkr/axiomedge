#!/usr/bin/env bash
# backup_drill.sh — End-to-end backup/restore validation drill.
#
# 더미 데이터 삽입 → 백업 → wipe → 복구 → row count 검증.
# CI nightly 또는 운영자 수동 실행 (분기 1회 권장).
#
# Stores: PostgreSQL + Qdrant (Neo4j 는 offline dump 복잡도 따로)
# 안전: 명시적 DRILL_CONFIRM=yes 없이는 wipe 거부.
#
# Usage:
#   DRILL_CONFIRM=yes ./scripts/ops/backup_drill.sh
#   DRILL_CONFIRM=yes QDRANT_URL=http://localhost:6333 ./scripts/ops/backup_drill.sh
#
# Production safety: prod DB URL 감지하면 즉시 abort.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
DRILL_TABLE="${DRILL_TABLE:-_backup_drill}"
DRILL_COLLECTION="${DRILL_COLLECTION:-_backup_drill}"
DRILL_ROWS="${DRILL_ROWS:-100}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

# === Safety guard ===
if [ "${DRILL_CONFIRM:-}" != "yes" ]; then
    echo "ERROR: backup_drill wipes data. Run with DRILL_CONFIRM=yes to proceed." >&2
    exit 2
fi

# Reject obvious prod URLs (heuristic — extend per environment)
if [ -n "${DATABASE_URL:-}" ]; then
    case "$DATABASE_URL" in
        *prod*|*production*|*\.com*|*\.io*)
            echo "ERROR: DATABASE_URL appears to be production. Aborting drill." >&2
            exit 3
            ;;
    esac
fi

log() { echo "[$(date -Iseconds)] [drill] $*"; }
fail() { log "FAIL: $*"; exit 1; }

# === PostgreSQL drill ===
drill_postgres() {
    log "=== PostgreSQL drill start ==="

    # Parse DATABASE_URL (or env vars)
    local pg_args=""
    if [ -n "${DATABASE_URL:-}" ]; then
        local uri="${DATABASE_URL#*://}"
        local userinfo="${uri%%@*}"
        export PGUSER="${userinfo%%:*}"
        export PGPASSWORD="${userinfo#*:}"
        local hostpart="${uri#*@}"
        local hostport="${hostpart%%/*}"
        export PGDATABASE="${hostpart#*/}"
        export PGHOST="${hostport%%:*}"
        export PGPORT="${hostport#*:}"
    fi
    PGUSER="${PGUSER:-knowledge}"
    PGDATABASE="${PGDATABASE:-knowledge_db}"
    PGHOST="${PGHOST:-localhost}"
    PGPORT="${PGPORT:-5432}"
    export PGUSER PGDATABASE PGHOST PGPORT PGPASSWORD

    log "Target: $PGUSER@$PGHOST:$PGPORT/$PGDATABASE"

    # Step 1: seed dummy data
    log "Step 1/5: seed $DRILL_ROWS rows into $DRILL_TABLE"
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 <<SQL
    DROP TABLE IF EXISTS $DRILL_TABLE;
    CREATE TABLE $DRILL_TABLE (id INT PRIMARY KEY, payload TEXT);
    INSERT INTO $DRILL_TABLE SELECT g, 'drill-' || g FROM generate_series(1, $DRILL_ROWS) g;
SQL

    local pre_count
    pre_count=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -tA -c "SELECT count(*) FROM $DRILL_TABLE")
    [ "$pre_count" = "$DRILL_ROWS" ] || fail "seed failed: expected $DRILL_ROWS rows, got $pre_count"
    log "  pre-backup count: $pre_count ✓"

    # Step 2: backup
    log "Step 2/5: backup via scripts/ops/backup_db.sh"
    "$SCRIPT_DIR/backup_db.sh" >/dev/null
    local backup_file
    backup_file=$(find "$BACKUP_DIR" -name "knowledge_db_*.sql.gz" -type f | sort | tail -1)
    [ -f "$backup_file" ] || fail "backup file not found"
    log "  backup: $backup_file ($(du -h "$backup_file" | cut -f1))"

    # Step 3: wipe
    log "Step 3/5: drop drill table"
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "DROP TABLE IF EXISTS $DRILL_TABLE" >/dev/null

    # Step 4: restore (only the drill table — full restore is too invasive for drill)
    log "Step 4/5: restore from $backup_file"
    gunzip -c "$backup_file" | grep -E "^(CREATE TABLE|INSERT INTO|COPY|SELECT pg_catalog\.setval).*${DRILL_TABLE}|^${DRILL_ROWS}\." \
        | psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=0 >/dev/null 2>&1 || true
    # Fallback: import via psql in a way that handles COPY
    if ! psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -tA -c "SELECT 1 FROM information_schema.tables WHERE table_name = '$DRILL_TABLE'" | grep -q 1; then
        log "  partial restore failed, performing full restore"
        gunzip -c "$backup_file" | psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=0 >/dev/null 2>&1 || true
    fi

    # Step 5: verify
    log "Step 5/5: verify row count"
    local post_count
    post_count=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -tA -c "SELECT count(*) FROM $DRILL_TABLE" 2>/dev/null || echo "0")
    [ "$post_count" = "$DRILL_ROWS" ] || fail "restore verification failed: expected $DRILL_ROWS, got $post_count"
    log "  post-restore count: $post_count ✓"

    # Cleanup
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "DROP TABLE IF EXISTS $DRILL_TABLE" >/dev/null
    log "PostgreSQL drill PASSED"
}

# === Qdrant drill ===
drill_qdrant() {
    log "=== Qdrant drill start ==="
    log "Target: $QDRANT_URL"

    # Step 1: create test collection + seed
    log "Step 1/5: create $DRILL_COLLECTION + seed $DRILL_ROWS points"
    curl -fsS -X PUT "$QDRANT_URL/collections/$DRILL_COLLECTION" \
        -H "Content-Type: application/json" \
        -d '{"vectors": {"size": 4, "distance": "Cosine"}}' >/dev/null

    local points_json
    points_json=$(python3 -c "
import json
points = [{'id': i, 'vector': [0.1, 0.2, 0.3, 0.4], 'payload': {'idx': i}} for i in range(1, $DRILL_ROWS + 1)]
print(json.dumps({'points': points}))
")
    curl -fsS -X PUT "$QDRANT_URL/collections/$DRILL_COLLECTION/points?wait=true" \
        -H "Content-Type: application/json" \
        -d "$points_json" >/dev/null

    local pre_count
    pre_count=$(curl -fsS "$QDRANT_URL/collections/$DRILL_COLLECTION" | python3 -c "import json, sys; print(json.load(sys.stdin)['result']['points_count'])")
    [ "$pre_count" = "$DRILL_ROWS" ] || fail "Qdrant seed failed: expected $DRILL_ROWS, got $pre_count"
    log "  pre-backup count: $pre_count ✓"

    # Step 2: snapshot
    log "Step 2/5: snapshot via scripts/ops/backup_qdrant.sh"
    BACKUP_DIR="$BACKUP_DIR/qdrant" "$SCRIPT_DIR/backup_qdrant.sh" >/dev/null
    local snap_file
    snap_file=$(find "$BACKUP_DIR/qdrant" -name "${DRILL_COLLECTION}_*.snapshot" -type f | sort | tail -1)
    [ -f "$snap_file" ] || fail "snapshot file not found"
    log "  snapshot: $snap_file ($(du -h "$snap_file" | cut -f1))"

    # Step 3: wipe
    log "Step 3/5: delete collection"
    curl -fsS -X DELETE "$QDRANT_URL/collections/$DRILL_COLLECTION" >/dev/null

    # Step 4: restore via snapshot recover API (URL-based — Qdrant fetches from local file URL)
    log "Step 4/5: recover from snapshot"
    # Recreate collection then upload snapshot
    curl -fsS -X PUT "$QDRANT_URL/collections/$DRILL_COLLECTION" \
        -H "Content-Type: application/json" \
        -d '{"vectors": {"size": 4, "distance": "Cosine"}}' >/dev/null
    curl -fsS -X POST "$QDRANT_URL/collections/$DRILL_COLLECTION/snapshots/upload?priority=snapshot&wait=true" \
        -F "snapshot=@$snap_file" >/dev/null

    # Step 5: verify
    log "Step 5/5: verify point count"
    local post_count
    post_count=$(curl -fsS "$QDRANT_URL/collections/$DRILL_COLLECTION" | python3 -c "import json, sys; print(json.load(sys.stdin)['result']['points_count'])")
    [ "$post_count" = "$DRILL_ROWS" ] || fail "Qdrant restore verification failed: expected $DRILL_ROWS, got $post_count"
    log "  post-restore count: $post_count ✓"

    # Cleanup
    curl -fsS -X DELETE "$QDRANT_URL/collections/$DRILL_COLLECTION" >/dev/null
    log "Qdrant drill PASSED"
}

# === Run all ===
log "Backup recovery drill — starting (timestamp=$TIMESTAMP)"
drill_postgres
drill_qdrant
log "✅ All backup drills PASSED"
