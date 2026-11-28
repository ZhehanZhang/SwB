# Deploy SwB

See [Deploy](https://swb.one/docs/deploy) to choose where to run SwB.

Use one instance with a volume at `/data`. A volume keeps your data when the instance restarts. [Back up the volume](https://swb.one/docs/operate#backups-and-upgrades) before an update.

These deployments build the repository Dockerfile. After the container release opens to the public, use `ghcr.io/zhehanzhang/swb:1.0.0` with a volume UID 10001 can write to.

## Sign in

Open your app. Copy the setup code from your hosting account or the private file named by your launch command. Enter it in **Setup code**. Add a passkey to sign in again.

Use the service log to find the sign-in page address. The setup code works once. If you need a new sign-in link, run `swb login-link` in the private service shell. Open the link within five minutes.

Use `/healthz` to check service health.

## Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ZhehanZhang/SwB/tree/release/1.0)

The [Blueprint](../render.yaml) creates one web service and one 1 GB disk. Use a Hobby workspace. The [512 MB service](https://render.com/docs/compute-plans) costs $7 per month. The [disk](https://render.com/pricing) adds $0.25 per month. Open **Environment** in the service. Copy `SWB_SETUP_CODE`.

## Railway

Use the [Railway template composer](https://docs.railway.com/templates/create) to publish the deploy button. Select the SwB release branch in `https://github.com/ZhehanZhang/SwB`. Use the root Dockerfile and one replica. Attach one 1 GB volume at `/data`. Set `/healthz` as the health check. Set `/data` as the required mount path. Enable public networking on port `8000`. Set **Serverless** to off. Use the [Hobby plan](https://docs.railway.com/pricing/plans). It costs $5 per month and includes $5 of usage.

Copy the [template variables](../.railway/template.env.example) into the template.

Open **Variables** in the service. Copy `SWB_SETUP_CODE`. After sign-in, seal or remove this variable.

[Railway infrastructure as code](../.railway/railway.ts) sets the service and volume. [Legacy Railway settings](../railway.json) apply to existing services until January 1, 2027.

## Fly.io

Install Python 3.11 or later, or install [uv](https://docs.astral.sh/uv/). Sign in with the [Fly command line tool](https://docs.fly.io/flyctl/). Select your personal organization for the [Free plan](https://docs.fly.io/about/free-plan). Run this in the SwB repository. Replace `my-swb` with a unique app name.

```sh
sh deploy/fly.sh my-swb
```

The script checks for one 512 MB Machine and one 1 GB volume in Ashburn. It checks for shared IPv4 and public IPv6. Open `.env.swb-setup-fly-my-swb` in your checkout. Copy `SWB_SETUP_CODE`. Delete the file after sign-in.

Back up your data before you update an existing app. Run the same script with the app name and `--existing` to keep your current setup code and keys.

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

For a public address through Cloudflare, see [Named tunnel](https://swb.one/docs/cloudflare#named-tunnel).
