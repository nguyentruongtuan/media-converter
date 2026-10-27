#!/usr/bin/env bash
# Generate random credentials and print a Secret manifest. Pipe it to kubeseal, sops or kubectl:
#   scripts/generate-secrets.sh | kubectl apply -n media-converter -f -
set -euo pipefail

rand() { openssl rand -hex "${1:-24}"; }

PG_PASSWORD="$(rand)"
MINIO_USER="admin$(rand 4)"
MINIO_PASSWORD="$(rand)"
API_ADMIN_KEY="$(rand 32)"

cat <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: media-converter-secrets
type: Opaque
stringData:
  POSTGRES_USER: transcoder
  POSTGRES_PASSWORD: ${PG_PASSWORD}
  POSTGRES_DB: transcoder
  DATABASE_URL: postgresql+psycopg://transcoder:${PG_PASSWORD}@postgres:5432/transcoder
  MINIO_ROOT_USER: ${MINIO_USER}
  MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD}
  MINIO_ACCESS_KEY: ${MINIO_USER}
  MINIO_SECRET_KEY: ${MINIO_PASSWORD}
  API_KEYS: admin:${API_ADMIN_KEY}:admin
EOF

echo "# Admin API key: ${API_ADMIN_KEY}" >&2
