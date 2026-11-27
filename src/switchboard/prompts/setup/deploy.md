Follow the deploy guide at https://swb.one/docs/deploy.

Help me run SwB, my switchboard for agents. Use your terminal for commands it can run. Ask me for choices and actions that need me.

Ask where to run SwB. Offer this computer, Fly.io, Koyeb or my server with Docker.

## This computer

Run the local commands on the computer that will run SwB. If I have that terminal, give me the commands to run there.

Check that uv is available. Make an empty instance folder. Check that port 8870 is free. Run the following commands in that folder. Keep the server running.

```bash
uvx swb-one init --public-url http://localhost:8870
uvx swb-one up --dir .
```

`swb init` creates a private `data/` folder with mode `0700`. An initialized instance or `--dir` uses the instance's `data/` folder by default. Outside an instance, `swb up` defaults to `~/.local/share/switchboard`.

SwB checks for `data_dir` in the YAML settings and their overlay first. Then it checks `SWITCHBOARD_DATA_DIR`, then `SWITCHBOARD_DATA`. The default folder applies last.

For an older instance with a missing `data/` folder, plain `swb up` reuses an existing home database. SwB shows a notice once. Database files use mode `0600`.

SwB uses the config file named by `SWITCHBOARD_CONFIG`. With `--dir`, it uses the config file from that instance instead.

In a second terminal, use the same folder to create a one-time owner sign-in link in a private file.

```bash
(umask 077; set -C; uvx swb-one login-link > .swb-login-link)
```

Tell me the file path. Ask me to open the link in my local browser within five minutes. Ask me to delete the file after sign-in.

For another port, set `PORT` in the instance's `.env` file. Use that port in the `--public-url` value when you run `init`. Use it in the local MCP address.

## Fly.io, Koyeb, or a server

Clone https://github.com/ZhehanZhang/SwB. Work in the checkout.

For Fly.io, ask me for an app name such as `my-swb`. Check that Fly CLI is available. Run `fly auth whoami` to check my sign-in. If I am signed out, run `fly auth login`. Wait for me to finish in my browser. Run `sh deploy/fly.sh my-swb` with my app name.

For Koyeb, ask me for an app name such as `my-swb`. Check that the Koyeb CLI is available. Check my sign-in. Run `sh deploy/koyeb.sh my-swb` with my app name.

For my own server, ask for my domain. Ask me to point its DNS at the server. Wait for me to confirm. Run `sh deploy/compose/start.sh swb.example.com` with my domain.

The setup code is a secret. Keep it in its private file. Give me the file name for my platform. Fly.io uses `.env.swb-setup-fly-my-swb`. Koyeb uses `.env.swb-setup-koyeb-my-swb`. The Docker setup uses `deploy/compose/.env`.

Wait for SwB to start. Give me the public service URL. Give me the sign-in page address from the service log. Give me the MCP address. Add `/mcp` to the public service URL for that address.

Tell me to open the private file. Tell me to copy the value of `SWB_SETUP_CODE`. Tell me to paste it into **Setup code** on the sign-in page. The code works once, for the first owner sign-in. For Fly.io or Koyeb, tell me to delete the private file after sign-in.

## Connect my first agent

Ask where the app runs. For an MCP client on the SwB computer, use the local MCP address and https://swb.one/prompts/mcp-client.md. For a coding agent, use https://swb.one/docs/setup/coding-agent.

Cloud clients need a public HTTPS address. If SwB is running only on my computer, offer a [public address](https://swb.one/docs/first-run#get-a-public-address) or a hosted deployment. For Claude, use https://swb.one/prompts/claude.md. For ChatGPT, use https://swb.one/prompts/chatgpt.md. For Grok, use https://swb.one/prompts/grok.md.

Once the first app connects, ask me to paste the prompt from https://swb.one/prompts/coordinator.md into that app.
