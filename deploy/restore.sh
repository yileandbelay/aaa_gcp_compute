#!/usr/bin/env bash
# Restore the database and uploaded files from a backup made by backup.sh, on this server or a
# scratch one. The backup can be a local folder or an rclone remote (the off-server copy).
#
#   bash deploy/restore.sh --env /etc/academy.env --from /var/lib/academy/backups
#   bash deploy/restore.sh --env /etc/academy.env --from b2:my-bucket/academy --stamp 20261113-0300 --force
#
# Stop the app first (systemctl stop academy) and start it again afterwards. Without --force the
# script refuses to overwrite an existing database; with --force the old one is kept next to it as
# *.before-restore.
set -euo pipefail

ENV_FILE=""; FROM=""; STAMP=""; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    --stamp) STAMP="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n 2,13p "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -n "$ENV_FILE" ] && [ -n "$FROM" ] || { echo "Usage: restore.sh --env FILE --from DIR_OR_REMOTE [--stamp YYYYmmdd-HHMM] [--force]" >&2; exit 2; }
set -a; . "$ENV_FILE"; set +a

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
case "$FROM" in
  /*|./*|../*) SRC="$FROM" ;;                         # a local folder
  *:*) command -v rclone >/dev/null || { echo "rclone is needed to read $FROM" >&2; exit 1; }
       rclone copy "$FROM" "$WORK" ${STAMP:+--include "*-$STAMP.*"}; SRC="$WORK" ;;
  *) SRC="$FROM" ;;
esac

if [ -z "$STAMP" ]; then   # newest backup in the folder
  STAMP="$(ls "$SRC" | sed -n 's/^db-\([0-9]\{8\}-[0-9]\{4\}\)\..*/\1/p' | sort | tail -1)"
  [ -n "$STAMP" ] || { echo "No db-* backup found in $FROM" >&2; exit 1; }
fi
echo "Restoring backup $STAMP"

case "${DATABASE_URL:-}" in
  sqlite:///*)
    DB_FILE="${DATABASE_URL#sqlite:///}"
    [ -f "$SRC/db-$STAMP.sqlite.gz" ] || { echo "Missing $SRC/db-$STAMP.sqlite.gz" >&2; exit 1; }
    if [ -e "$DB_FILE" ]; then
      [ "$FORCE" = 1 ] || { echo "$DB_FILE already exists; run again with --force to replace it" >&2; exit 1; }
      cp -p "$DB_FILE" "$DB_FILE.before-restore"
    fi
    mkdir -p "$(dirname "$DB_FILE")"
    gunzip -c "$SRC/db-$STAMP.sqlite.gz" > "$DB_FILE.restoring" && mv "$DB_FILE.restoring" "$DB_FILE" ;;
  postgres*)
    [ -f "$SRC/db-$STAMP.sql.gz" ] || { echo "Missing $SRC/db-$STAMP.sql.gz" >&2; exit 1; }
    gunzip -c "$SRC/db-$STAMP.sql.gz" | psql "$(echo "$DATABASE_URL" | sed 's/+psycopg2//')" ;;
  *) echo "DATABASE_URL is not set in $ENV_FILE" >&2; exit 1 ;;
esac

if [ -f "$SRC/uploads-$STAMP.tar.gz" ]; then
  mkdir -p "$(dirname "$UPLOAD_DIR")"
  tar -xzf "$SRC/uploads-$STAMP.tar.gz" -C "$(dirname "$UPLOAD_DIR")"
else
  echo "No uploads archive for $STAMP; only the database was restored" >&2
fi
echo "Restore finished. Start the app again (systemctl start academy)."
