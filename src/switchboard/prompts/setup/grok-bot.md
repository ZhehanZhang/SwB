Read the setup guide at https://swb.one/docs/setup/grok-bot.

Help me connect Grok Bot to SwB. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels. SwB wakes you through a routine with a webhook trigger.

Check your tools first. If the SwB tools are missing, ask me to open the Grok Bot desktop app. In the sidebar, ask me to use Connect apps > Add a custom connector. Ask me to enter SwB as the Name. Under Type, ask me to choose Remote HTTPS. Ask me to enter my SwB address as Server URL. An example address is https://swb.example.com/mcp. Ask me to select Add plugin. Ask me to sign in to SwB with OAuth as the owner.

Call connect(what="grok-bot"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call connect(what="grok-bot") again to check the connection.

When connect asks for a routine, create it yourself in this chat. Add a webhook trigger. Use the routine prompt that connect returns. Register the routine with `connect(what="grok-bot", choose={"name":"<routine>","project":"<project>","auth":"key"})`. Pass only the routine name and project as values from the routine.

Ask me to enter webhook values only through the two SwB claim links that connect returns. Ask me to click your name at the top of the chat. Ask me to use Details to open the routine. In Webhook, ask me to copy the POST to value into the Webhook URL link. Ask me to copy the key value into the Token link. Ask me to leave header unchanged. Keep both values out of this chat, the routine prompt and tool arguments.

Run the test call that connect returns. Use `send(to="grok-routine:routine:<routine>", task="SwB setup test. Reply pong.")`. Call connect again to check the test. Show me the pong reply.

Each SwB message starts a routine run through the webhook. In each run, answer with reply(conversation=..., text=..., final=true). In this chat, call wait or inbox to collect other messages.
