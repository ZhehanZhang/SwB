Setup guide: https://swb.one/docs/setup/dots

Help me connect my ChatGPT dot to SwB. You are my dot, my personal ChatGPT agent. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

First check your tools. If the SwB tools such as connect are missing here, guide me to add SwB in ChatGPT on the web in a browser, never in the desktop app. The path is https://chatgpt.com/plugins, then +, then Add custom MCP server. I enter a name such as SwB. Under Connection, I enter my SwB address, for example https://swb.example.com/mcp, as the Server URL. I choose OAuth, select I understand and want to continue, then Create as a plugin. I install the plugin, sign in to SwB as the owner, and add the plugin to you.

Call connect(what="chatgpt") to confirm the sign-in. Then call connect(what="dots"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path, config and links. Wait for me to say done after each step. Then call connect(what="dots") again to check it. If connect asks for a Slack workspace, tell me to set delivery: mailbox in the dots block of config/switchboard.yaml and restart SwB.

Ask me for a token only through the SwB claim link that connect returns. Keep tokens out of this chat and out of tool arguments.

When connect reports the dots block as ready, send a test message with send(to="dot", task="SwB setup test: reply pong"). Read it with inbox and show me the result.

Last, set yourself up to receive SwB messages. Subscribe to the MCP event switchboard.message.created for your SwB mailbox, chatgpt by default. SwB checks your callback address before it sends events. When an event arrives, read the message with get_message and answer with reply(conversation=..., text=..., final=true). If MCP events are unavailable to you, call wait or inbox at the start of each task. Then ask me to have another SwB agent send you a second test, and confirm that you woke and answered pong.
