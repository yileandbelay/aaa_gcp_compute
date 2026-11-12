#!/usr/bin/env bash
# Install or update an academy on an Ubuntu (22.04+) or Debian (12+) server.
#
#   sudo bash deploy/install.sh --domain learn.example.com --email you@example.com
#
# Options:
#   --domain D   Public domain name pointing at this server (enables HTTPS with --email)
#   --email E    Email for Let's Encrypt certificate notices (needed for HTTPS)
#   --name N     Instance name: user, folders, service (default: academy).
#                Use different names to run several academies on one server.
#   --port P     Local port for this instance (default: 8000; use 8001, 8002... for more).
#                Several instances on one server each need their own --domain.
#   --repo URL   Git repository to install from (default: this checkout's origin)
#   --branch B   Branch to install (default: main)
#
# To update, re-run it (options are remembered): it pulls the code, applies
# database migrations and restarts the app. Settings in /etc/<name>.env are kept.
set -euo pipefail

NAME=academy; PORT=""; DOMAIN=""; EMAIL=""; REPO=""; BRANCH=main
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || { echo "--name must be lowercase letters, digits or dashes." >&2; exit 1; }

# Remember domain/email/port/repo so a plain re-run (to update) keeps them.
STATE=/etc/$NAME.install
if [ -f "$STATE" ]; then . "$STATE"; fi
DOMAIN="${DOMAIN:-${SAVED_DOMAIN:-}}"; EMAIL="${EMAIL:-${SAVED_EMAIL:-}}"
PORT="${PORT:-${SAVED_PORT:-8000}}"; REPO="${REPO:-${SAVED_REPO:-}}"
if [ -z "$REPO" ]; then
  REPO="$(git -C "$(dirname "$0")/.." remote get-url origin 2>/dev/null || true)"
  [ -n "$REPO" ] || { echo "Pass --repo <git url>." >&2; exit 1; }
fi

APP_DIR=/opt/$NAME; DATA_DIR=/var/lib/$NAME; ENV_FILE=/etc/$NAME.env
# shellcheck source=deploy/https.sh
. "$(dirname "${BASH_SOURCE[0]}")/https.sh"
printf 'SAVED_DOMAIN=%q\nSAVED_EMAIL=%q\nSAVED_PORT=%q\nSAVED_REPO=%q\n' "$DOMAIN" "$EMAIL" "$PORT" "$REPO" > "$STATE"
echo "==> Installing '$NAME' from $REPO ($BRANCH) on port $PORT"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y -q
apt-get install -y -q python3 python3-venv python3-pip git nginx certbot python3-certbot-nginx
# Optional: lets the nightly backup also copy itself off the server (see RCLONE_REMOTE in the settings).
apt-get install -y -q rclone || echo "(rclone is not available here; off-server backups stay off until it is installed)"

id "$NAME" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$NAME"
mkdir -p "$DATA_DIR/uploads" "$DATA_DIR/instance" "$DATA_DIR/backups"

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH" && git -C "$APP_DIR" checkout -q "$BRANCH" \
    && git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
  # First install: start from the example and fill in safe server defaults.
  sed -e "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')|" \
      -e "s|^UPLOAD_DIR=.*|UPLOAD_DIR=$DATA_DIR/uploads|" \
      -e "s|^SESSION_COOKIE_SECURE=.*|SESSION_COOKIE_SECURE=false|" \
      -e "s|^PAYMENT_DEMO_MODE=.*|PAYMENT_DEMO_MODE=false|" \
      "$APP_DIR/.env.example" > "$ENV_FILE"
  {
    echo ""
    echo "# Set by install.sh"
    echo "DATABASE_URL=sqlite:///$DATA_DIR/instance/academy.db"
    echo "BACKUP_DIR=$DATA_DIR/backups"
  } >> "$ENV_FILE"
  echo "==> Created $ENV_FILE - edit it to set your academy name, payment and email details."
