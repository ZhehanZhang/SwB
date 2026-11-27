Setup guide: https://swb.one/docs/setup/claude-project

Help me connect this Claude Project to SwB. SwB is my switchboard for shared memory and agent handoffs. You reach it as an MCP server.

If the SwB tools are missing here, first guide me to add SwB in Claude at Customize → Connectors → Add → Add custom connector. I enter my SwB address, such as https://swb.example.com/mcp, and approve the sign-in. Wait until I confirm.

Call connect(what="claude-project"). Do each step marked "you" yourself. You are the coordinator, so create a Claude Code routine named "switchboard <project>" inside this project with an API trigger, using the routine prompt that connect returns. Only I can make the routine's API token.

Give me each step marked "person" one at a time, and wait for me to say done. Guide me to the routine in one of two ways. Path A is the link. Give me the routine's link, or I open claude.ai/code/routines, click the banner at the top that says project routines are hidden, and open the routine. Path B is the project. In the right panel, I click + in the top tab row, choose Routines, click the routine, and click View full page. On the full page I click Edit, then Trigger → + → API. I copy the Fire URL and give it to you. I click Generate token and copy the token.

Call connect(what="claude-project") again with choose set to fire_url, claude_project and project. Give me the claim link it returns. I paste the token into Token on that page and click Save token. Ask me for tokens only through the claim link, and keep them out of chat. Call connect again to check.

Then send a test message to the routine with send and show me the reply. Last, make sure new work reaches you. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise call wait or inbox to collect new messages.
