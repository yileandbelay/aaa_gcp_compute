#!/usr/bin/env bash
# Move a running academy from SQLite to PostgreSQL on the same server, with a backup first, a verified copy, an
# automatic health check and a way back.
#
#   sudo bash deploy/migrate-to-postgres.sh --name academy --dry-run     # checks and row counts; changes nothing live
#   sudo bash deploy/migrate-to-postgres.sh --name academy               # the real move (the site is down for a short time)
#   sudo bash deploy/migrate-to-postgres.sh --name academy --rollback    # back to the SQLite file
#   ... --reset-postgres    # to try the move again after a rollback: empties the (now out-of-date) PostgreSQL copy first
#
# Run it after updating the code (sudo bash deploy/install.sh), so the code and the database schema are current.
# What it does, in order: install PostgreSQL and create an empty database; back up everything (site still running);
# stop the site; copy every table in one transaction and compare row counts and content of each table; switch the
# settings file to PostgreSQL; start the site and check it answers from PostgreSQL. If anything fails before the switch
# the site is started again on SQLite, untouched. If the health check fails after the switch, it rolls back by itself.
# The SQLite file is kept (renamed *.migrated-<date>) so you can roll back; delete it once you are happy.
set -euo pipefail

NAME=academy; DRY=0; ROLLBACK=0; RESET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --rollback) ROLLBACK=1; shift ;;
    --reset-postgres) RESET=1; shift ;;
    -h|--help) sed -n 2,18p "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }

ENV_FILE="/etc/$NAME.env"; APP_DIR="/opt/$NAME"; DATA_DIR="/var/lib/$NAME"; STATE="/etc/$NAME.install"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/postgres.sh
. "$HERE/postgres.sh"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found. Is the academy installed under the name '$NAME'?" >&2; exit 1; }
PORT=8000; if [ -f "$STATE" ]; then . "$STATE"; PORT="${SAVED_PORT:-8000}"; fi
BEFORE_ENV="$ENV_FILE.before-postgres"; MARKER="$DATA_DIR/migrated-from-sqlite.path"
STAMP="$(date -u +%Y%m%d-%H%M%S)"

current_url() { grep -m1 '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2-; }
as_app() { sudo -u "$NAME" bash -c "set -a; . '$ENV_FILE'; ${1:+$1; }cd '$APP_DIR' && $2"; }
flask_app() { as_app "$1" ".venv/bin/flask --app wsgi $2"; }
healthy() {  # the site answers and can read its database
  local i
  for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:$PORT/courses" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}
say() { echo "==> $*"; }

rollback() {
  [ -f "$BEFORE_ENV" ] && [ -f "$MARKER" ] || { echo "Nothing to roll back: no earlier settings file ($BEFORE_ENV) or migration marker." >&2; return 1; }
  local sqlite_file; sqlite_file="$(cat "$MARKER")"
  [ -f "$sqlite_file" ] || { echo "The kept SQLite file $sqlite_file is missing." >&2; return 1; }
  say "Rolling back to SQLite. Anything done on the site since the move exists only in PostgreSQL."
  local pgurl; pgurl="$(current_url)"
  case "$pgurl" in postgres*) ( umask 077; echo "$pgurl" > "$DATA_DIR/postgres-url.private" ); pg_dump "$(echo "$pgurl" | sed 's/+psycopg2//')" | gzip > "$DATA_DIR/backups/postgres-before-rollback-$STAMP.sql.gz" \
      && say "Saved the PostgreSQL content as $DATA_DIR/backups/postgres-before-rollback-$STAMP.sql.gz" ;; esac
  systemctl stop "$NAME" || true
  cp -p "$BEFORE_ENV" "$ENV_FILE"
  local original="${sqlite_file%.migrated-*}"
  [ -e "$original" ] && mv "$original" "$original.replaced-$STAMP"
  mv "$sqlite_file" "$original"
  rm -f "$MARKER"
  systemctl start "$NAME"
  healthy && say "Back on SQLite and answering." || { echo "The site does not answer after the rollback. See: journalctl -u $NAME" >&2; return 1; }
}

if [ "$ROLLBACK" = 1 ]; then rollback; exit $?; fi

# ---- preflight -----------------------------------------------------------------------------------------------------
URL="$(current_url)"
case "$URL" in
  sqlite:///*) SQLITE_FILE="${URL#sqlite:///}" ;;
  postgres*) echo "This academy already uses PostgreSQL. Nothing to do."; exit 0 ;;
  *) echo "DATABASE_URL is missing or unknown in $ENV_FILE." >&2; exit 1 ;;
