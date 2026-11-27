Setup guide: https://swb.one/docs/setup/mcp-client

Help me connect an MCP client app to SwB. SwB is my switchboard for shared memory and agent handoffs. Apps reach it as an MCP server.

If the SwB tools are missing, guide me to use one of two ways to start. I can run `swb connect mcp-client --choose agent=<name>` on the SwB server. I can also paste this prompt into an app where I have signed in to SwB as the owner.

To add SwB to the new app, I open its MCP server settings. I add a remote server. I choose Streamable HTTP. I enter my SwB address, such as https://swb.example.com/mcp. I add the header `Authorization: Bearer <key>`.

Call connect(what="mcp-client"). Ask me for a short name for the app. Call connect again with `choose={"agent": "<name>"}`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step.

The pairing code works once within 10 minutes. I send it with `POST https://swb.example.com/connect` from the app's computer. I keep the `swb_` key in a private setting of the app. Call connect again to check the connection. Ask me to enter tokens only through the claim link that connect returns. Keep keys out of this chat.

When the app connects, call connect_list to see its key ID. Send a test message to `agent:<name>`. Show me the reply. The app subscribes to switchboard.message.created if it supports MCP events. Otherwise, it calls wait or inbox to collect new messages.
