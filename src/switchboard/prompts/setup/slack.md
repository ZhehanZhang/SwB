Read the setup guide at https://swb.one/docs/setup/slack.

Help me connect Slack to SwB. SwB carries messages between my agents and channels.

If the SwB tools are missing, ask me to add SwB as a custom MCP server. Follow https://swb.one/docs/connect to add the client.

Use a SwB client with owner access.

Call `connect(what="slack")`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step.

Ask me to open https://api.slack.com/apps. Use `Create New App > From a manifest`. A manifest is a file that sets the app's permissions and features.

For both identities, use https://swb.one/docs/setup/slack#bot-and-your-account. For my account only, use https://swb.one/docs/setup/slack#your-account-only. Ask me to paste the manifest from that section.

Follow the setup guide to make an app-level token with `connections:write`. Ask me to install the app. Ask me to find the `xapp-` token and the `xoxp-` user token. If I chose both identities, also ask me to find the `xoxb-` bot token. Ask me to invite that bot to each channel with `/invite @<bot>`. Find the workspace ID in the Slack URL, such as `https://app.slack.com/client/T12345/`.

Add the workspace under `blocks.slack.workspaces` in `config/switchboard.yaml`. Use the selected identities in the workspace settings. Call `switchboard_status` to get your agent name from `you.agent`. Before the first restart, grant that agent access under `blocks.slack.agents`.

```yaml
blocks:
  slack:
    agents:
      claude:
        workspaces:
          example: [bot, user]
```

Replace `claude` with your agent name. Replace `example` with the workspace label. Use the identities set up for that workspace. For my account only, use `[user]`. If the file needs a manual edit, give me the YAML. Ask me to apply it. Ask me to restart SwB when needed. Check identities with `slack_workspaces`.

After the restart, call `connect(what="slack")` again. Ask me to save the required tokens only through the SwB claim link. Restart SwB if `connect` asks you to. Call `connect` again to check the connection.

When `connect` reports `connected`, check access with `slack_workspaces`. Ask me to choose a test channel. Call `slack_post` with the workspace, channel, test text and chosen identity. Show me whether SwB sent the message or queued it for approval.

Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
