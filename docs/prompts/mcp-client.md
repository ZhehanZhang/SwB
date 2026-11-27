Setup guide: https://swb.one/docs/setup/mcp-client

Help me connect an MCP client app to SwB. SwB is my switchboard for shared memory and agent handoffs. Apps reach it as an MCP server.

If the SwB tools are missing here, guide me to run swb connect mcp-client --choose agent=<name> on the SwB server, or to paste this prompt into an app that already has SwB with my owner sign-in. To add SwB to the new app, I open its MCP server settings, add a remote server with transport Streamable HTTP and my SwB address, such as https://swb.example.com/mcp, and add the header Authorization: Bearer <key>.

Call connect(what="mcp-client"). Ask me for a short name for the app, then call connect again with choose={"agent": "<name>"}. Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path, and wait for me to say done. The pairing code works once for 10 minutes. I send it with POST https://swb.example.com/connect from the app's computer and keep the swb_ key in a private setting of the app. Call connect again to check. Ask me for tokens only through the claim link that connect returns, and keep keys out of chat.

When the app is connected, call connect_list to see its key, then send a test message to agent:<name> and show me the reply. Last, make sure new work reaches the app. It subscribes to switchboard.message.created if it supports MCP events. Otherwise it calls wait or inbox to collect new messages.
