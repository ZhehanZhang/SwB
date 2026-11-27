Setup guide: https://swb.one/docs/blocks/grok

Help me connect Grok to SwB. SwB is my switchboard for shared memory and agent handoffs, and you reach it as an MCP server.

If the SwB tools are missing in this app, first walk me through adding SwB as a custom MCP server here. Use my SwB address, for example https://swb.example.com/mcp, and wait until I confirm.

I add SwB to Grok at grok.com under Connectors, New Connector, Custom. Tell me the SwB MCP address to enter. I sign in to SwB with OAuth when Grok asks.

Call connect(what="grok") and read the steps it returns. Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path and links. After each step, wait for me to say done. Then call connect(what="grok") again to check it. Ask me for tokens only through the claim link that connect returns, and keep them out of chat.

When connect reports done, send a test message and show me the reply. Last, make sure new work reaches you. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise call wait to collect new messages.
