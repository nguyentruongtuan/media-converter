#!/bin/sh
# Runs before the NGINX image renders its templates. An empty domain would otherwise surface as
# "invalid number of the map parameters". This check lives here because ${VAR:?} in docker-compose.yml
# is evaluated even when the tls profile is off, which would break every other Compose command.
set -eu

missing=""
for name in MEDIA_DOMAIN API_DOMAIN S3_DOMAIN; do
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        missing="$missing $name"
    fi
done

if [ -n "$missing" ]; then
    echo "proxy: set these in .env to use the tls profile:$missing" >&2
    exit 1
fi
