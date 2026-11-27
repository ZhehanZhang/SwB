Read the setup guide at https://swb.one/docs/setup/claude-project.

Help me connect this Claude Project to SwB. SwB is my switchboard for shared memory and agent handoffs. You reach it as an MCP server.

If the SwB tools are missing, ask me to open the Claude connector settings. Give me the path `Customize > Connectors > Add > Add custom connector`. Ask me to enter my SwB MCP address. Use `https://swb.example.com/mcp` as the example. Ask me to approve the sign-in. Wait for me to confirm.

Call connect(what="claude-project"). Do each step marked "you" yourself. Create a Claude Code routine named `swb <project>` in this project. Add an API trigger. Use the routine prompt that connect returns.

Give me each step marked "person" one at a time. Wait for me to say done after each step. Guide me to the routine by link or through the project.

Give me the routine link. For the routines page path, ask me to open claude.ai/code/routines. Ask me to click the banner about hidden project routines. Ask me to open the routine.

For the project path, ask me to click + in the top row of the right panel. Ask me to choose Routines. Ask me to click the routine. Ask me to click View full page.

On the full page, ask me to click Edit. Ask me to use Trigger > + > API. Ask me to give you the Fire URL. Ask me to click Generate token. Ask me to copy the token.

Call connect(what="claude-project") again with `fire_url`, `claude_project` and `project` in choose. Give me the claim link that connect returns. Ask me to paste the token into Token on that page only. Ask me to click Save token. Keep tokens out of this chat. Call connect again to check the connection.

Send a test message to the routine with send. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
