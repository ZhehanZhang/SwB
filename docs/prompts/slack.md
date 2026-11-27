Setup guide: https://swb.one/docs/setup/slack

Help me connect Slack to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, first help me add SwB as a custom MCP server here, at my SwB address such as https://swb.example.com/mcp. In Claude, open Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a web browser, then + > Add custom MCP server. In Grok, open grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait until I confirm.

Call connect(what="slack"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path, and wait for me to say done. Then call connect(what="slack") again to check it.

My Slack steps, in order: open https://api.slack.com/apps > Create New App > From a manifest, pick the workspace > Next. Select YAML and paste manifests/workspace-a-agent.yaml from the SwB repo (bot plus my account) or manifests/workspace-b-user-only.yaml (my account only), then Next > Create. Open Basic Information > App-Level Tokens > Generate Token and Scopes, add connections:write, then Generate. Open Install App > Install to Workspace > Allow. The xapp- token is under Basic Information > App-Level Tokens. The xoxb- and xoxp- tokens are under OAuth & Permissions > OAuth Tokens. Then I send /invite @<bot> in each channel. The workspace ID is the T... part of app.slack.com/client/T.../ in a browser.

Add the workspace under blocks.slack.workspaces in config/switchboard.yaml, or give me the YAML. Tell me when SwB needs a restart. Check identities with slack_workspaces.

Ask me for tokens only through the claim link that connect returns, and keep them out of chat.

When connect reports connected, send a test message with slack_post and show me the reply. Last, subscribe to SwB events: use switchboard.message.created if this app supports MCP events, else call wait or inbox to collect new messages.
