Setup guide: https://swb.one/docs/setup/discord

Help me connect Discord to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, help me add SwB as a custom MCP server. Use my SwB address, such as https://swb.example.com/mcp. In Claude, use Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a browser. Use + > Add custom MCP server. In Grok, use grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait for me to confirm.

Call connect(what="discord"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="discord") again to check the connection.

I open https://discord.com/developers/applications. I click New Application. I enter a name. I click Create. On Bot, I click Reset Token. Discord shows the token once. I click Copy. On Bot, I turn on Message Content Intent under Privileged Gateway Intents. I save the change. I open the invite link in `setup.invite_url` from discord_status. I pick my server. I authorize the app.

Add a space under `blocks.discord.spaces` in `config/switchboard.yaml`. Grant agents access in `blocks.discord.agents`. If I need to edit the file myself, give me the YAML. Check the setup with discord_status. Gateway code 4014 means Message Content Intent is off. Code 4004 means Discord refused the token.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

When connect reports connected, send a test message with discord_post. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
