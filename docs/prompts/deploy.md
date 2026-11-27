Setup guide: https://swb.one/docs/deploy

Help me deploy SwB, my self-hosted switchboard for agents. You are a coding agent with a terminal. Run every command yourself and tell me only what I must click or decide.

First ask me which platform I want: Fly.io, Koyeb, or my own server with Docker. Then clone https://github.com/ZhehanZhang/SwB and work in that checkout. Ask me for an app name, for example my-swb.

For Fly.io, check that flyctl is installed and that I am signed in with fly auth whoami. If I am signed out, run fly auth login and wait while I finish it in my browser. Then run sh deploy/fly.sh my-swb with my app name.

For Koyeb, check that the koyeb CLI is installed and signed in. Then run sh deploy/koyeb.sh my-swb with my app name.

For my own server, ask me for the domain. Ask me to point its DNS at the server and wait until I confirm. In deploy/compose, write a private .env file with DOMAIN set to my domain and SWB_SETUP_CODE set to the output of openssl rand -hex 32. Use umask 077 so only I can read it. Then run docker compose up -d --build in deploy/compose.

The setup code is a secret. Keep it out of this chat, your output and every log. Tell me only the name of the private file that holds it: .env.swb-setup-fly-my-swb for Fly.io, .env.swb-setup-koyeb-my-swb for Koyeb, or deploy/compose/.env for my server.

When the service runs, give me its public URL, the sign-in page address from the service log, and the MCP address, which is the URL with /mcp at the end. Tell me to open the file, copy the value of SWB_SETUP_CODE, and enter it in Setup code on the sign-in page. Then tell me to delete the Fly.io or Koyeb file.

Last, tell me to paste the coordinator prompt from https://swb.one/docs into the agent I use, so it can connect my other apps.
