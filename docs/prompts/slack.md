Read the setup guide at https://swb.one/docs/setup/slack.

Help me connect Slack to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing, ask me to add SwB as a custom MCP server. Ask me to enter my SwB MCP address. Use `https://swb.example.com/mcp` as the example.

For Claude, give me the path `Customize > Connectors > Add > Custom > Web`. For ChatGPT, ask me to open `https://chatgpt.com/plugins`. Give me the path `+ > Add custom MCP server`. For Grok, give me the path `grok.com > Connectors > New Connector > Custom`. For other apps, ask me to open the MCP server settings. Wait for me to confirm.

Call connect(what="slack"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="slack") again to check the connection.

Ask me to open `https://api.slack.com/apps`. Give me the path `Create New App > From a manifest`. Ask me to pick the workspace. Ask me to click Next. Ask me to select YAML.

For both identities, point me to `https://swb.one/docs/setup/slack#bot-and-your-account`. For my account only, point me to `https://swb.one/docs/setup/slack#your-account-only`. Ask me to paste the YAML from that section. Ask me to click Next. Ask me to click Create.

Ask me to use Basic Information > App-Level Tokens > Generate Token and Scopes. Ask me to add `connections:write`. Ask me to click Generate. Ask me to use Install App > Install to Workspace. Ask me to click Allow. Ask me to find the `xapp-` token at Basic Information > App-Level Tokens. Ask me to find the `xoxb-` and `xoxp-` tokens at OAuth & Permissions > OAuth Tokens. Ask me to send `/invite @<bot>` in each channel. The workspace ID is the `T...` part of `app.slack.com/client/T.../` in a browser.

Add the workspace under `blocks.slack.workspaces` in `config/switchboard.yaml`. Call `switchboard_status` to get your agent name. Before the first restart, add a grant for that name under `blocks.slack.agents`. The grant lets you call `slack_workspaces` and `slack_post`.

```yaml
blocks:
  slack:
    agents:
      claude:
        workspaces:
          example: [bot, user]
```

Replace `claude` with your agent name. Replace `example` with the workspace label. Use the identities set up for that workspace. For my account only, use `[user]`. If the file needs a manual edit, give me the YAML. Ask me to apply it. Ask me to restart SwB when needed. Check identities with `slack_workspaces`.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

When connect reports connected, send a test message with slack_post. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
