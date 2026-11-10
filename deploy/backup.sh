#!/usr/bin/env bash
# Daily backup of the Kasina database and uploaded files (videos, receipts).
# Installed to /etc/cron.daily/kasina-backup by gce-startup.sh. Keeps 14 days
# locally and, if BACKUP_BUCKET is set (gs://...), copies each backup to Cloud Storage.
set -euo pipefail
set -a; . /etc/kasina.env; set +a
DEST="${BACKUP_DIR:-/var/lib/kasina/backups}"
STAMP="$(date -u +%Y%m%d-%H%M)"
mkdir -p "$DEST"

case "${DATABASE_URL:-}" in
  sqlite:///*)
    DB_FILE="${DATABASE_URL#sqlite:///}"
    # Online-safe copy using SQLite's backup API.
    python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" \
      "$DB_FILE" "$DEST/db-$STAMP.sqlite"
    gzip "$DEST/db-$STAMP.sqlite" ;;
  postgres*)
    pg_dump "$(echo "$DATABASE_URL" | sed 's/+psycopg2//')" | gzip > "$DEST/db-$STAMP.sql.gz" ;;
esac

# Uploads: receipts and locally stored videos (VdoCipher videos live at VdoCipher).
tar -czf "$DEST/uploads-$STAMP.tar.gz" -C "$(dirname "$UPLOAD_DIR")" "$(basename "$UPLOAD_DIR")"

if [ -n "${BACKUP_BUCKET:-}" ]; then
  gcloud storage cp "$DEST"/*-"$STAMP".* "$BACKUP_BUCKET/" --quiet
fi
find "$DEST" -type f -mtime +14 -delete
chown -R kasina:kasina "$DEST"
chmod 700 "$DEST"
