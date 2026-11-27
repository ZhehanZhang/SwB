Setup guide: https://swb.one/docs/setup/chatgpt

Help me connect ChatGPT to SwB. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

First check your tools. If the SwB tools such as connect are missing here, guide me to add SwB as a custom MCP server. I add it in ChatGPT on the web in a browser, because the desktop app only uses plugins that I already added. The path is https://chatgpt.com/plugins, then +, then Add custom MCP server. I enter a name such as SwB. Under Connection, I enter my SwB address, for example https://swb.example.com/mcp, as the Server URL. I choose OAuth, select I understand and want to continue, then Create as a plugin. I install the plugin and sign in to SwB as the owner. Then I start a new chat, select the plugin with @, and paste this prompt again.

Call connect(what="chatgpt"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path and links. Wait for me to say done after each step. Then call connect(what="chatgpt") again to check it.

Ask me for a token only through the SwB claim link that connect returns. Keep tokens out of this chat and out of tool arguments.

When connect reports that the client is signed in, send a test message with send(to="agent:chatgpt", task="SwB setup test: reply pong"). Read it with inbox and show me the result.

Last, subscribe to SwB events so new work reaches you. If this app supports MCP events, subscribe to switchboard.message.created for the chatgpt mailbox. When an event arrives, read the message with get_message and answer with reply(conversation=..., text=..., final=true). Otherwise, call wait or inbox at the start of each chat to collect new messages.
