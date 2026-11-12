#!/usr/bin/env bash
# Daily backup of an academy's database and uploaded files (videos, receipts,
# materials). install.sh copies this to /etc/cron.daily/<name>-backup.
# Keeps 14 days locally. Off-server copies (so one failed server does not lose everything):
#   RCLONE_REMOTE=remote:bucket/path  copies each backup there with rclone (any object storage:
#                                     Backblaze B2, S3, Wasabi, Google Drive...) and deletes copies
#                                     older than OFFSITE_RETENTION_DAYS (default 30).
#   BACKUP_BUCKET=gs://... or s3://...  the older gcloud / aws way.
# Set up rclone once with `rclone config`. Restore with deploy/restore.sh.
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
OFFSITE_FAILED=0
if [ -n "${RCLONE_REMOTE:-}" ]; then
  if command -v rclone >/dev/null 2>&1; then
    if rclone copy "$DEST" "$RCLONE_REMOTE" --include "*-$STAMP.*" --transfers 2 \
       && rclone delete "$RCLONE_REMOTE" --min-age "${OFFSITE_RETENTION_DAYS:-30}d"; then
      echo "Off-server backup copied to $RCLONE_REMOTE"
    else
      echo "!! Off-server backup to $RCLONE_REMOTE failed" >&2; OFFSITE_FAILED=1
    fi
  else
    echo "!! RCLONE_REMOTE is set but rclone is not installed (apt-get install rclone)" >&2; OFFSITE_FAILED=1
  fi
fi
find "$DEST" -type f -mtime +14 -delete
# A non-zero exit makes cron email the failure instead of hiding it.
exit "$OFFSITE_FAILED"
