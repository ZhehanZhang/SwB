Setup guide: https://swb.one/docs/setup/grok

Help me connect Grok on grok.com to SwB. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

First check your tools. If the SwB tools such as connect are missing here, guide me to add SwB as a custom connector. The path is https://grok.com/connectors, then New Connector, then Custom. I enter my SwB address, for example https://swb.example.com/mcp, and sign in to SwB with OAuth as the owner. On Grok Business, a team admin first adds it at https://console.x.ai under the team, then Grok Business, Connectors, + Add Connector, Other. Then I open a new chat and paste this prompt again.

Call connect(what="grok"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path and links. Wait for me to say done after each step. Then call connect(what="grok") again to check it.

Ask me for a token only through the SwB claim link that connect returns. Keep tokens out of this chat and out of tool arguments.

When connect reports that the client is signed in, send a test message with send(to="agent:grok", task="SwB setup test: reply pong"). Read it with inbox and show me the result.

Last, make sure new work reaches you. If this app supports MCP events, subscribe to switchboard.message.created for the grok mailbox. Otherwise, call wait or inbox at the start of each chat to collect new messages. Read each message with get_message and answer with reply(conversation=..., text=..., final=true).

Grok Bot is a separate app. If I need work to start by itself, point me to https://swb.one/docs/setup/grok-bot.
