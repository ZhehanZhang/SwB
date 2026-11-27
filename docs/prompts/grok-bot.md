Read the setup guide at https://swb.one/docs/setup/grok-bot.

Help me connect Grok Bot to SwB. SwB uses a webhook to start a Grok Bot routine. A webhook is a request to a URL when an event occurs.

Check your tools first. If the SwB tools are missing, ask me to open the Grok Bot desktop app. Use `Connect apps > Add a custom connector > Remote HTTPS`. Ask me to enter my public SwB MCP address, such as `https://swb.example.com/mcp`. If Grok Bot opens SwB sign-in, ask me to approve access. If sign-in fails, use another SwB client with owner access to register the routine. If Grok Bot supports bearer keys, set up its key with https://swb.one/prompts/mcp-client.md.

Call `connect(what="grok-bot")` from a client with owner access. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call `connect` again with the same choices to check progress.

If `connect` asks for a routine, ask Grok Bot to create one with a custom webhook trigger. Use the routine prompt that `connect` returns. Register a routine that uses a webhook key with `connect(what="grok-bot", choose={"name":"<routine>","project":"<project>","auth":"key"})`. For a signed trigger, use `auth="signature"`.

Ask me to open the routine's webhook settings. Ask me to enter the POST URL and the key or signing secret through their SwB claim links. Keep the values out of this chat, the routine prompt and tool arguments. Pass `header` only if the trigger uses a custom header.

Run the `send` call that `connect` returns. Call `connect` again to check that Grok accepted the webhook. Use `wait` or `inbox` to collect the reply. Show me the reply if it arrives.

For a message sent to the routine, use the saved routine prompt. Answer with `reply` on the conversation from the webhook payload. Use `wait` or `inbox` to read other messages in this chat.
