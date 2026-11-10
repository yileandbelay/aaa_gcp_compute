#!/usr/bin/env bash
# Google Compute Engine startup script for Kasina Cakery Academy (Debian 12 / Ubuntu 22.04+).
# Usage: pass as --metadata-from-file=startup-script=deploy/gce-startup.sh and set
#   --metadata=kasina-repo=https://github.com/<owner>/<repo>.git,kasina-branch=main,kasina-domain=academy.example.com
# Optional: kasina-backup-bucket=gs://my-kasina-backups to copy daily backups to Cloud Storage.
set -euo pipefail
meta() { curl -sf -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" || true; }
REPO="$(meta kasina-repo)"; BRANCH="$(meta kasina-branch)"; DOMAIN="$(meta kasina-domain)"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/kasina; DATA_DIR=/var/lib/kasina

apt-get update -y
apt-get install -y python3 python3-venv python3-pip git nginx

id kasina >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin kasina
mkdir -p "$DATA_DIR/uploads" "$DATA_DIR/instance"
if [ ! -d "$APP_DIR/.git" ]; then git clone --branch "$BRANCH" "$REPO" "$APP_DIR"; else git -C "$APP_DIR" pull --ff-only; fi
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f /etc/kasina.env ]; then
  cat > /etc/kasina.env <<ENV
SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
DATABASE_URL=sqlite:///$DATA_DIR/instance/kasina.db
UPLOAD_DIR=$DATA_DIR/uploads
SESSION_COOKIE_SECURE=false
BEHIND_PROXY=true
ENV
  chmod 600 /etc/kasina.env
fi
# Settings added in later versions, appended if missing.
grep -q '^USE_X_ACCEL_REDIRECT=' /etc/kasina.env || echo 'USE_X_ACCEL_REDIRECT=true' >> /etc/kasina.env
grep -q '^BACKUP_DIR=' /etc/kasina.env || echo "BACKUP_DIR=$DATA_DIR/backups" >> /etc/kasina.env
BUCKET="$(meta kasina-backup-bucket)"
if [ -n "$BUCKET" ] && ! grep -q '^BACKUP_BUCKET=' /etc/kasina.env; then echo "BACKUP_BUCKET=$BUCKET" >> /etc/kasina.env; fi
chown -R kasina:kasina "$DATA_DIR"
chmod 750 "$DATA_DIR"
# nginx (www-data) reads lecture videos for X-Accel-Redirect through the kasina group.
usermod -aG kasina www-data

# Apply database migrations before (re)starting the app.
sudo -u kasina bash -c "set -a; . /etc/kasina.env; cd $APP_DIR && .venv/bin/flask --app wsgi db upgrade"

# Daily backup of the database and uploads (see deploy/backup.sh).
install -m 755 "$APP_DIR/deploy/backup.sh" /etc/cron.daily/kasina-backup

install -m 644 "$APP_DIR/deploy/kasina.service" /etc/systemd/system/kasina.service
sed "s/__DOMAIN__/${DOMAIN:-_}/" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/kasina
ln -sf /etc/nginx/sites-available/kasina /etc/nginx/sites-enabled/kasina
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable --now kasina
systemctl restart kasina
nginx -t && systemctl restart nginx
echo "Kasina is up. Create an admin with:"
echo "  sudo -u kasina bash -c 'set -a; . /etc/kasina.env; cd $APP_DIR && .venv/bin/flask --app wsgi create-admin'"
