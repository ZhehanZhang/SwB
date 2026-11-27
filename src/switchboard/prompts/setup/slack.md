Setup guide https://swb.one/docs/setup/slack

Help me connect Slack to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, help me add SwB as a custom MCP server. Use my SwB address, such as https://swb.example.com/mcp. In Claude, use Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a browser. Use + > Add custom MCP server. In Grok, use grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait for me to confirm.

Call connect(what="slack"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="slack") again to check the connection.

I open https://api.slack.com/apps. I use Create New App > From a manifest. I pick the workspace. I click Next. I select YAML. For the bot and my account, I paste the YAML at https://swb.one/docs/setup/slack#bot-and-your-account. For my account only, I paste the YAML at https://swb.one/docs/setup/slack#your-account-only. I click Next. I click Create.

I use Basic Information > App-Level Tokens > Generate Token and Scopes. I add `connections:write`. I click Generate. I use Install App > Install to Workspace. I click Allow. I find the `xapp-` token at Basic Information > App-Level Tokens. I find the `xoxb-` and `xoxp-` tokens at OAuth & Permissions > OAuth Tokens. I send `/invite @<bot>` in each channel. The workspace ID is the `T...` part of `app.slack.com/client/T.../` in a browser.

Add the workspace under `blocks.slack.workspaces` in `config/switchboard.yaml`. Call `switchboard_status` to get your agent name. Before the first restart, add a grant for that name under `blocks.slack.agents`. The grant lets you call `slack_workspaces` and `slack_post`.

```yaml
blocks:
  slack:
    agents:
      claude:
        workspaces:
          example: [bot, user]
```

Replace `claude` with your agent name. Replace `example` with the workspace label. Add the same grant for `owner` if I use the `swb` command for Slack. Use the identities set up for that workspace. For my account only, use `[user]`. If I need to edit the file myself, give me the YAML. Tell me when SwB needs a restart. Check identities with `slack_workspaces`.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

When connect reports connected, send a test message with slack_post. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
