Setup guide: https://swb.one/docs/setup/capy-captain

Help me connect my Capy Captain to SwB. A Captain is a long-lived Capy agent that directs a crew of threads. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, guide me to add SwB as a custom MCP server in Capy. I use Settings > MCP & plugins to add a server. I choose Streamable HTTP. I enter my SwB address, such as https://swb.example.com/mcp. I choose Sign in for Authentication. I sign in to SwB as the owner.

Call connect(what="capy-captain"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call connect(what="capy-captain") again to check the connection.

The first key is a Capy API key with access to my Captains. I create it in Capy at Settings > API. I save it in Token on the first claim link. I restart SwB. When connect lists the Captains, confirm which one to use with me. Call `connect(what="capy-captain", choose={"captain":"<Captain ID>"})`. For a project route, add `"project":"<project>"` to choose.

The second key is for a `read_write` service user in Settings > API. Tell me to give that user access to the project. Tell me to share the Captain and its paired device with the user through Captain menu > Share. I save the key on the second claim link. I restart SwB.

Ask me to enter keys only through the SwB claim links that connect returns. Keep keys out of this chat and tool arguments.

When connect reports connected, call capy_captains. Check the route with routes_list. Send a short test task to the Captain with send. Show me the answer from result or inbox.

Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
