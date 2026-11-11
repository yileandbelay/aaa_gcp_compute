#!/usr/bin/env bash
# Daily backup of an academy's database and uploaded files (videos, receipts,
# materials). install.sh copies this to /etc/cron.daily/<name>-backup.
# Keeps 14 days locally and, if BACKUP_BUCKET is set (gs://... or s3://...),
# also copies each backup there with gcloud or aws.
set -euo pipefail
ENV_FILE="${1:-__ENV_FILE__}"
set -a; . "$ENV_FILE"; set +a
DEST="${BACKUP_DIR:?BACKUP_DIR is not set in $ENV_FILE}"
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

tar -czf "$DEST/uploads-$STAMP.tar.gz" -C "$(dirname "$UPLOAD_DIR")" "$(basename "$UPLOAD_DIR")"

case "${BACKUP_BUCKET:-}" in
  gs://*) gcloud storage cp "$DEST"/*-"$STAMP".* "$BACKUP_BUCKET/" --quiet ;;
  s3://*) aws s3 cp "$DEST/" "$BACKUP_BUCKET/" --recursive --exclude "*" --include "*-$STAMP.*" ;;
esac
find "$DEST" -type f -mtime +14 -delete
