Setup guide: https://swb.one/docs/setup/dots

Help me connect my ChatGPT dot to SwB. You are my dot, my personal ChatGPT agent. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, guide me to add SwB in ChatGPT on the web. I open https://chatgpt.com/plugins in a browser. I use + > Add custom MCP server. I enter a name such as SwB. In Connection, I enter my SwB address as Server URL. An example address is https://swb.example.com/mcp. I choose OAuth. I select I understand and want to continue. I click Create as a plugin. I install the plugin. I sign in to SwB as the owner. I add the plugin to my dot.

Call connect(what="chatgpt") to confirm the sign-in. Call connect(what="dots"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path, config and links. Wait for me to say done after each step. Call connect(what="dots") again to check the connection. If connect asks for a Slack workspace, tell me to set `delivery: mailbox` in the dots block of config/switchboard.yaml. If you gave me that setting, tell me to restart SwB.

Ask me to enter a token only through the SwB claim link that connect returns. Keep tokens out of this chat and tool arguments.

When connect reports that the dots block is ready, send a test message with `send(to="dot", task="SwB setup test: reply pong")`. Read the message with inbox. Show me the result.

If your app supports MCP events, subscribe to switchboard.message.created for your SwB mailbox. Otherwise, call wait or inbox at the start of each task. The default mailbox is chatgpt. SwB checks your callback address before it sends events. When an event arrives, read the message with get_message. Answer that message with reply(conversation=..., text=..., final=true). Ask me to have another SwB agent send a second test. Confirm that the message woke you and that you answered pong.
