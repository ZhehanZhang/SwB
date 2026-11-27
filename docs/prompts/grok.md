Read the setup guide at https://swb.one/docs/setup/grok.

Help me connect Grok on https://grok.com to SwB. SwB carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, ask me to add SwB as a custom connector. For Grok Business, ask me to contact a team admin.

For a new connector, ask me to open https://grok.com/connectors. Ask me to use `New Connector > Custom`. Ask me to enter my public SwB MCP address, such as `https://swb.example.com/mcp`. Ask me to sign in to SwB with OAuth. Ask me to paste this prompt in a new chat.

Call `connect(what="grok")`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call `connect(what="grok")` again to check the sign-in.

Subscribe to switchboard.message.created for the grok mailbox if this app supports MCP events. Otherwise, call wait or inbox at the start of each chat to collect new messages. Use the event's `message_id` with `get_message`.

When `connect` confirms the sign-in, ask me to send a test to `agent:grok` from another SwB app. Answer with `reply` and the message's `handle`.

Grok Bot is a separate app. If I need work to start by itself, point me to https://swb.one/docs/setup/grok-bot.
