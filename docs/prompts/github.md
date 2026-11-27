Setup guide: https://swb.one/docs/setup/github

Help me connect GitHub to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, first help me add SwB as a custom MCP server here, at my SwB address such as https://swb.example.com/mcp. In Claude, open Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a web browser, then + > Add custom MCP server. In Grok, open grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait until I confirm.

Call connect(what="github"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path, and wait for me to say done. Then call connect(what="github") again to check it.

My GitHub steps, in order: profile picture > Settings > Developer settings > GitHub Apps > New GitHub App. Enter GitHub App name and Homepage URL. Under Webhook keep Active, set Webhook URL to my SwB address plus /github/webhook, and enter a 64 character random Webhook secret from my password manager. Under Repository permissions, set Issues and Pull requests to Read & write, and Contents, Checks and Actions to Read-only. Subscribe to issues, issue_comment, pull_request, pull_request_review, pull_request_review_comment, pull_request_review_thread, push, check_run, check_suite and workflow_run. Select Only on this account > Create GitHub App. Under Private keys, Generate a private key. Then Install App > Install > Only select repositories > Install.

For agent writes, call connect(what="github", choose={"credential":"app"}) so I save the App ID and the .pem file. Add the repositories under blocks.github.spaces or blocks.github.routes in config/switchboard.yaml, or give me the YAML. Check with github_status. Ask me to look at Advanced > Recent deliveries in the app settings.

Ask me for tokens only through the claim link that connect returns, and keep them out of chat.

When connect reports connected, send a test message and show me the reply. Last, subscribe to SwB events: use switchboard.message.created if this app supports MCP events, else call wait or inbox to collect new messages.
