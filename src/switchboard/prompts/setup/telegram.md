Setup guide: https://swb.one/docs/setup/telegram

Help me connect Telegram to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, first help me add SwB as a custom MCP server here, at my SwB address such as https://swb.example.com/mcp. In Claude, open Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a web browser, then + > Add custom MCP server. In Grok, open grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait until I confirm.

Call connect(what="telegram"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path, and wait for me to say done. Then call connect(what="telegram") again to check it.

My Telegram steps, in order: open https://t.me/BotFather > Start, send /newbot, then a display name, then a username that ends in bot. BotFather replies with the token. Then I open the bot chat, tap Start and send one message. For a group, I open setup.add_to_group_url from telegram_status, pick the group, and send /swb@<bot username> there. To let the bot read all group messages, I send /setprivacy to BotFather, pick the bot, choose Disable, then remove and re-add the bot.

Read the chat IDs from telegram_status setup.seen_chats. Add a space under blocks.telegram.spaces in config/switchboard.yaml with chats and owner_ids, or give me the YAML.

Ask me for tokens only through the claim link that connect returns, and keep them out of chat.

When connect reports connected, send a test message with telegram_post and show me the reply. Last, subscribe to SwB events: use switchboard.message.created if this app supports MCP events, else call wait or inbox to collect new messages.
