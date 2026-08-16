#!/usr/bin/env bash
# Backup del database.
#
# Un'app di spese senza backup è un'app che un giorno ti fa ricominciare da
# capo: lo storico bancario oltre una certa data non lo riscarichi più,
# perché le banche limitano la finestra di export.
#
# Uso:
#   ./deploy/backup.sh                      -> ./backups/spese-AAAA-MM-GG.sql.gz
#   ./deploy/backup.sh /percorso/cartella
#
# Per farlo ogni notte alle 3, in crontab -e:
#   0 3 * * * cd /percorso/dashboard-spese && ./deploy/backup.sh >> /var/log/spese-backup.log 2>&1

set -euo pipefail

cd "$(dirname "$0")/.."

DEST="${1:-./backups}"
KEEP_DAYS=30

# shellcheck disable=SC1091
set -a; source .env; set +a

mkdir -p "$DEST"
FILE="$DEST/spese-$(date +%Y-%m-%d).sql.gz"

# --single-transaction: dump coerente senza bloccare le scritture
docker compose exec -T db mysqldump \
    --single-transaction \
    --routines \
    -u root -p"$MYSQL_ROOT_PASSWORD" \
    "$MYSQL_DATABASE" | gzip > "$FILE"

echo "Backup salvato: $FILE ($(du -h "$FILE" | cut -f1))"

# I backup vecchi si cancellano da soli, altrimenti in un anno riempiono il disco
find "$DEST" -name 'spese-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "Rimossi i backup più vecchi di $KEEP_DAYS giorni"

# Ripristino:
#   gunzip < backups/spese-2026-08-16.sql.gz | \
#     docker compose exec -T db mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"
