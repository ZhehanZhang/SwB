Setup guide: https://swb.one/docs/setup/telegram

Help me connect Telegram to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, help me add SwB as a custom MCP server. Use my SwB address, such as https://swb.example.com/mcp. In Claude, use Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a browser. Use + > Add custom MCP server. In Grok, use grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait for me to confirm.

Call connect(what="telegram"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="telegram") again to check the connection.

I open https://t.me/BotFather. I tap Start. I send `/newbot`. I send a display name. I send a username that ends in bot. BotFather replies with the token. I open the bot chat. I tap Start. I send one message.

For a group, I open `setup.add_to_group_url` from telegram_status. I pick the group. I send `/swb@<bot username>` there. To let the bot read all group messages, I send `/setprivacy` to BotFather. I pick the bot. I choose Disable. I remove the bot from the group. I add the bot to the group again.

Read the chat IDs in `setup.seen_chats` from telegram_status. Add a space under `blocks.telegram.spaces` in `config/switchboard.yaml`. Give the space `chats` and `owner_ids`. If I need to edit the file myself, give me the YAML.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

When connect reports connected, send a test message with telegram_post. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
