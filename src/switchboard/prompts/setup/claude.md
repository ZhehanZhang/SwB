Read the setup guide at https://swb.one/docs/setup/claude.

Help me connect Claude to SwB. SwB carries messages and stores shared memory. Use its MCP tools from Claude.

If the SwB tools are missing, ask me to add SwB as a custom connector. Use the setup guide for a personal plan. On Team or Enterprise, ask me to contact an admin.

For a new connector, ask me to enter SwB as the name. Ask me to enter my public SwB MCP address, such as `https://swb.example.com/mcp`. Ask me to sign in to SwB. Ask me to select **Approve** on **Connect to SwB**. Ask me to enable SwB in this chat. Wait for me to confirm.

Call `connect(what="claude")`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call `connect(what="claude")` again to check the sign-in.

When `connect` confirms the sign-in, call `project_list`. Ask me to send a test message from another SwB app. Read it with `inbox`. Answer with `reply` and the item's `handle`. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
