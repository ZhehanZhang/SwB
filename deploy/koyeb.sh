#!/bin/sh
set +x
set -eu

app=${1:?Usage: sh deploy/koyeb.sh APP [BRANCH] [REGION]}
branch=${2:-$(git branch --show-current)}
region=${3:-was}
case "$app" in
    ""|-*|*[!a-z0-9-]*) printf '%s\n' 'Use lowercase letters, digits and hyphens for the app name.' >&2; exit 2 ;;
esac
case "$region" in
    was|fra) ;;
    *) printf '%s\n' 'Use was or fra for the volume region.' >&2; exit 2 ;;
esac
if [ -z "$branch" ]; then
    printf '%s\n' 'Pass the published SwB branch to deploy.' >&2
    exit 2
fi

volume="swb-$app"
secret="swb-$app-setup"
setup=".env.swb-setup-koyeb-$app"
code=$(openssl rand -hex 32)
(umask 077; set -C; printf 'SWB_SETUP_CODE=%s\n' "$code" > "$setup")
printf 'Setup code saved in %s\n' "$setup"
printf '%s' "$code" | koyeb secrets create "$secret" --value-from-stdin
unset code
koyeb volumes create "$volume" --size 1 --region "$region"
koyeb apps init "$app" \
    --git github.com/ZhehanZhang/SwB --git-branch "$branch" --git-builder docker \
    --git-docker-dockerfile Dockerfile --git-docker-target paas --git-no-deploy-on-push \
    --instance-type small --regions "$region" --scale 1 --volumes "$volume:/data" \
    --ports 8000:http --routes /:8000 --checks 8000:http:/healthz --checks-grace-period 8000=15 \
    --env PORT=8000 --env SWB_DEPLOY_PLATFORM=koyeb --env "SWB_SETUP_CODE={{ secret.$secret }}"
