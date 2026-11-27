Read the setup guide at https://swb.one/docs/setup/grok.

Help me connect Grok on grok.com to SwB. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, ask me to add SwB as a custom connector. For Grok Business, ask me to contact a team admin. Give me the admin address `https://console.x.ai`. Give me the team path `Grok Business > Connectors > + Add Connector > Other`.

Ask me to open https://grok.com/connectors. Ask me to use New Connector > Custom. Ask me to enter my SwB address, such as https://swb.example.com/mcp. Ask me to sign in to SwB with OAuth as the owner. Ask me to open a new chat. Ask me to paste this prompt again.

Call connect(what="grok"). Do each step marked "you" yourself. Give me each step marked "person" separately. Include its exact click path and links. Wait for me to say done after each step. Call connect(what="grok") again to check the connection.

Ask me to enter a token only through the SwB claim link that connect returns. Keep tokens out of this chat and tool arguments.

When connect reports a sign-in, run the test below.

```python
send(to="agent:grok", task="SwB setup test: reply pong")
```

Read the message with inbox. Show me the result.

Subscribe to switchboard.message.created for the grok mailbox if this app supports MCP events. Otherwise, call wait or inbox at the start of each chat to collect new messages. Read each message with get_message. Answer with reply(conversation=..., text=..., final=true).

Grok Bot is a separate app. If I need work to start by itself, point me to https://swb.one/docs/setup/grok-bot.
