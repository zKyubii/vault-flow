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

# Un backup vuoto è peggio di nessun backup: sembra fatto, e te ne accorgi
# solo il giorno in cui provi a ripristinarlo. Capita davvero — se Docker non
# sta girando, mysqldump non scrive niente e `| gzip` produce comunque un file
# valido di ~20 byte.
SIZE=$(wc -c < "$FILE")
if [ "$SIZE" -lt 1000 ]; then
    rm -f "$FILE"
    echo "ERRORE: il dump è risultato vuoto ($SIZE byte). File rimosso." >&2
    echo "Controlla che i container siano in esecuzione: docker compose ps" >&2
    exit 1
fi

# `grep -c` e non `grep -q`: il secondo si ferma al primo risultato, gunzip
# riceve SIGPIPE e con `pipefail` l'intera pipeline risulta fallita anche
# quando la tabella c'era — cancellando un backup buono.
# `|| true` perché grep esce con 1 quando non trova nulla, e `set -e`
# interromperebbe lo script prima del controllo vero.
TABLES=$(gunzip -c "$FILE" | grep -c "CREATE TABLE" || true)
if [ "${TABLES:-0}" -lt 1 ]; then
    rm -f "$FILE"
    echo "ERRORE: il dump non contiene nessuna tabella. File rimosso." >&2
    exit 1
fi
echo "Backup salvato: $FILE ($(du -h "$FILE" | cut -f1), $TABLES tabelle)"

# I backup vecchi si cancellano da soli, altrimenti in un anno riempiono il disco
find "$DEST" -name 'spese-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "Rimossi i backup più vecchi di $KEEP_DAYS giorni"

# Ripristino:
#   gunzip < backups/spese-2026-08-16.sql.gz | \
#     docker compose exec -T db mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"
