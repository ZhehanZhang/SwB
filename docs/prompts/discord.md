Read the setup guide at https://swb.one/docs/setup/discord.

Help me connect Discord to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing, ask me to add SwB as a custom MCP server. Ask me to enter my SwB MCP address. Use `https://swb.example.com/mcp` as the example.

For Claude, give me the path `Customize > Connectors > Add > Custom > Web`. For ChatGPT, ask me to open `https://chatgpt.com/plugins`. Give me the path `+ > Add custom MCP server`. For Grok, give me the path `grok.com > Connectors > New Connector > Custom`. For other apps, ask me to open the MCP server settings. Wait for me to confirm.

Call connect(what="discord"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="discord") again to check the connection.

Ask me to open https://discord.com/developers/applications. Ask me to click New Application. Ask me to enter a name. Ask me to click Create. On Bot, ask me to click Reset Token. Discord shows the token once. Ask me to click Copy. On Bot, ask me to turn on Message Content Intent under Privileged Gateway Intents. Ask me to save the change. Ask me to open the invite link in `setup.invite_url` from discord_status. Ask me to pick my server. Ask me to authorize the app.

Add a space under `blocks.discord.spaces` in `config/switchboard.yaml`. Grant agents access in `blocks.discord.agents`. If the file needs a manual edit, give me the YAML. Ask me to apply it. Check the setup with discord_status. Gateway code 4014 means Message Content Intent is off. Code 4004 means Discord refused the token.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

When connect reports connected, send a test message with discord_post. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
