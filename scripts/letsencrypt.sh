#!/usr/bin/env bash
# Issue and renew a Let's Encrypt certificate for the Compose stack's domains, using certbot in Docker.
# Run as root on the server the domains point at, with port 80 reachable from the internet:
#   sudo scripts/letsencrypt.sh issue --email you@example.com [--staging] [--nginx] [-d media.example.com ...]
#   sudo scripts/letsencrypt.sh renew
# Without -d, the domains come from PUBLIC_API_BASE_URL, PUBLIC_MEDIA_BASE_URL and MINIO_PUBLIC_ENDPOINT in .env.
#
# --nginx also writes an NGINX site that uses the certificate and forwards each domain to its local port.
# It needs the three addresses in .env, because that is what says which domain serves what.
#
# certbot needs port 80 for a moment. If a proxy holds it, set PROXY_STOP_CMD and PROXY_START_CMD,
# e.g. "systemctl stop nginx" and "systemctl start nginx". They only run when certbot is about to.
# Those two commands are the default once --nginx is used.
set -euo pipefail

CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/certbot:v5.9.0}"
LETSENCRYPT_DIR="${LETSENCRYPT_DIR:-/etc/letsencrypt}"
ENV_FILE="${ENV_FILE:-$(cd "$(dirname "$0")/.." && pwd)/.env}"
NGINX_CONF="${NGINX_CONF:-/etc/nginx/conf.d/media-converter.conf}"
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

# Called on success, so that a proxy which fails to start fails the script too.
restart_proxy() {
  trap - EXIT
  start_proxy
}

use_nginx_service() {
  : "${PROXY_STOP_CMD:=systemctl stop nginx}"
  : "${PROXY_START_CMD:=systemctl start nginx}"
}

# Args: certificate name, then the media, API and S3 domains.
write_nginx_conf() {
  local cert_dir="$LETSENCRYPT_DIR/live/$1" media="$2" api="$3" s3="$4" media_port previous=""
  media_port="$(env_value MEDIA_PORT)"
  media_port="${media_port:-8080}"
  [[ "$media_port" =~ ^[0-9]+$ ]] || die "MEDIA_PORT is not a port: $media_port"
  if [[ -f "$NGINX_CONF" ]]; then previous="$(cat "$NGINX_CONF")"; fi

  cat >"$NGINX_CONF" <<EOF
# Written by scripts/letsencrypt.sh, which overwrites it on the next issue --nginx.
map \$host \$media_converter_port {
	$media $media_port;
	$api 8000;
	$s3 9000;
}
server {
	listen 80;
	server_name $media $api $s3;
	return 301 https://\$host\$request_uri;
}
server {
	listen 443 ssl;
	server_name $media $api $s3;
	ssl_certificate $cert_dir/fullchain.pem;
	ssl_certificate_key $cert_dir/privkey.pem;
	# Uploads can be any size. The original Host is part of the S3 upload signature.
	client_max_body_size 0;
	location / {
		proxy_pass http://127.0.0.1:\$media_converter_port;
		proxy_http_version 1.1;
		proxy_request_buffering off;
		proxy_set_header Host \$host;
		proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
		proxy_set_header X-Forwarded-Proto \$scheme;
	}
}
EOF

  # A config NGINX rejects must not stay in place, or NGINX could not start again.
  if ! nginx -t; then
    if [[ -n "$previous" ]]; then printf '%s\n' "$previous" >"$NGINX_CONF"; else rm -f "$NGINX_CONF"; fi
    die "NGINX rejected the new config, so it was not kept"
  fi
  echo "NGINX config: $NGINX_CONF"
}

issue() {
  local email="" staging=false nginx=false cert_name="$CERT_NAME" domains="" domain
  local media_domain="" api_domain="" s3_domain=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --email) [[ $# -ge 2 ]] || die "--email needs a value"; email="$2"; shift 2 ;;
      -d | --domain) [[ $# -ge 2 ]] || die "$1 needs a value"; domains+=" $(hostname_of "$2")"; shift 2 ;;
      --staging) staging=true; shift ;;
      --nginx) nginx=true; shift ;;
      -h | --help) usage ;;
      *) die "unknown option: $1" ;;
    esac
  done

  [[ -n "$email" ]] || die "--email is required, Let's Encrypt uses it for account notices"
  if [[ "$nginx" == true ]]; then
    [[ -z "$domains" ]] || die "--nginx takes its domains from $ENV_FILE, so it cannot be combined with -d"
    command -v nginx >/dev/null || die "nginx is not installed"
    media_domain="$(hostname_of "$(env_value PUBLIC_MEDIA_BASE_URL)")"
    api_domain="$(hostname_of "$(env_value PUBLIC_API_BASE_URL)")"
    s3_domain="$(hostname_of "$(env_value MINIO_PUBLIC_ENDPOINT)")"
    [[ -n "$media_domain" && -n "$api_domain" && -n "$s3_domain" ]] ||
      die "--nginx needs PUBLIC_MEDIA_BASE_URL, PUBLIC_API_BASE_URL and MINIO_PUBLIC_ENDPOINT in $ENV_FILE"
    domains="$media_domain $api_domain $s3_domain"
    use_nginx_service
  fi
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
  if [[ "$nginx" == true ]]; then
    write_nginx_conf "$cert_name" "$media_domain" "$api_domain" "$s3_domain"
  fi
  restart_proxy
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

  if [[ -f "$NGINX_CONF" ]]; then use_nginx_service; fi
  stop_proxy
  run_certbot renew --non-interactive --cert-name "$CERT_NAME"
  restart_proxy
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
