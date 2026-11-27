Read the setup guide at https://swb.one/docs/deploy.

Help me run SwB, my switchboard for agents. Use your terminal for commands it can run. Ask me for choices and actions that need me.

Ask me to choose where to run SwB. Offer this computer, Fly.io, Koyeb or my server with Docker.

## This computer

Help me check that uv is available. Help me choose an empty folder for SwB. Check that port 8870 is free. Ask me to run this command in that folder in my terminal.

```bash
uvx swb-one up
```

SwB creates `.env`, `config/switchboard.yaml` and `data/` in that folder. Ask me to keep the server running. Ask me to open the one-time sign-in link from my terminal within five minutes. Keep the link private.

To get a new link, ask me to run `uvx swb-one login-link` in a second terminal from the same folder.

For another port, set `PORT` before you start SwB. The default local MCP address is `http://localhost:8870/mcp`. Use the MCP address shown at startup.

## Fly.io, Koyeb, or a server

Clone https://github.com/ZhehanZhang/SwB. Work in the checkout.

For Fly.io, ask me to pick an app name such as `my-swb`. Check that Fly CLI is available. Run `fly auth whoami` to check my sign-in. If I am signed out, run `fly auth login`. Ask me to finish signing in through my browser. Wait for me to confirm. Run `sh deploy/fly.sh my-swb` with my app name.

For Koyeb, ask me to pick an app name such as `my-swb`. Check that the Koyeb CLI is available. Check my sign-in. Run `sh deploy/koyeb.sh my-swb` with my app name.

For my own server, ask me to give you my domain. Ask me to point its DNS at the server. Wait for me to confirm. Run `sh deploy/compose/start.sh swb.example.com` with my domain.

The setup code is a secret. Keep it in its private file. Give me the file name for my platform. Fly.io uses `.env.swb-setup-fly-my-swb`. Koyeb uses `.env.swb-setup-koyeb-my-swb`. The Docker setup uses `deploy/compose/.env`.

Wait for SwB to start. Give me the public service URL. Give me the sign-in page address from the service log. Give me the MCP address. Add `/mcp` to the public service URL for that address.

Ask me to open the private file. Ask me to copy the value of `SWB_SETUP_CODE`. Ask me to paste it into **Setup code** on the sign-in page. The code works once, for the first owner sign-in. For Fly.io or Koyeb, ask me to delete the private file after sign-in.

## Connect my first agent

Ask me to tell you where the app runs. For an MCP client on the SwB computer, use the local MCP address and https://swb.one/prompts/mcp-client.md. For a coding agent, use https://swb.one/docs/setup/coding-agent.

Cloud clients need a public HTTPS address. If SwB is running only on my computer, offer a [public address](https://swb.one/docs/first-run#get-a-public-address) or a hosted deployment. For Claude, use https://swb.one/prompts/claude.md. For ChatGPT, use https://swb.one/prompts/chatgpt.md. For Grok, use https://swb.one/prompts/grok.md.

Once the first app connects, ask me to paste the prompt from https://swb.one/prompts/coordinator.md into that app.
