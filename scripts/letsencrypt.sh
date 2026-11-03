#!/usr/bin/env bash
# Issue and renew a Let's Encrypt certificate for the Compose stack's domains, using certbot in Docker.
# Run as root on the server the domains point at, with port 80 reachable from the internet:
#   sudo scripts/letsencrypt.sh issue --email you@example.com [--staging] [-d media.example.com ...]
#   sudo scripts/letsencrypt.sh renew
# Without -d, the domains come from PUBLIC_API_BASE_URL, PUBLIC_MEDIA_BASE_URL and MINIO_PUBLIC_ENDPOINT in .env.
#
# certbot needs port 80 for a moment. If a proxy holds it, set PROXY_STOP_CMD and PROXY_START_CMD,
# e.g. "systemctl stop nginx" and "systemctl start nginx". They only run when certbot is about to.
set -euo pipefail

CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/certbot:v5.9.0}"
LETSENCRYPT_DIR="${LETSENCRYPT_DIR:-/etc/letsencrypt}"
ENV_FILE="${ENV_FILE:-$(cd "$(dirname "$0")/.." && pwd)/.env}"
CERT_NAME=media-converter
RENEW_BEFORE_SECONDS=$((30 * 24 * 3600))

die() { echo "error: $*" >&2; exit 1; }

usage() { sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

env_value() {
  [[ -f "$ENV_FILE" ]] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d "\"'\r"
}

# Reduce a URL or host:port to its hostname.
hostname_of() { sed -E 's#^[a-zA-Z]+://##; s#[/:].*$##' <<<"$1" | tr '[:upper:]' '[:lower:]'; }

run_certbot() {
  docker run --rm -p 80:80 -v "$LETSENCRYPT_DIR:/etc/letsencrypt" "$CERTBOT_IMAGE" "$@"
}

start_proxy() { if [[ -n "${PROXY_START_CMD:-}" ]]; then sh -c "$PROXY_START_CMD"; fi; }

# The proxy comes back up with the new certificate, including when certbot fails.
stop_proxy() {
  if [[ -n "${PROXY_STOP_CMD:-}" ]]; then
    trap start_proxy EXIT
    sh -c "$PROXY_STOP_CMD"
  fi
}

issue() {
  local email="" staging=false cert_name="$CERT_NAME" domains="" domain
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --email) [[ $# -ge 2 ]] || die "--email needs a value"; email="$2"; shift 2 ;;
      -d | --domain) [[ $# -ge 2 ]] || die "$1 needs a value"; domains+=" $(hostname_of "$2")"; shift 2 ;;
      --staging) staging=true; shift ;;
      -h | --help) usage ;;
      *) die "unknown option: $1" ;;
    esac
  done

  [[ -n "$email" ]] || die "--email is required, Let's Encrypt uses it for account notices"
  if [[ -z "$domains" ]]; then
    for var in PUBLIC_API_BASE_URL PUBLIC_MEDIA_BASE_URL MINIO_PUBLIC_ENDPOINT; do
      value="$(env_value "$var")"
      if [[ -n "$value" ]]; then domains+=" $(hostname_of "$value")"; fi
    done
  fi
  [[ -n "$domains" ]] || die "no domains: pass -d or set the public addresses in $ENV_FILE"

  local args=(certonly --standalone --non-interactive --agree-tos --keep-until-expiring --expand --email "$email")
  # Staging gets its own name, otherwise a later production run would keep the untrusted certificate.
  if [[ "$staging" == true ]]; then
    args+=(--staging)
    cert_name="$CERT_NAME-staging"
  fi
  args+=(--cert-name "$cert_name")

  for domain in $domains; do
    [[ "$domain" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+([a-z]{2,}|xn--[a-z0-9-]+)$ ]] ||
      die "not a public domain name: $domain"
    args+=(-d "$domain")
  done

  stop_proxy
  run_certbot "${args[@]}"
  echo "Certificate: $LETSENCRYPT_DIR/live/$cert_name/fullchain.pem"
  echo "Private key: $LETSENCRYPT_DIR/live/$cert_name/privkey.pem"
}

renew() {
  [[ $# -eq 0 ]] || die "renew takes no options"
  local cert="$LETSENCRYPT_DIR/live/$CERT_NAME/fullchain.pem"
  [[ -f "$cert" ]] || die "no certificate at $cert, run issue first"

  # Safe to run from a daily cron job: the proxy is left alone until renewal is due.
  if openssl x509 -checkend "$RENEW_BEFORE_SECONDS" -noout -in "$cert" >/dev/null; then
    echo "Certificate is valid for more than 30 days, nothing to do."
    return
  fi

  stop_proxy
  run_certbot renew --non-interactive --cert-name "$CERT_NAME"
}

[[ $# -ge 1 ]] || usage 1
command="$1"
shift
case "$command" in
  -h | --help) usage ;;
  issue | renew) ;;
  *) usage 1 ;;
esac

[[ "$(id -u)" -eq 0 ]] || die "run as root: certificates under $LETSENCRYPT_DIR are only readable by root"
command -v docker >/dev/null || die "docker is required"
"$command" "$@"
