#!/usr/bin/env bash
# Google Compute Engine startup script: reads settings from instance metadata
# and runs install.sh. Example:
#   gcloud compute instances create my-academy ... \
#     --metadata=academy-repo=https://github.com/<owner>/<repo>.git,academy-domain=learn.example.com,academy-email=you@example.com \
#     --metadata-from-file=startup-script=deploy/gce-startup.sh
set -euo pipefail
meta() { curl -sf -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" || true; }
REPO="$(meta academy-repo)"; BRANCH="$(meta academy-branch)"; NAME="$(meta academy-name)"
DOMAIN="$(meta academy-domain)"; EMAIL="$(meta academy-email)"
SRC=/tmp/academy-installer
rm -rf "$SRC" && git clone -q --depth 1 --branch "${BRANCH:-main}" "$REPO" "$SRC" 2>/dev/null \
  || { apt-get update -y -q && apt-get install -y -q git && git clone -q --depth 1 --branch "${BRANCH:-main}" "$REPO" "$SRC"; }
bash "$SRC/deploy/install.sh" --repo "$REPO" --branch "${BRANCH:-main}" --name "${NAME:-academy}" \
  ${DOMAIN:+--domain "$DOMAIN"} ${EMAIL:+--email "$EMAIL"}
