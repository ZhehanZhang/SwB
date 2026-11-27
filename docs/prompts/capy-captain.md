Read the setup guide at https://swb.one/docs/setup/capy-captain.

Help me connect my Capy Captain to SwB. A Capy Captain is a long-lived Capy agent that directs a crew of threads. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, ask me to add a custom MCP server at Settings > MCP & plugins. Ask me to choose Streamable HTTP. Ask me to enter my SwB address, such as https://swb.example.com/mcp. Ask me to choose Sign in for Authentication. Ask me to sign in to SwB as the owner.

Call connect(what="capy-captain"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call connect(what="capy-captain") again to check the connection.

Ask me to create a key with access to my Capy Captains at Settings > API. Ask me to save it in Token on the first claim link. Ask me to restart SwB. When connect lists the Capy Captains, ask me to choose one. Call `connect(what="capy-captain", choose={"captain":"<Capy Captain ID>"})`. For a project route, add `"project":"<project>"` to choose.

Ask me to create a key for a `read_write` service user at Settings > API. Ask me to give that user access to the project. Ask me to share the Capy Captain and its paired device with that user through Capy Captain menu > Share. Ask me to save the key on the second claim link. Ask me to restart SwB.

Ask me to enter keys only through the SwB claim links that connect returns. Keep keys out of this chat and tool arguments.

When connect reports connected, call capy_captains. Check the route with routes_list. Send a short test task to the Capy Captain with send. Show me the answer from result or inbox.

Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
