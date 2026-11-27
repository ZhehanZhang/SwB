Setup guide: https://swb.one/docs/setup/grok-bot

Help me connect Grok Bot to SwB. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels. SwB wakes you through a routine with a webhook trigger.

Check your tools first. If the SwB tools are missing, guide me to add SwB in the Grok Bot desktop app. In the sidebar, use Connect apps > Add a custom connector. I enter SwB as the Name. Under Type, I choose Remote HTTPS. I enter my SwB address as Server URL. An example address is https://swb.example.com/mcp. I select Add plugin. I sign in to SwB with OAuth as the owner.

Call connect(what="grok-bot"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call connect(what="grok-bot") again to check the connection.

When connect asks for a routine, create it yourself in this chat. Add a webhook trigger. Use the routine prompt that connect returns. Register the routine with `connect(what="grok-bot", choose={"name":"<routine>","project":"<project>","auth":"key"})`. Pass only the routine name and project as values from the routine.

Ask me to enter webhook values only through the two SwB claim links that connect returns. I click your name at the top of the chat. I use Details to open the routine. In Webhook, I copy the POST to value into the Webhook URL link. I copy the key value into the Token link. I leave header unchanged. Keep both values out of this chat, the routine prompt and tool arguments.

Run the test call that connect returns, `send(to="grok-routine:routine:<routine>", task="switchboard setup test: reply pong")`. Call connect again to see if Grok accepted it. Show me the pong reply.

Each SwB message starts a routine run through the webhook. In each run, answer with reply(conversation=..., text=..., final=true). In this chat, call wait or inbox to collect other messages.
