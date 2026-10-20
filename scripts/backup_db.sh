#!/usr/bin/env bash
# Copia de seguridad de la base de Veyra con VERIFICACIÓN: se vuelca, se restaura en una base temporal y se comparan
# los conteos de todas las tablas. Si no coinciden, la copia se marca como inválida y el script falla.
#   scripts/backup_db.sh [carpeta_destino] [cuántas_conservar]      (por defecto: backups/ y las últimas 14)
set -euo pipefail
OUT="${1:-backups}"; KEEP="${2:-14}"; CT="${LABELHUB_DB_CONTAINER:-labelhub-db}"; DB="${LABELHUB_DB_NAME:-labelhub}"; U=labelhub
mkdir -p "$OUT"
F="$OUT/${DB}_$(date +%Y%m%d_%H%M%S).sql.gz"
docker exec "$CT" pg_dump -U "$U" -d "$DB" --no-owner | gzip > "$F"
[ -s "$F" ] || { echo "ERROR: el volcado salió vacío" >&2; rm -f "$F"; exit 1; }
TMP="${DB}_verify_$$"
trap 'docker exec "$CT" psql -q -U "$U" -d postgres -c "DROP DATABASE IF EXISTS $TMP WITH (FORCE)" >/dev/null 2>&1 || true' EXIT
docker exec "$CT" psql -q -U "$U" -d postgres -c "CREATE DATABASE $TMP" >/dev/null
gunzip -c "$F" | docker exec -i "$CT" psql -q -v ON_ERROR_STOP=1 -U "$U" -d "$TMP" >/dev/null
counts() {
  docker exec "$CT" psql -At -U "$U" -d "$1" -c \
    "select string_agg(tablename||'='||(xpath('/row/c/text()', query_to_xml('select count(*) as c from '||quote_ident(tablename), false, true, '')))[1]::text, ',' order by tablename) from pg_tables where schemaname='public'"
}
A="$(counts "$DB")"; B="$(counts "$TMP")"
if [ "$A" != "$B" ]; then echo "ERROR: la restauración no coincide con la base" >&2; echo " base:      $A" >&2; echo " restaurada: $B" >&2; mv "$F" "$F.INVALIDA"; exit 1; fi
echo "OK $F ($(du -h "$F" | cut -f1)) · restauración verificada: $(echo "$A" | tr ',' '\n' | grep -v '=0$' | tr '\n' ' ')"
ls -1t "$OUT"/${DB}_*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
