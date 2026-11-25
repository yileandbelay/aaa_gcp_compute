#!/usr/bin/env bash
# Helpers for running the academy's database on PostgreSQL on the same server. Sourced by install.sh and
# migrate-to-postgres.sh (not run directly). PostgreSQL listens only on this machine (localhost); the app
# connects with a password that is generated here and kept only in /etc/<name>.env (readable by root and the app).

# PostgreSQL's own administration user runs the commands. PGHOST / PGPORT in the environment are honoured (used by tests).
pg_run() { (cd / && runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 "$@"); }

pg_install() {
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q postgresql postgresql-client
  systemctl enable -q postgresql 2>/dev/null || true
  systemctl start postgresql 2>/dev/null || true
  local i
  for i in $(seq 1 30); do pg_run -tAc "SELECT 1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "PostgreSQL did not start. Look at: journalctl -u postgresql" >&2
  return 1
}

# pg_ident NAME: a database/role name that is safe without quoting (dashes become underscores).
pg_ident() { echo "${1//-/_}"; }

# pg_provision NAME: create the role and database if they are missing; sets PG_URL.
# Never touches an existing database's data. A fresh password is generated only when the role is new; if PG_CRED_FILE is
# set, the URL (with the password) is kept there, readable by root only, so a second run finds the same role.
pg_provision() {
  local ident password port
  ident="$(pg_ident "$1")"
  [[ "$ident" =~ ^[a-z][a-z0-9_]{1,30}$ ]] || { echo "Cannot use '$1' as a database name." >&2; return 1; }
  password="$(python3 -c 'import secrets;print(secrets.token_hex(24))')"
  if [ "$(pg_run -tAc "SELECT count(*) FROM pg_roles WHERE rolname='$ident'")" = 0 ]; then
    pg_run -c "CREATE ROLE $ident LOGIN PASSWORD '$password'"
  else
    # A role made by an earlier run (for example a dry run): reuse the password kept in PG_CRED_FILE.
    if [ -z "${PG_EXISTING_PASSWORD:-}" ] && [ -n "${PG_CRED_FILE:-}" ] && [ -f "$PG_CRED_FILE" ]; then
      PG_EXISTING_PASSWORD="$(sed -n 's#^postgresql+psycopg2://[^:]*:\([^@]*\)@.*#\1#p' "$PG_CRED_FILE")"
    fi
    if [ -z "${PG_EXISTING_PASSWORD:-}" ]; then
      echo "The database role '$ident' already exists and its password is not in the settings file." >&2
      echo "Set a new one with: sudo -u postgres psql -c \"ALTER ROLE $ident PASSWORD 'NEWPASSWORD'\"" >&2
      echo "then add DATABASE_URL=postgresql+psycopg2://$ident:NEWPASSWORD@127.0.0.1:<port>/$ident to the settings file." >&2
      return 1
    fi
    password="$PG_EXISTING_PASSWORD"
  fi
  if [ "$(pg_run -tAc "SELECT count(*) FROM pg_database WHERE datname='$ident'")" = 0 ]; then
    pg_run -c "CREATE DATABASE $ident OWNER $ident ENCODING 'UTF8' TEMPLATE template0"
  fi
  port="$(pg_run -tAc "SHOW port")"
  PG_URL="postgresql+psycopg2://$ident:$password@127.0.0.1:$port/$ident"
  if [ -n "${PG_CRED_FILE:-}" ]; then ( umask 077; echo "$PG_URL" > "$PG_CRED_FILE" ); fi   # root-only; lets a later run reuse the role
}

# pg_table_count URL: how many tables the database already has (0 = empty).
pg_table_count() {
  psql "$(echo "$1" | sed 's/+psycopg2//')" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
}
