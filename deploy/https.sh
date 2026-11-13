#!/usr/bin/env bash
# HTTPS helpers for install.sh (sourced, not run). The goal: re-running the installer never leaves
# the site without HTTPS, even when certbot is busy with its own renewal or fails.
#
#   https_snapshot CONF    keep a copy of the nginx config if it already has HTTPS
#   install_certificate D E [ALIAS...]   wait for any running certbot, then get or reinstall one
#                          certificate for domain D (email E) that also covers the aliases, retrying;
#                          returns non-zero if it never worked
#   normalize_names D LIST  validate a list of extra names (spaces or commas), drop D and duplicates
#   resolvable_names N...   print the names that have a DNS record, warn about the others
#   redirect_block D ALIAS...  print an nginx server block redirecting the aliases to https://D
#   https_restore CONF     put the HTTPS config back (after a failed certbot run)
#   https_clear CONF       drop the snapshot after success
#
# Settings (environment): CERTBOT_WAIT_SECONDS (300), CERTBOT_TRIES (5), CERTBOT_RETRY_SECONDS (10).

CERTBOT_WAIT_SECONDS="${CERTBOT_WAIT_SECONDS:-300}"
CERTBOT_TRIES="${CERTBOT_TRIES:-5}"
CERTBOT_RETRY_SECONDS="${CERTBOT_RETRY_SECONDS:-10}"

# certbot is a Python program, so look for "/certbot " in the command line, not a process named certbot.
certbot_busy() {
  pgrep -f "${CERTBOT_PGREP:-/certbot( |\$)}" >/dev/null 2>&1
}

wait_for_certbot() {
  local waited=0
  while certbot_busy; do
    if [ "$waited" -ge "$CERTBOT_WAIT_SECONDS" ]; then
      echo "!! Another certbot run is still active after ${CERTBOT_WAIT_SECONDS}s" >&2
      return 1
    fi
    echo "==> Another certbot run is active (probably the automatic renewal); waiting..."
    sleep 5
    waited=$((waited + 5))
  done
}

install_certificate() {
  local domain="$1" email="$2" attempt name
  shift 2
  local names=(-d "$domain")
  for name in "$@"; do names+=(-d "$name"); done
  for attempt in $(seq 1 "$CERTBOT_TRIES"); do
    wait_for_certbot || return 1
    # --keep-until-expiring reuses an existing certificate, so repeated runs do not hit Let's Encrypt
    # limits; --cert-name keeps one certificate named after the main domain, and --expand lets it grow
    # when names are added (for example the bare domain and the old address).
    if certbot --nginx -n --agree-tos --keep-until-expiring --expand -m "$email" "${names[@]}" \
         --cert-name "$domain" --redirect; then
      return 0
    fi
    echo "!! certbot attempt $attempt of $CERTBOT_TRIES failed" >&2
    [ "$attempt" -lt "$CERTBOT_TRIES" ] && sleep $((attempt * CERTBOT_RETRY_SECONDS))
  done
  return 1
}

normalize_names() {
  local primary="$1" raw name out=""
  shift
  raw="$*"; raw="${raw//,/ }"
  for name in $raw; do
    name="${name,,}"
    if ! [[ "$name" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$ ]]; then
      echo "Not a valid domain name: $name" >&2
      return 1
    fi
    [ "$name" = "$primary" ] && continue
    case " $out " in *" $name "*) continue ;; esac
    out="$out $name"
  done
  for name in $out; do printf '%s\n' "$name"; done
}

resolvable_names() {
  local name
  for name in "$@"; do
    if getent hosts "$name" >/dev/null 2>&1; then
      printf '%s\n' "$name"
    else
      echo "!! $name has no DNS record yet, so it is skipped. Add the record at your domain provider, then re-run the installer." >&2
    fi
  done
}

# The redirect lives in "location /" so certbot can still answer its challenge for these names
# (a server-level return would redirect the challenge before certbot's location is reached).
redirect_block() {
  local primary="$1" names
  shift
  names="$*"
  printf '\nserver {\n    listen 80;\n    server_name %s;\n\n    location / {\n        return 301 https://%s$request_uri;\n    }\n}\n' "$names" "$primary"
}

https_snapshot() {
  if [ -f "$1" ] && grep -q "ssl_certificate" "$1"; then
    cp -p "$1" "$1.https-keep"
  else
    rm -f "$1.https-keep"
  fi
}

https_restore() {
  [ -f "$1.https-keep" ] || return 1
  cp -p "$1.https-keep" "$1"
}

https_clear() {
  rm -f "$1.https-keep"
}
