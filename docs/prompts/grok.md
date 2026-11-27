Setup guide: https://swb.one/docs/setup/grok

Help me connect Grok on grok.com to SwB. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, guide me to add SwB as a custom connector. On Grok Business, a team admin adds the connector first. The admin opens https://console.x.ai. Under the team, use Grok Business > Connectors > + Add Connector > Other.

I open https://grok.com/connectors. I use New Connector > Custom. I enter my SwB address, such as https://swb.example.com/mcp. I sign in to SwB with OAuth as the owner. I open a new chat. I paste this prompt again.

Call connect(what="grok"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call connect(what="grok") again to check the connection.

Ask me to enter a token only through the SwB claim link that connect returns. Keep tokens out of this chat and tool arguments.

When connect reports that the client is signed in, send a test message with `send(to="agent:grok", task="SwB setup test: reply pong")`. Read the message with inbox. Show me the result.

Subscribe to switchboard.message.created for the grok mailbox if this app supports MCP events. Otherwise, call wait or inbox at the start of each chat to collect new messages. Read each message with get_message. Answer with reply(conversation=..., text=..., final=true).

Grok Bot is a separate app. If I need work to start by itself, point me to https://swb.one/docs/setup/grok-bot.
