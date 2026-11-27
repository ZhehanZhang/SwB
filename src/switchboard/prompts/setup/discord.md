Read the setup guide at https://swb.one/docs/setup/discord.

Help me connect Discord to SwB. SwB carries messages between my agents and channels.

If the SwB tools are missing, ask me to add SwB as a custom MCP server. Follow https://swb.one/docs/connect to add the client.

For Claude, give me the path `Customize > Connectors > Add > Custom > Web`. For ChatGPT, ask me to open `https://chatgpt.com/plugins`. Give me the path `+ > Add custom MCP server`. For Grok, give me the path `grok.com > Connectors > New Connector > Custom`. For other apps, ask me to open the MCP server settings. Wait for me to confirm.

Use a SwB client with owner access.

Call `connect(what="discord")`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step.

Ask me to create an app at https://discord.com/developers/applications. Under **Bot**, ask me to generate its token. Ask me to turn on **Message Content Intent**. Ask me to enter the token only through the claim link from `connect`.

Add a space under `blocks.discord.spaces` in `config/switchboard.yaml`. Set `guild_id` to my Discord server ID and `owner_ids` to my user IDs. Grant the test agent access to the space in `blocks.discord.agents`. If the file needs a manual edit, give me the YAML. Restart SwB after an edit.

Read the invite link from `setup.invite_url` in `discord_status`. If the link is missing, set `blocks.discord.application_id` or wait for the bot to connect. Ask me to open the link and add the bot to my server. Call `connect` again to check the connection.

If Discord reports code `4014`, check **Message Content Intent**. If it reports code `4004`, replace the rejected token. When `connect` reports `connected`, ask me to choose a test channel. Call `discord_post` with the space label, channel ID and test text. Show me the result.

Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
