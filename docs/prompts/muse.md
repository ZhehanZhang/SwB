Read the setup guide at https://swb.one/docs/setup/muse.

Help me connect Muse to SwB. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, add SwB as a custom connector with your built-in connector feature. Use the settings below. Replace the example address with my SwB address.

```
Name: SwB
MCP server URL: https://swb.example.com/mcp
Transport: Streamable HTTP
Authentication: OAuth 2.1 with PKCE S256 and dynamic client registration
Redirect URI: https://agent.meta.ai/api/hatch/oauth/callback
```

Start the OAuth sign-in. Give me the sign-in link. Ask me to sign in to SwB as the owner.

Call connect(what="muse"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact links. Wait for me to say done after each step. Call connect(what="muse") again to check the connection.

Ask me to enter a token only through the SwB claim link that connect returns. Keep tokens out of this chat and tool arguments.

When connect reports that the client is signed in, send a test message with `send(to="agent:muse", task="SwB setup test: reply pong")`. Read the message with inbox. Show me the result.

Create a recurring scheduled task that runs every 30 minutes. In each run, call wait on SwB to collect new messages for the muse mailbox. Read each message with get_message. Answer that message with reply(conversation=..., text=..., final=true). Also call inbox at the start of each chat.
