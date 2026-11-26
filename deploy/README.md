# Deploy Switchboard

Switchboard runs on Railway, Render, or Fly.io from the root Dockerfile. Use one replica with a persistent volume at `/data` so connections, memory, and grants survive restarts and redeploys. A second replica with a separate disk won't share this state. The source-built image initializes fresh mounts and then runs the server as UID `10001`; no custom start command is needed. Back up the volume before upgrades.

## Environment variables

| Variable | Value |
| --- | --- |
| `SWITCHBOARD_DATA_DIR` | `/data`. The included templates use the equivalent `SWITCHBOARD_DATA` variable. Keep the path set to the persistent mount. |
| `SWITCHBOARD_PUBLIC_URL` | The service's external HTTPS origin, such as `https://switchboard.example.com`, without a path or trailing slash. |
| `SWITCHBOARD_KEY_OWNER` | A private, randomly generated owner bearer key. Use at least 32 characters and store it in the platform's secret settings and your password manager, not in source control. |
| `PORT` | The HTTP port on `0.0.0.0`. Use the platform-provided port on Railway and Render. Fly's `fly.toml` sets both the listening port and the proxy's internal port to `8000`. |
| `SWITCHBOARD_CONFIG` | Leave unset on an env-only deployment. Use it only to point an existing YAML-based installation at its mounted config file. |

No YAML config or Cloudflare credentials are needed for bearer access. For clients that support a custom HTTP header, set a separate secret `SWITCHBOARD_KEY_<NAME>` and connect to `https://YOUR-HOST/mcp` with `Authorization: Bearer <client-key>`. Keep the owner key for administration. For OAuth sign-in, follow the [owner sign-in configuration](../README.md#config-version-2); setting the public URL alone doesn't configure OAuth.

## Railway

For [Railway Infrastructure as Code](https://docs.railway.com/infrastructure-as-code), set the GitHub repository in [`.railway/railway.ts`](../.railway/railway.ts). With Node.js and the current Railway CLI installed, run `npm install --no-save --package-lock=false railway` in the repo, link a Railway project and environment with `railway link`, then review `railway config plan` and run `railway config apply`. This sets up the Docker service, one replica, a required volume at `/data`, and `/healthz`. In the dashboard, set the owner key, enable public networking, and set the public URL as in step 2 below. The IaC file preserves those two variables when you reapply it.

If you prefer the dashboard:

1. Create a service from the GitHub repository. Railway builds the root Dockerfile. Before deploying, [attach a volume](https://docs.railway.com/volumes) to the service at `/data`.
2. In the service's settings, enable public networking, generate a domain or add your own, and set the health check path to `/healthz`. Set `SWITCHBOARD_DATA=/data`, `SWITCHBOARD_PUBLIC_URL` to the resulting HTTPS origin, and `SWITCHBOARD_KEY_OWNER` to your secret. Leave `PORT` to Railway.
3. Deploy the staged changes. Keep the Dockerfile's default start command so it can prepare the Railway volume before starting Switchboard.

[`railway.json`](../railway.json) is a fallback only for services already using legacy [Config as Code](https://docs.railway.com/config-as-code). It sets the build, mount requirement, and health check; it can't create a volume. Attach the volume in the dashboard and set the variables above. Railway doesn't accept legacy Config as Code on new services and stops reading it on December 1, 2026. Don't use both config files for the same service.

For a one-click link, [create a Railway template](https://docs.railway.com/templates/create) from these service settings, including the volume, and copy its share URL. Railway's deploy button requires a template code; a GitHub URL isn't a substitute.

## Render

1. Create a [Blueprint](https://render.com/docs/infrastructure-as-code) from the repository, or use the Deploy to Render button. [`render.yaml`](../render.yaml) creates a paid Starter web service, a 1 GB disk at `/data`, and an HTTP check at `/healthz`. Render generates the owner key.
2. Fill in `SWITCHBOARD_PUBLIC_URL` when prompted. If the service URL isn't known yet, use an HTTPS placeholder, then set the actual `.onrender.com` or custom HTTPS origin in the service's Environment settings before connecting clients.
3. After changing the source or environment, check the service's deploy status. Automatic deploys on source pushes are off so an upstream push can't redeploy everyone's installation.

## Fly.io

1. [Install and sign in to the Fly CLI](https://docs.fly.io/getting-started/launch/). In [`fly.toml`](../fly.toml), choose a globally unique app name and a region. Set `SWITCHBOARD_PUBLIC_URL` to `https://YOUR-APP-NAME.fly.dev` or your custom HTTPS origin.
2. Create the app with `fly apps create YOUR-APP-NAME`. Add `SWITCHBOARD_KEY_OWNER` in the Fly dashboard's Secrets settings, or stage it with [`fly secrets import --stage`](https://docs.fly.io/flyctl/cmd/fly_secrets_import/) over standard input. Don't put the key in `fly.toml`.
3. Run `fly deploy --ha=false` from the repo root. It provisions a single Machine and a 1 GB volume in the app region on the first deploy. Don't scale out: Fly doesn't replicate data between volumes.

## Verify the deployment

Run `curl --fail --silent --show-error https://YOUR-HOST/healthz`. A successful response means the server is reachable; check the response for any degraded blocks. Then connect your MCP client and verify that its saved state remains after a redeploy.

If startup fails with a permission error after restoring a backup, check ownership of the restored files on the **mounted volume**. The startup process prepares the mount directory but doesn't change ownership of existing files. Restore them as UID/GID `10001` so the server can write to them. Don't make the volume world-writable.
