Setup guide: https://swb.one/docs/deploy

Help me deploy SwB, my self-hosted switchboard for agents. You are a coding agent with a terminal. Run every command yourself and tell me only what I must click or decide.

First ask me which platform I want: Fly.io or my own server with Docker. Then clone https://github.com/ZhehanZhang/SwB and work in that checkout.

For Fly.io, check that flyctl is installed and that I am signed in with fly auth whoami. If I am signed out, run fly auth login and wait while I finish it in my browser. Then run fly launch --copy-config --generate-name --ha=false --no-github-workflow.

For my own server, ask me for the domain. Ask me to point its DNS at the server and wait until I confirm. Set DOMAIN in deploy/compose, then run docker compose up -d --build in deploy/compose.

When the service runs, give me its public URL and the MCP address, which is the URL with /mcp at the end. Get the one-time sign-in link with swb login-link inside the running service, for example through fly ssh console. Show the link only to me, keep it out of logs, and tell me to open it to sign in.

Last, tell me to paste the coordinator prompt from https://swb.one/docs into the agent I use, so it can connect my other apps.
