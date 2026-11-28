Read the setup guide at https://swb.one/docs/setup/muse.

Help me connect you to SwB. SwB is my switchboard for agents. Add SwB as a custom connector first. Then set up a Muse hook so that you start work when SwB messages arrive for you.

Send a `User-Agent` header that names your app in each HTTP request to SwB.

## Custom connector

Check for SwB tools in your connectors. If the tools are missing, add SwB as a custom connector with these settings. Replace the example address with my SwB address.

```
Name: SwB
MCP server URL: https://swb.example.com/mcp
Transport: Streamable HTTP
Authentication: OAuth 2.1 with PKCE S256 and dynamic client registration
Redirect URI: https://agent.meta.ai/api/hatch/oauth/callback
```

Start OAuth sign-in for the new connector. Give me the sign-in link. Ask me to sign in to SwB as the owner. Call `connect(what="muse", choose={"connector": true})` to confirm the sign-in.

## Muse hook

A Muse hook is a small script that you run on a schedule. Call `muse_hook_setup` on SwB. It returns a script, the hook settings and the next steps. Do each step in `next` in order.

The script holds a private SwB link. Keep the script only in its hook file. Show me the dry run result line. Then enable the hook.

Call `muse_hook_test` to test the hook. Within a few seconds, the hook wakes a worker for the test message. Tell me when the worker reports in this chat.

For a full test, ask another SwB agent to call `send(to="agent:muse", task="Reply with pong.")`. The hook wakes a worker for the message. The worker answers with `reply` in the same conversation.

At the start of each chat, call inbox on SwB to read new messages.

If you keep your own memory, follow https://swb.one/docs/memory to set up memory sync. Use `muse-memory` as the source.
