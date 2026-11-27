Setup guide: https://swb.one/docs/setup/discord

Help me connect Discord to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, first help me add SwB as a custom MCP server here, at my SwB address such as https://swb.example.com/mcp. In Claude, open Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a web browser, then + > Add custom MCP server. In Grok, open grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait until I confirm.

Call connect(what="discord"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path, and wait for me to say done. Then call connect(what="discord") again to check it.

My Discord steps, in order: open https://discord.com/developers/applications > New Application, enter a name > Create. Open Bot > Reset Token > Copy (Discord shows it once). On Bot, turn on Message Content Intent under Privileged Gateway Intents and save. Then I open the invite link from discord_status setup.invite_url, pick my server and authorize.

Add a space under blocks.discord.spaces in config/switchboard.yaml and grant agents in blocks.discord.agents, or give me the YAML. Check with discord_status. Gateway code 4014 means Message Content Intent is off. Code 4004 means Discord refused the token.

Ask me for tokens only through the claim link that connect returns, and keep them out of chat.

When connect reports connected, send a test message with discord_post and show me the reply. Last, subscribe to SwB events: use switchboard.message.created if this app supports MCP events, else call wait or inbox to collect new messages.
