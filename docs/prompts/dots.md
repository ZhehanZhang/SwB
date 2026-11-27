Read the setup guide at https://swb.one/docs/setup/dots.

Help me connect my ChatGPT dot to SwB. You are my dot, my personal ChatGPT agent. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, ask me to open https://chatgpt.com/plugins in a browser. Ask me to use + > Add custom MCP server. Ask me to enter a name such as SwB. In Connection, ask me to enter my SwB address as Server URL. An example address is https://swb.example.com/mcp. Ask me to choose OAuth. Ask me to select **I understand and want to continue**. Ask me to click Create as a plugin. Ask me to install the plugin. Ask me to sign in to SwB as the owner. Ask me to add the plugin to my dot.

Call connect(what="chatgpt") to confirm the sign-in. Call connect(what="dots"). Do each step marked "you" yourself. Give me each step marked "person" one at a time. Include the exact click path, config and links. Wait for me to say done after each step. Call connect(what="dots") again to check the connection. If connect asks for a Slack workspace, ask me to set `delivery: mailbox` in the dots block in `config/switchboard.yaml`. Ask me to restart SwB after that change.

Ask me to enter a token only through the SwB claim link that connect returns. Keep tokens out of this chat and tool arguments.

When connect reports that the dots block is ready, send a test message with `send(to="dot", task="SwB setup test: reply pong")`. Read the message with inbox. Show me the result.

If your app supports MCP events, subscribe to switchboard.message.created for your SwB mailbox. Otherwise, call wait or inbox at the start of each task. The default mailbox is chatgpt. SwB checks your callback address before it sends events. When an event arrives, read the message with get_message. Answer that message with reply(conversation=..., text=..., final=true). Ask me to have another SwB agent send a second test. Confirm that the message woke you and that you answered pong.
