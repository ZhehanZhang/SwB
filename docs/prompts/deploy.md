Read the setup guide at https://swb.one/docs/deploy.

Help me run SwB. Use your terminal where you can. Ask for choices and actions that need me.

Ask me to choose where to run SwB. Offer this computer, Fly.io, Koyeb or my server with Docker.

## This computer

If needed, install uv using the [official guide](https://docs.astral.sh/uv/getting-started/installation/). SwB needs Python 3.12 or later. The command below downloads Python 3.12 when needed.

Choose an empty folder with me. Check that port 8870 is free. To choose another port, use a command such as `PORT=8871 uvx swb-one up`.

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

After a restart, use `login-link` to get a fresh link. Run `uvx --python 3.12 swb-one login-link` from the same folder in another terminal. Save its output to a new private file. If I run the command, ask me to open the link there. Keep links out of chat and shared output.

Use `http://localhost:8870/mcp` with the chosen port.

## Fly.io, Koyeb, or a server

Clone https://github.com/ZhehanZhang/SwB. Run these commands in the checkout.

For Fly.io, ask for an app name. Check the Fly CLI. Run `fly auth whoami`. If needed, run `fly auth login` and wait for me to sign in. Run `sh deploy/fly.sh my-swb` with my app name.

For Koyeb, ask for an app name. Check the Koyeb CLI and my sign-in. Run `sh deploy/koyeb.sh my-swb` with my app name.

For my own server, ask for my domain. Ask me to point its DNS at the server. Wait for me to confirm. Run `sh deploy/compose/start.sh swb.example.com` with my domain.

Keep the setup code in its private file. Give me the file path. Fly.io uses `.env.swb-setup-fly-my-swb`. Koyeb uses `.env.swb-setup-koyeb-my-swb`. Docker uses `deploy/compose/.env`.

Wait for SwB to start. Give me the public URL and the sign-in page address from the log. Add `/mcp` to the public URL for the MCP address.

Ask me to copy `SWB_SETUP_CODE` from the private file. Ask me to paste it into **Setup code** on the sign-in page. The code works once. For Fly.io or Koyeb, ask me to delete the private file after sign-in.

## Connect my first agent

Ask me where the app runs. To let the first agent set up other apps, give it owner access. For a local owner client, follow https://swb.one/prompts/mcp-client.md. For a coding agent, use https://swb.one/docs/setup/coding-agent.

For a hosted app, have me approve its OAuth sign-in to give it owner access. Cloud clients need a public HTTPS address. If SwB is local, offer a [public address](https://swb.one/docs/first-run#get-a-public-address) or a hosted deployment. For Claude, use https://swb.one/prompts/claude.md. For ChatGPT, use https://swb.one/prompts/chatgpt.md. For Grok, use https://swb.one/prompts/grok.md.

Once the first app connects, ask me to paste https://swb.one/prompts/coordinator.md into that app. If it uses a paired key, have it give me the matching `swb connect` command for each owner-only connection.
