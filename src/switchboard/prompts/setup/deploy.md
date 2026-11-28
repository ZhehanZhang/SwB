Read the setup guide at https://swb.one/docs/deploy.

Help me run SwB. Use your terminal for commands you can run. Ask me to make choices and to take actions that need my account.

Send a `User-Agent` header that names your app in each HTTP request to SwB.

Ask me to choose where to run SwB. Offer this computer, Render, Railway, Fly.io, Koyeb or my server with Docker.

## This computer

Use this section after the PyPI release. For a source build, use **Build from source**. Install uv using the [official guide](https://docs.astral.sh/uv/getting-started/installation/). SwB needs Python 3.12 or later. The command below uses Python 3.12.

Choose an empty folder with me. Check that port 8870 is free. To choose another port, use a command such as `SWB_PORT=8871 uvx --python 3.12 swb-one up`.

If your terminal is on my SwB computer, run this in the chosen folder. Keep SwB running.

```bash
(umask 077; log=$(mktemp .swb-startup.XXXXXXXX) || exit
printf 'Private startup log: %s/%s\n' "$PWD" "$log"
uvx --python 3.12 swb-one up > "$log")
```

Use a fresh file for each start. Use file mode `0600` for each private link file. Use equivalent protection in other shells. Give me the file path.

Tell me how to read the link in my private terminal. Give me this command with the actual file path.

```bash
grep -F '/login#t=' '/path/to/.swb-startup.ABC12345'
```

If I start SwB in my terminal, give me the `uvx` command by itself. SwB prints the first link when it starts.

Ask me to open the `/login#t=` link within five minutes. Ask me to delete any private link file after sign-in.

For later sign-ins, ask me to use a passkey or get a fresh link. To get a link, run `uvx --python 3.12 swb-one login-link` from the same folder in another terminal. Save its output to a new private file. If I run the command, ask me to open the link there. Keep links out of chat and shared output.

Use `http://localhost:8870/mcp` with the chosen port.

## Build from source

Use the release branch for a source build. Run these commands on the computer you will use to build or deploy SwB.

```bash
git clone --branch release/1.0 https://github.com/ZhehanZhang/SwB.git
cd SwB
```

To run SwB on this computer, follow https://swb.one/docs/self-hosting#docker-compose. Start with the command that copies `.env.example`.

## Render and Railway

Read the deployment guide at https://github.com/ZhehanZhang/SwB/tree/release/1.0/deploy. Guide me through the Render Blueprint or Railway service setup. Use `release/1.0` as the source branch. Use one instance with one 1 GB volume at `/data`. On Render, use a Hobby workspace and the 512 MB service. On Railway, use the Hobby plan and set the variables from the guide, including a private `SWB_SETUP_CODE`. Ask me to copy `SWB_SETUP_CODE` from the service settings. Keep the code out of chat and logs.

## Fly.io, Koyeb and Docker

Run these commands in the `release/1.0` checkout.

For Fly.io, ask for an app name. Check the Fly CLI and Python 3.11 or later, or uv. Run `fly auth whoami`. If needed, run `fly auth login` and wait for me to sign in. Use my personal organization for the Fly.io Free plan. Run `sh deploy/fly.sh my-swb` with my app name. Stop if the script reports a resource outside the supported setup.

For Koyeb, ask for an app name. Check the Koyeb CLI and my sign-in. Run `sh deploy/koyeb.sh my-swb release/1.0` with my app name.

For my own server, ask for my domain. Ask me to point its DNS at the server. Ask me to open ports 80 and 443. Wait for me to confirm. Run `sh deploy/compose/start.sh swb.example.com` on the server with my domain.

Keep the setup code in its private file. Give me the file path. Fly.io uses `.env.swb-setup-fly-my-swb`. Koyeb uses `.env.swb-setup-koyeb-my-swb`. Docker uses `deploy/compose/.env`. Ask me to copy `SWB_SETUP_CODE` from that file.

## Sign in on a server

Wait for SwB to start. Give me the public URL and the sign-in page address from the log. Add `/mcp` to the public URL for the MCP address.

Ask me to paste the code into **Setup code** on the sign-in page. The code works once. Ask me to add a passkey to sign in again. For Fly.io or Koyeb, ask me to delete the private file after sign-in.

## Connect my first agent

Ask me where the app runs. To let the first agent set up other apps, give it owner access. For a local owner client, follow https://swb.one/prompts/mcp-client.md. For a coding agent, use https://swb.one/docs/setup/coding-agent.

For a hosted app, have me approve its OAuth sign-in to give it owner access. Use a public HTTPS address. If SwB is local, offer a [public address](https://swb.one/docs/first-run#get-a-public-address) or a hosted deployment. For Claude, use https://swb.one/prompts/claude.md. For ChatGPT, use https://swb.one/prompts/chatgpt.md. For Grok, use https://swb.one/prompts/grok.md.

Once the first app connects, ask me to paste https://swb.one/prompts/coordinator.md into that app. If it uses a paired key, have it give me the matching `swb connect` command for each owner-only connection.

If you keep your own memory, set up memory sync as described at https://swb.one/docs/memory.
