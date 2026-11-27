# Deploy SwB

Use one instance with a volume at `/data`. A volume keeps your data when the instance restarts. Back up the volume before an update.

These deployments build the repository Dockerfile. After the container release opens to the public, use `ghcr.io/zhehanzhang/swb:1.0.0` with a volume UID 10001 can write to.

## Sign in

Open your app. Copy the setup code from your hosting account or the private file named by your launch command. Enter it in **Setup code**. Add a passkey to sign in again.

Use the service log to find the sign-in page address. The setup code works once. If you need a new sign-in link, run `swb login-link` in the private service shell. Open the link within five minutes.

Use `/healthz` to check service health.

## Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ZhehanZhang/SwB)

The button uses the repository default branch. The [Blueprint](../render.yaml) creates a paid web service and a 1 GB disk. Open **Environment** in the service. Copy `SWB_SETUP_CODE`.

## Railway

Use the [Railway template composer](https://docs.railway.com/templates/create) to publish the deploy button. Select the SwB release branch in `https://github.com/ZhehanZhang/SwB`. Use the root Dockerfile and one replica. Attach a 1 GB volume at `/data`. Set `/healthz` as the health check. Set `/data` as the required mount path. Enable public networking on port `8000`.

Copy the [template variables](../.railway/template.env.example) into the template.

Open **Variables** in the service. Copy `SWB_SETUP_CODE`. After sign-in, seal or remove this variable.

[Railway infrastructure as code](../.railway/railway.ts) describes the service and volume. [Legacy Railway settings](../railway.json) apply to existing services until December 1, 2026.

## Fly.io

Sign in with the [Fly command line tool](https://docs.fly.io/flyctl/). Run this in the SwB repository. Replace `my-swb` with a unique app name.

```sh
sh deploy/fly.sh my-swb
```

Fly creates a public address and a 1 GB volume from [fly.toml](../fly.toml). Open `.env.swb-setup-fly-my-swb` in your checkout. Copy `SWB_SETUP_CODE`. Delete the file after sign-in.

## Koyeb

Sign in with the [Koyeb command line tool](https://www.koyeb.com/docs/build-and-deploy/cli/installation). Run this from the published SwB branch you want to deploy. Replace `my-swb` with your app name.

```sh
sh deploy/koyeb.sh my-swb
```

The script uses a paid instance and a 1 GB volume in Washington, DC. Pass a branch and `fra` after the app name to use Frankfurt. Koyeb volumes are in preview.

Open `.env.swb-setup-koyeb-my-swb` in your checkout. Copy `SWB_SETUP_CODE`. Delete the file after sign-in.

## Docker Compose

Use a server with Docker Compose. Point a domain name at your server. Open ports 80 and 443. Replace `swb.example.com` with your domain in the command.

```sh
sh deploy/compose/start.sh swb.example.com
```

Copy `SWB_SETUP_CODE` from `deploy/compose/.env`. Caddy manages HTTPS. This setup also works on a DigitalOcean Droplet.