esac
[ -f "$SQLITE_FILE" ] || { echo "The SQLite file $SQLITE_FILE does not exist." >&2; exit 1; }
[ -d "$APP_DIR/.venv" ] || { echo "$APP_DIR/.venv not found. Run deploy/install.sh first." >&2; exit 1; }

say "Installing PostgreSQL and preparing an empty database"
PG_CRED_FILE="$DATA_DIR/postgres-url.private"   # root-only; kept between a dry run and the real run
pg_install
pg_provision "$NAME"
# The new database is unused (the site still runs on SQLite), so leftovers of an earlier attempt may be cleared,
# but never if it somehow contains real data.
PSQL_URL="$(echo "$PG_URL" | sed 's/+psycopg2//')"
if [ "$(pg_table_count "$PG_URL")" != 0 ]; then
  rows="$(psql "$PSQL_URL" -tAc 'SELECT count(*) FROM "user"' 2>/dev/null || echo 0)"
  if [ "${rows:-0}" != 0 ] && [ "$RESET" != 1 ]; then
    echo "The PostgreSQL database already holds data ($rows users), for example from an earlier move that was rolled back." >&2
    echo "The site is still on SQLite, so that copy is out of date. To start again from the SQLite data, add --reset-postgres." >&2
    exit 1
  fi
  say "Clearing the earlier PostgreSQL copy"
  psql "$PSQL_URL" -v ON_ERROR_STOP=1 -q -c "DROP SCHEMA public CASCADE" -c "CREATE SCHEMA public"
fi

say "Checking the SQLite data is at the current schema version"
flask_app "" "db upgrade" >/dev/null
say "Creating the schema in PostgreSQL with the same migrations every server uses"
flask_app "DATABASE_URL='$PG_URL'" "db upgrade" >/dev/null

if [ "$DRY" = 1 ]; then
  say "Dry run: counting rows, copying nothing"
  flask_app "" "migrate-to-postgres --target '$PG_URL' --dry-run"
  say "Dry run finished. The site was not touched."
  exit 0
fi

# ---- the real move -------------------------------------------------------------------------------------------------
say "Backing up everything first (the site keeps running)"
if [ -x "/etc/cron.daily/$NAME-backup" ]; then "/etc/cron.daily/$NAME-backup"; else bash "$HERE/backup.sh" "$ENV_FILE"; fi

SWITCHED=0; STOPPED=0
# An EXIT trap, not an ERR trap: it also runs when `set -e` stops the script inside a function. Until the settings are
# switched, any failure leaves the site running on SQLite exactly as it was.
on_exit() {
  local code=$?
  if [ "$code" != 0 ] && [ "$SWITCHED" = 0 ]; then
    echo "" >&2; echo "!! The move failed (exit status $code). Nothing was switched: the site stays on SQLite." >&2
    if [ "$STOPPED" = 1 ]; then systemctl start "$NAME" && echo "!! The site was started again on SQLite." >&2; fi
  fi
}
trap on_exit EXIT

say "Stopping the site for the copy"
systemctl stop "$NAME"; STOPPED=1
flask_app "" "db upgrade" >/dev/null   # in case anything changed while backing up
say "Copying and verifying every table"
flask_app "" "migrate-to-postgres --target '$PG_URL'"

say "Switching the settings to PostgreSQL"
cp -p "$ENV_FILE" "$BEFORE_ENV"
python3 - "$ENV_FILE" "$PG_URL" <<'PY'
import sys
path, url = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines()
lines = [("DATABASE_URL=" + url) if l.startswith("DATABASE_URL=") else l for l in lines]
open(path, "w").write("\n".join(lines) + "\n")
PY
chown "root:$NAME" "$ENV_FILE"; chmod 640 "$ENV_FILE"
mv "$SQLITE_FILE" "$SQLITE_FILE.migrated-$STAMP"
echo "$SQLITE_FILE.migrated-$STAMP" > "$MARKER"
SWITCHED=1
trap - EXIT
rm -f "$PG_CRED_FILE"   # the URL now lives in the settings file

say "Starting the site on PostgreSQL"
systemctl start "$NAME" || true
if healthy; then
  say "Done. The site is answering from PostgreSQL."
  echo "    Settings before the move: $BEFORE_ENV"
  echo "    SQLite file kept as:      $SQLITE_FILE.migrated-$STAMP   (delete it when you are happy)"
  echo "    To go back:               sudo bash $HERE/migrate-to-postgres.sh --name $NAME --rollback"
else
  echo "!! The site does not answer from PostgreSQL. Rolling back automatically." >&2
  rollback || true
  exit 1
fi
