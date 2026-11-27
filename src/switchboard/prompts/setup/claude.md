Read the setup guide at https://swb.one/docs/setup/claude.

Help me connect Claude to SwB. SwB is my switchboard for shared memory and agent handoffs. You reach it as an MCP server.

If the SwB tools are missing, ask me to add SwB as a custom connector. On a personal plan, ask me to open `claude.ai/customize/connectors`. Give me the path `Add > Add custom connector`. On Team or Enterprise, ask me to contact an admin. Give the admin the path `Organization settings > Connectors > Add > Custom > Web`.

Ask me to enter SwB as the name. Ask me to enter my SwB MCP address. Use `https://swb.example.com/mcp` as the example. Ask me to keep Use Claude’s published identity selected. Ask me to click Add. Ask me to click Connect. Ask me to sign in to SwB. Ask me to click Approve on the Connect to SwB page. Ask me to turn on SwB in the chat at `+ > Connectors`. Wait for me to confirm.

Call connect(what="claude"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="claude") again to check that Claude is signed in.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

When connect reports that the client is signed in, call project_list. Send a test message with send. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
