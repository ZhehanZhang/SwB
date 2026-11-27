Setup guide: https://swb.one/docs/setup/grok-bot

Help me connect Grok Bot to SwB. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels. SwB wakes you through a routine with a webhook trigger.

First check your tools. If the SwB tools such as connect are missing here, guide me to add SwB in the Grok Bot desktop app. The path is Connect apps in the sidebar, then Add a custom connector. I enter SwB as the Name, select Remote HTTPS under Type, and enter my SwB address, for example https://swb.example.com/mcp, as the Server URL. I select Add plugin and sign in to SwB with OAuth as the owner.

Call connect(what="grok-bot"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path and links. Wait for me to say done after each step. Then call connect(what="grok-bot") again to check it.

When connect asks for a routine, create it yourself in this chat. Give it a webhook trigger and the routine prompt that connect returns. Then register it with connect(what="grok-bot", choose={"name":"<routine>","project":"<project>","auth":"key"}). Pass the routine name and project only.

Ask me for the webhook values only through the two SwB claim links that connect returns. To find them, I click your name at the top of the chat, then Details, then the routine. Under Webhook, I copy POST to into the Webhook URL link. I copy key, and leave header, into the Token link. Keep both values out of this chat, the routine prompt and tool arguments.

Run the test call that connect returns, send(to="grok-routine:routine:<routine>", task="switchboard setup test: reply pong"). Call connect again to see if Grok accepted it, and show me the pong reply.

Last, the webhook is how SwB wakes you, so each SwB message starts a routine run. In each run, answer with reply(conversation=..., text=..., final=true). In this chat, call wait or inbox to collect other messages.
