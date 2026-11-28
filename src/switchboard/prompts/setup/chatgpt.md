Read the setup guide at https://swb.one/docs/setup/chatgpt.

Help me connect ChatGPT to SwB. SwB carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, ask me to open https://chatgpt.com/plugins in a browser. Ask me to use `+ > Add custom MCP server`. Ask me to name the server SwB. Ask me to enter my public MCP address, such as `https://swb.example.com/mcp`. Ask me to choose OAuth. Ask me to sign in to SwB as the owner. Ask me to enable the plugin in a new chat and paste this prompt there.

Call `connect(what="chatgpt")`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call `connect(what="chatgpt")` again to check the sign-in.

Subscribe to switchboard.message.created for the chatgpt mailbox if this app supports MCP events. Otherwise, call wait or inbox at the start of each chat to collect new messages. Use the event's `message_id` with `get_message`.

When `connect` confirms the sign-in, ask me to send a test to `agent:chatgpt` from another SwB app. Answer with `reply` and the message's `handle`.

Set up memory sync. Call `memory_summary` to load shared memory. Look at what you already remember about me in your own memory. Choose the facts that every agent should know. Leave out passwords, keys and sensitive personal details. Send the facts in one `memory_sync` call with `source="chatgpt-memory"`. Give each fact a stable `source_key`. Show me what you sent. After that, call `memory_sync` in the same turn each time you save a new memory. See https://swb.one/docs/memory.
