Setup guide: https://swb.one/docs/setup/claude-project

Help me connect this Claude Project to SwB. SwB is my switchboard for shared memory and agent handoffs. You reach it as an MCP server.

If the SwB tools are missing, guide me to add SwB in Claude. Use Customize > Connectors > Add > Add custom connector. I enter my SwB address, such as https://swb.example.com/mcp. I approve the sign-in. Wait for me to confirm.

Call connect(what="claude-project"). Do each step marked "you" yourself. Create a Claude Code routine named `switchboard <project>` in this project. Add an API trigger. Use the routine prompt that connect returns. I generate the routine's API token myself.

Give me each step marked "person" one at a time. Wait for me to say done after each step. Guide me to the routine by link or through the project.

For the link path, give me the routine link. I can also open claude.ai/code/routines. I click the banner about hidden project routines. I open the routine.

For the project path, I click + in the top tab row of the right panel. I choose Routines. I click the routine. I click View full page.

On the full page, I click Edit. I use Trigger > + > API. I copy the Fire URL. I give you the Fire URL. I click Generate token. I copy the token.

Call connect(what="claude-project") again with `fire_url`, `claude_project` and `project` in choose. Give me the claim link that connect returns. I paste the token into Token on that page. I click Save token. Ask me to enter tokens only through that claim link. Keep tokens out of this chat. Call connect again to check the connection.

Send a test message to the routine with send. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
