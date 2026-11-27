Setup guide: https://swb.one/docs/deploy

Help me run SwB, my self-hosted switchboard for agents. Use your terminal for commands. Ask me only for choices and steps that need me.

Ask where I want to run SwB. Offer this computer, Fly.io, Koyeb, or my own server with Docker.

## This computer

Check that uv is available. Use an empty instance folder. Check that port 8870 is free. Run `uvx swb-one init --public-url http://localhost:8870`. Run `echo "SWITCHBOARD_DATA_DIR=$PWD/data" >> .env` to keep the data in this folder. Start SwB with `uvx swb-one up`. Keep the server running. In a second terminal, run `(umask 077; set -C; uvx swb-one login-link > .swb-login-link)` from the same folder. The command writes a private file with a one-time owner sign-in link. Tell me the file path. Ask me to open the link in my local browser within five minutes. Ask me to delete the file after sign-in.

For a different port, use that port in `--public-url` when you run `init`. Set `PORT` in the instance's `.env` file. Use the same port in `--url` if you need to point `login-link` at that server. Use the MCP address that `up` prints for a client on this computer.

## Fly.io, Koyeb, or a server

Clone https://github.com/ZhehanZhang/SwB. Work in the checkout.

For Fly.io, ask me for an app name such as `my-swb`. Check that Fly CLI is available. Run `fly auth whoami` to check my sign-in. If I am signed out, run `fly auth login`. Wait for me to finish in my browser. Run `sh deploy/fly.sh my-swb` with my app name.

For Koyeb, ask me for an app name such as `my-swb`. Check that the Koyeb CLI is available. Check my sign-in. Run `sh deploy/koyeb.sh my-swb` with my app name.

For my own server, ask for my domain. Ask me to point its DNS at the server. Wait for me to confirm. Run `sh deploy/compose/start.sh swb.example.com` with my domain.

The setup code is a secret. Keep it in its private file. Give me the file name for my platform. Fly.io uses `.env.swb-setup-fly-my-swb`. Koyeb uses `.env.swb-setup-koyeb-my-swb`. The Docker setup uses `deploy/compose/.env`.

Wait for SwB to start. Give me the public service URL. Give me the sign-in page address from the service log. Give me the MCP address. Add `/mcp` to the public service URL for that address.

Tell me to open the private file. Tell me to copy the value of `SWB_SETUP_CODE`. Tell me to paste it into **Setup code** on the sign-in page. The code works once, for the first owner sign-in. For Fly.io or Koyeb, tell me to delete the private file after sign-in.

## Connect an agent

After I sign in, help me add SwB to one agent. Use https://swb.one/docs/connect. Then ask me to paste the coordinator prompt from https://swb.one/docs into that agent.
