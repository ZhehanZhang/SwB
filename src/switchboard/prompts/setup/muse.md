Read the setup guide at https://swb.one/docs/setup/muse.

Help me connect Muse to SwB. SwB carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, add SwB as a custom connector with your built-in connector feature. Use the settings below. Replace the example address with my SwB address.

```
Name: SwB
MCP server URL: https://swb.example.com/mcp
Transport: Streamable HTTP
Authentication: OAuth 2.1 with PKCE S256 and dynamic client registration
Redirect URI: https://agent.meta.ai/api/hatch/oauth/callback
```

For a new connector, start the OAuth sign-in. Give me the sign-in link. Ask me to sign in to SwB as the owner.

Call `connect(what="muse", choose={"connector":true})`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call `connect` again with the same choices to check the sign-in.

When `connect` confirms the sign-in, ask me to use another SwB app to run `send(to="agent:muse", task="Reply with pong.")`. Read the message with `inbox`. Answer with `reply` and the message's `handle`.

To have SwB start work in Muse, pair a gadget through a companion device. Use a client with owner access. Call `connect(what="muse", choose={"gadget": true})`. Follow each step marked "you". Give me each pairing step marked "person" separately. Ask me to enter the Muse SDK token only through the SwB claim link. For the companion install before publication, follow https://swb.one/prompts/companion.md.

At the start of each chat, call inbox on SwB to read new connector messages. For the gadget, use the gadget target from `targets(block="muse")` to send a test.