fi
# The app user must be able to read the settings (the migration and create-admin commands run as
# that user; systemd reads the file as root). Readable by root and the app's group only.
chown "root:$NAME" "$ENV_FILE"
chmod 640 "$ENV_FILE"
grep -q '^USE_X_ACCEL_REDIRECT=' "$ENV_FILE" || echo 'USE_X_ACCEL_REDIRECT=true' >> "$ENV_FILE"
grep -q '^BEHIND_PROXY=' "$ENV_FILE" || echo 'BEHIND_PROXY=true' >> "$ENV_FILE"

chown -R "$NAME:$NAME" "$DATA_DIR"
# Folder the app may create inside its (otherwise root-owned) code directory.
install -d -o "$NAME" -g "$NAME" "$APP_DIR/instance"
chmod 750 "$DATA_DIR"; chmod 700 "$DATA_DIR/backups"
# nginx (www-data) streams lecture videos for X-Accel-Redirect through the app's group.
usermod -aG "$NAME" www-data

echo "==> Applying database migrations"
sudo -u "$NAME" bash -c "set -a; . '$ENV_FILE'; cd '$APP_DIR' && .venv/bin/flask --app wsgi db upgrade"

sed -e "s|__NAME__|$NAME|g" -e "s|__PORT__|$PORT|g" "$APP_DIR/deploy/academy.service" \
  > "/etc/systemd/system/$NAME.service"
NGINX_CONF="/etc/nginx/sites-available/$NAME"
https_snapshot "$NGINX_CONF"   # remember the HTTPS version in case certbot cannot put it back below
sed -e "s|__NAME__|$NAME|g" -e "s|__PORT__|$PORT|g" -e "s|__DOMAIN__|${DOMAIN:-_}|g" \
  "$APP_DIR/deploy/nginx.conf" > "$NGINX_CONF"
ln -sf "/etc/nginx/sites-available/$NAME" "/etc/nginx/sites-enabled/$NAME"
rm -f /etc/nginx/sites-enabled/default

sed -e "s|__ENV_FILE__|$ENV_FILE|g" "$APP_DIR/deploy/backup.sh" > "/etc/cron.daily/$NAME-backup"
chmod 755 "/etc/cron.daily/$NAME-backup"

systemctl daemon-reload
systemctl enable -q "$NAME"
systemctl restart "$NAME"
nginx -t -q && systemctl restart nginx

if [ -n "$DOMAIN" ] && [ -n "$EMAIL" ]; then
  echo "==> Requesting an HTTPS certificate for $DOMAIN"
  # Waits for a running certbot (the automatic renewal), retries, and reuses the existing certificate.
  if install_certificate "$DOMAIN" "$EMAIL"; then
    https_clear "$NGINX_CONF"
    sed -i 's|^SESSION_COOKIE_SECURE=.*|SESSION_COOKIE_SECURE=true|' "$ENV_FILE"
    systemctl restart "$NAME"
  else
    echo "!! HTTPS setup failed. Check that $DOMAIN points to this server, then re-run." >&2
    # Never leave the site without HTTPS: put back the previous HTTPS configuration if there was one.
    if https_restore "$NGINX_CONF" && nginx -t -q && systemctl reload nginx; then
      echo "==> Kept the previous HTTPS configuration, so the site is still served over HTTPS."
    fi
  fi
fi

sleep 2
if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null; then
  echo "==> '$NAME' is running."
else
  echo "!! The app did not answer its health check. See: journalctl -u $NAME -n 50" >&2
fi
cat <<MSG

Next steps:
  1. Edit $ENV_FILE (academy name, payments, email), then: systemctl restart $NAME
  2. Create your admin account:
     sudo -u $NAME bash -c "set -a; . $ENV_FILE; cd $APP_DIR && .venv/bin/flask --app wsgi create-admin"
  3. Open https://${DOMAIN:-<server-ip>} and log in.
MSG
