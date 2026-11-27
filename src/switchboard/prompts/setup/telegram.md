Read the setup guide at https://swb.one/docs/setup/telegram.

Help me connect Telegram to SwB. SwB carries messages between my agents and channels.

If the SwB tools are missing, ask me to add SwB as a custom MCP server. Follow https://swb.one/docs/connect to add the client.

For Claude, give me the path `Customize > Connectors > Add > Custom > Web`. For ChatGPT, ask me to open `https://chatgpt.com/plugins`. Give me the path `+ > Add custom MCP server`. For Grok, give me the path `grok.com > Connectors > New Connector > Custom`. For other apps, ask me to open the MCP server settings. Wait for me to confirm.

Use a SwB client with owner access.

Call `connect(what="telegram")`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step.

Ask me to open https://t.me/BotFather and send `/newbot`. Ask me to choose the bot's name and username. BotFather replies with the token. Ask me to enter it only through the claim link from `connect`. Then ask me to start a chat with the bot and send one message.

For a group, ask me to open `setup.add_to_group_url` from `telegram_status`. Ask me to select the group. Ask me to send `/swb@<bot username>` there. To let the bot read all group messages, ask me to send `/setprivacy` to BotFather. Ask me to choose the bot and select **Disable**. Ask me to remove the bot from the group. Ask me to add it again.

Use `telegram_status` to get the chat IDs from `setup.seen_chats` and my user ID from the direct message. Add a space under `blocks.telegram.spaces` in `config/switchboard.yaml`. Put the chat IDs in `chats` and my user ID in `owner_ids`. Grant the test agent access in `blocks.telegram.agents`. If the file needs a manual edit, give me the YAML. Restart SwB after an edit.

Call `connect(what="telegram")` again to check the connection. When it reports `connected`, ask me to choose a test chat. Call `telegram_post` with the space label, chat ID and test text. Show me the result.

Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.

If you keep your own memory, set up memory sync as described at https://swb.one/docs/memory.
