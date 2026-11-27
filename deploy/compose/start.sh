#!/bin/sh
set +x
set -eu

domain=${1:?Usage: sh deploy/compose/start.sh DOMAIN}
case "$domain" in
    -*|.*|*[!a-zA-Z0-9.-]*) printf '%s\n' 'Use your server domain name.' >&2; exit 2 ;;
esac

cd "$(dirname "$0")"
code=$(openssl rand -hex 32)
(umask 077; set -C; printf 'DOMAIN=%s\nSWB_SETUP_CODE=%s\n' "$domain" "$code" > .env)
unset code
docker compose up -d --build
