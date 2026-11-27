Setup guide: https://swb.one/docs/blocks/discord

Help me connect Discord to SwB. SwB is my switchboard for shared memory and agent handoffs, and you reach it as an MCP server.

If the SwB tools are missing in this app, first walk me through adding SwB as a custom MCP server here. Use my SwB address, for example https://swb.example.com/mcp, and wait until I confirm.

I create a bot in the Discord Developer Portal, turn on the Message Content intent, copy the bot token, and invite the bot with the link that connect gives you. Give me the exact pages and buttons.

Call connect(what="discord") and read the steps it returns. Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path and links. After each step, wait for me to say done. Then call connect(what="discord") again to check it. Ask me for tokens only through the claim link that connect returns, and keep them out of chat.

When connect reports done, send a test message and show me the reply. Last, make sure new work reaches you. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise call wait to collect new messages.
