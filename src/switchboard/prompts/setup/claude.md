Setup guide: https://swb.one/docs/setup/claude

Help me connect Claude to SwB. SwB is my switchboard for shared memory and agent handoffs. You reach it as an MCP server.

If the SwB tools are missing here, first guide me to add SwB as a custom connector. On a personal plan I open claude.ai/customize/connectors, click Add, then Add custom connector. On Team or Enterprise an admin opens Organization settings → Connectors → Add → Custom → Web. I enter the name SwB and my SwB address, such as https://swb.example.com/mcp. I keep Use Claude’s published identity, click Add, then Connect. I sign in to SwB and click Approve on the Connect to SwB page. Then I turn on SwB in the chat at + → Connectors. Wait until I confirm.

Call connect(what="claude"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path, and wait for me to say done. Then call connect(what="claude") again to check that Claude is signed in. Ask me for tokens only through the claim link that connect returns, and keep them out of chat.

When connect reports the client is signed in, call project_list, send a test message with send, and show me the reply. Last, make sure new work reaches you. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise call wait or inbox to collect new messages.
