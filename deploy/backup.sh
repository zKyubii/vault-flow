#!/usr/bin/env bash
# Database backup.
#
# An expense app without backups is an app that one day makes you start over:
# bank history beyond a certain date cannot be downloaded again, because banks
# cap the export window.
#
# Usage:
#   ./deploy/backup.sh                  -> ./backups/vaultflow-YYYY-MM-DD.sql.gz
#   ./deploy/backup.sh /some/directory
#
# To run it nightly at 3am, in `crontab -e`:
#   0 3 * * * cd /path/to/vault-flow && ./deploy/backup.sh >> /var/log/vault-flow-backup.log 2>&1

set -euo pipefail

cd "$(dirname "$0")/.."

DEST="${1:-./backups}"
KEEP_DAYS=30

# shellcheck disable=SC1091
set -a; source .env; set +a

mkdir -p "$DEST"
FILE="$DEST/vaultflow-$(date +%Y-%m-%d).sql.gz"

# --single-transaction: a consistent dump without blocking writes
docker compose exec -T db mysqldump \
    --single-transaction \
    --routines \
    -u root -p"$MYSQL_ROOT_PASSWORD" \
    "$MYSQL_DATABASE" | gzip > "$FILE"

# An empty backup is worse than no backup: it looks done, and you only find
# out the day you try to restore it. It really happens — if Docker is not
# running, mysqldump writes nothing and `| gzip` still produces a valid file
# of about 20 bytes.
SIZE=$(wc -c < "$FILE")
if [ "$SIZE" -lt 1000 ]; then
    rm -f "$FILE"
    echo "ERROR: the dump came out empty ($SIZE bytes). File removed." >&2
    echo "Check that the containers are running: docker compose ps" >&2
    exit 1
fi

# `grep -c`, not `grep -q`: the latter stops at the first match, gunzip gets
# SIGPIPE and with `pipefail` the whole pipeline counts as failed — deleting a
# perfectly good backup.
# `|| true` because grep exits 1 when it finds nothing, and `set -e` would
# abort before the real check below.
TABLES=$(gunzip -c "$FILE" | grep -c "CREATE TABLE" || true)
if [ "${TABLES:-0}" -lt 1 ]; then
    rm -f "$FILE"
    echo "ERROR: the dump contains no tables. File removed." >&2
    exit 1
fi

echo "Backup saved: $FILE ($(du -h "$FILE" | cut -f1), $TABLES tables)"

# Old backups delete themselves, otherwise they fill the disk within a year
find "$DEST" -name 'vaultflow-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "Removed backups older than $KEEP_DAYS days"

# Restore:
#   gunzip < backups/vaultflow-2026-08-16.sql.gz | \
#     docker compose exec -T db mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"
