Read the setup guide at https://swb.one/docs/setup/muse.

Help me connect Muse to SwB. Use the custom connector for SwB tool access.

## Custom connector

Check for SwB tools in Muse. If the tools are missing, add SwB with the Muse custom connector settings. Replace the example address with my SwB address.

```
Name: SwB
MCP server URL: https://swb.example.com/mcp
Transport: Streamable HTTP
Authentication: OAuth 2.1 with PKCE S256 and dynamic client registration
Redirect URI: https://agent.meta.ai/api/hatch/oauth/callback
```

Start OAuth sign-in for a new connector. Give me the sign-in link. Ask me to sign in to SwB as the owner.

Call `connect(what="muse")` to confirm sign-in. Ask another SwB agent to call `send(to="agent:muse", task="Reply with pong.")`. Read the message with `inbox`. Answer with `reply` in the same conversation.

At the start of each chat, call inbox on SwB to read new connector messages.

If you keep your own memory, set up memory sync as described at https://swb.one/docs/memory.
