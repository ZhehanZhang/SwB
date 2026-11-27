Setup guide: https://swb.one/docs/setup/chatgpt

Help me connect ChatGPT to SwB. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, guide me to add SwB in ChatGPT on the web. I open https://chatgpt.com/plugins in a browser. I use + > Add custom MCP server. I enter a name such as SwB. In Connection, I enter my SwB address as Server URL. An example address is https://swb.example.com/mcp. I choose OAuth. I select I understand and want to continue. I click Create as a plugin. I install the plugin. I sign in to SwB as the owner. I start a new chat. I select the plugin with `@`. I paste this prompt again.

Call connect(what="chatgpt"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call connect(what="chatgpt") again to check the connection.

Ask me to enter a token only through the SwB claim link that connect returns. Keep tokens out of this chat and tool arguments.

When connect reports that the client is signed in, send a test message with `send(to="agent:chatgpt", task="SwB setup test: reply pong")`. Read the message with inbox. Show me the result.

Subscribe to switchboard.message.created for the chatgpt mailbox if this app supports MCP events. Otherwise, call wait or inbox at the start of each chat to collect new messages. When an event arrives, read the message with get_message. Answer that message with reply(conversation=..., text=..., final=true).
