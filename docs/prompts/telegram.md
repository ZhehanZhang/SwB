Read the setup guide at https://swb.one/docs/setup/telegram.

Help me connect Telegram to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing, ask me to add SwB as a custom MCP server. Ask me to enter my SwB MCP address. Use `https://swb.example.com/mcp` as the example.

For Claude, give me the path `Customize > Connectors > Add > Custom > Web`. For ChatGPT, ask me to open `https://chatgpt.com/plugins`. Give me the path `+ > Add custom MCP server`. For Grok, give me the path `grok.com > Connectors > New Connector > Custom`. For other apps, ask me to open the MCP server settings. Wait for me to confirm.

Call connect(what="telegram"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="telegram") again to check the connection.

Ask me to open https://t.me/BotFather. Ask me to tap Start. Ask me to send `/newbot`. Ask me to send a display name. Ask me to send a username that ends in bot. BotFather replies with the token. Ask me to open the bot chat. Ask me to tap Start. Ask me to send one message.

For a group, ask me to open `setup.add_to_group_url` from telegram_status. Ask me to pick the group. Ask me to send `/swb@<bot username>` there. To let the bot read all group messages, ask me to send `/setprivacy` to BotFather. Ask me to pick the bot. Ask me to choose Disable. Ask me to remove the bot from the group. Ask me to add the bot to the group again.

Read the chat IDs in `setup.seen_chats` from telegram_status. Add a space under `blocks.telegram.spaces` in `config/switchboard.yaml`. Give the space `chats` and `owner_ids`. If the file needs a manual edit, give me the YAML. Ask me to apply it.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

When connect reports connected, send a test message with telegram_post. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
