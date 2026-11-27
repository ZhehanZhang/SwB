#!/bin/sh
set +x
set -eu

app=${1:?Usage: sh deploy/fly.sh APP}
case "$app" in
    ""|-*|*[!a-z0-9-]*) printf '%s\n' 'Use lowercase letters, digits and hyphens for the app name.' >&2; exit 2 ;;
esac

setup=".env.swb-setup-fly-$app"
code=$(openssl rand -hex 32)
(umask 077; set -C; printf 'SWB_SETUP_CODE=%s\n' "$code" > "$setup")
unset code
printf 'Setup code saved in %s\n' "$setup"
fly launch --copy-config --no-deploy --name "$app" --no-github-workflow
fly secrets import --stage --app "$app" < "$setup"
fly deploy --ha=false --app "$app"
