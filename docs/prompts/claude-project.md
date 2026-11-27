Setup guide: https://swb.one/docs/blocks/claude

Help me connect a Claude Project to SwB. SwB is my switchboard for shared memory and agent handoffs, and you reach it as an MCP server.

If the SwB tools are missing in this app, first walk me through adding SwB as a custom MCP server here. Use my SwB address, for example https://swb.example.com/mcp, and wait until I confirm.

Claude Projects work best with a Claude Code routine that SwB can wake through an API trigger. Use the new Claude Projects. Create the routine inside the project so it runs with the project's context. Only I can make the routine's API token. Guide me to the routine in one of two ways. The first way is to give me the routine's link. The second way is the Routines page, where a banner at the top hides project routines. I click the banner to show them, open the routine, and click Edit. In the project, I can also use the right panel. I click the + tab, choose Routines, open the routine, and click View full page, then Edit. On the routine's page I add an API trigger, make the token, and copy the fire URL. Tell me to paste the token on the SwB claim link and to give you the fire URL or trigger ID.

Call connect(what="claude-project") and read the steps it returns. Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path and links. After each step, wait for me to say done. Then call connect(what="claude-project") again to check it. Ask me for tokens only through the claim link that connect returns, and keep them out of chat.

When connect reports done, send a test message and show me the reply. Last, make sure new work reaches you. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise call wait to collect new messages.
