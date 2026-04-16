#!/usr/bin/env bash
# backup_db.sh - Backup knowledge_db PostgreSQL database
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/knowledge_db ./scripts/backup_db.sh
#   PGHOST=localhost PGPORT=5432 PGUSER=user PGPASSWORD=pass PGDATABASE=knowledge_db ./scripts/backup_db.sh
#
# Backups are saved to backups/ with timestamp filenames. Keeps last 7 backups.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
KEEP_COUNT="${KEEP_COUNT:-7}"

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/knowledge_db_${TIMESTAMP}.sql.gz"

# Parse DATABASE_URL if provided, otherwise fall back to PG* env vars
if [ -n "${DATABASE_URL:-}" ]; then
    # Parse: postgresql://user:password@host:port/dbname
    # Strip the scheme
    uri="${DATABASE_URL#*://}"

    # Extract user:password
    userinfo="${uri%%@*}"
    export PGUSER="${userinfo%%:*}"
    export PGPASSWORD="${userinfo#*:}"

    # Extract host:port/dbname
    hostpart="${uri#*@}"
    hostport="${hostpart%%/*}"
    export PGDATABASE="${hostpart#*/}"

    if [[ "$hostport" == *:* ]]; then
        export PGHOST="${hostport%%:*}"
        export PGPORT="${hostport#*:}"
    else
        export PGHOST="$hostport"
        export PGPORT="${PGPORT:-5432}"
    fi
fi

# Validate required vars
: "${PGHOST:?PGHOST or DATABASE_URL must be set}"
: "${PGDATABASE:?PGDATABASE or DATABASE_URL must be set}"
PGUSER="${PGUSER:-postgres}"
PGPORT="${PGPORT:-5432}"

echo "[$(date -Iseconds)] Starting backup of ${PGDATABASE}@${PGHOST}:${PGPORT}..."

pg_dump \
    -h "$PGHOST" \
    -p "$PGPORT" \
    -U "$PGUSER" \
    -d "$PGDATABASE" \
    --no-password \
    | gzip > "$BACKUP_FILE"

FILE_SIZE="$(du -h "$BACKUP_FILE" | cut -f1)"
echo "[$(date -Iseconds)] Backup complete: $BACKUP_FILE ($FILE_SIZE)"

# Rotate: keep only last N backups
BACKUP_COUNT="$(find "$BACKUP_DIR" -name 'knowledge_db_*.sql.gz' -type f | wc -l | tr -d ' ')"
if [ "$BACKUP_COUNT" -gt "$KEEP_COUNT" ]; then
    DELETE_COUNT=$((BACKUP_COUNT - KEEP_COUNT))
    echo "[$(date -Iseconds)] Rotating: removing $DELETE_COUNT old backup(s)"
    find "$BACKUP_DIR" -name 'knowledge_db_*.sql.gz' -type f \
        | sort \
        | head -n "$DELETE_COUNT" \
        | xargs rm -f
fi

echo "[$(date -Iseconds)] Done. $KEEP_COUNT most recent backups retained."
