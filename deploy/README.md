# Deploy SwB

Use one instance with a volume at `/data`. A volume keeps your data when the instance restarts. Back up the volume before an update.

These deployments build the repository Dockerfile. After the container release opens to the public, use `ghcr.io/zhehanzhang/swb:1.0.0` with a volume UID 10001 can write to.

## Sign in

Open the service shell in your hosting account. Run `swb login-link` there. Open the link within five minutes. Ask your agent to run `swb connect <kind>` in the same shell. Share the sign-in link only with the owner.

Open the service URL or use the sign-in page address in the service log. Use `/healthz` to check service health.

## Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ZhehanZhang/SwB)

The [Blueprint](../render.yaml) creates a paid web service and a 1 GB disk. Render sets the service URL and generates the owner key. Use the Shell tab in the service to sign in.

## Railway

Use the [Railway template composer](https://docs.railway.com/templates/create) to publish the deploy button. Select `https://github.com/ZhehanZhang/SwB` as the source. Use the root Dockerfile and one replica. Attach a 1 GB volume at `/data`. Set `/healthz` as the health check. Set `/data` as the required mount path. Enable public networking on port `8000`.

Set these variables in the template.

```dotenv
PORT=8000
SWITCHBOARD_DEPLOY_PLATFORM=railway
SWITCHBOARD_KEY_OWNER=${{secret()}}
```

Use `railway ssh -- swb login-link` in a linked project. [Railway infrastructure as code](../.railway/railway.ts) describes the service and volume. [Legacy Railway settings](../railway.json) apply to existing services until December 1, 2026.

## Fly.io

Sign in with the [Fly command line tool](https://docs.fly.io/flyctl/). Run this in the SwB repository.

```sh
fly launch --copy-config --generate-name --ha=false --no-github-workflow
```

Fly creates a public address and a 1 GB volume from [fly.toml](../fly.toml). SwB saves the owner key on the volume. Get the sign-in link in a private terminal.

```sh
fly ssh console -C 'swb login-link'
```

## Koyeb

Sign in with the [Koyeb command line tool](https://www.koyeb.com/docs/build-and-deploy/cli/installation). Run this in the SwB repository. Replace `my-swb` with your app name.

```sh
sh deploy/koyeb.sh my-swb
koyeb services exec --app my-swb my-swb swb -- login-link
```

The script uses a paid instance and a 1 GB volume in Washington, DC. Pass a branch and `fra` after the app name to use Frankfurt. SwB saves the owner key on the volume. Koyeb volumes are in preview.

## Docker Compose

Use a server with Docker Compose. Point a domain name at your server. Open ports 80 and 443. Set `DOMAIN` in [the example settings](compose/.env.example) and save the result as `deploy/compose/.env`.

```sh
cd deploy/compose
docker compose up -d --build
docker compose exec switchboard swb login-link
```

Caddy manages HTTPS. SwB saves the owner key on the volume. This setup also works on a DigitalOcean Droplet.
