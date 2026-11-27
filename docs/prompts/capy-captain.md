Setup guide: https://swb.one/docs/setup/capy-captain

Help me connect my Capy Captain to SwB. A Captain is a long-lived Capy agent that directs a crew of threads. SwB is my self-hosted switchboard. It is an MCP server that carries messages between my agents and channels.

First check your tools. If the SwB tools such as connect are missing here, guide me to add SwB as a custom MCP server in this app. In Capy, the path is Settings, then MCP & plugins, then add a server with Streamable HTTP, my SwB address, for example https://swb.example.com/mcp, and Sign in as the Authentication. I sign in to SwB as the owner.

Call connect(what="capy-captain"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path and links. Wait for me to say done after each step. Then call connect(what="capy-captain") again to check it.

The first key is a Capy API key that can see my Captains. I create it in Capy under Settings, then API, and I save it on the claim link in Token. Then I restart SwB. When connect lists the Captains, confirm the right one with me and call connect(what="capy-captain", choose={"captain":"<Captain ID>"}). Add "project":"<project>" to choose for a project route.

The second key is for a read_write service user in Settings, then API. Tell me to give it the project, and to share the Captain and its paired device with it through the Captain menu, then Share. I save its key on the second claim link, then restart SwB.

Ask me for keys only through the SwB claim links that connect returns. Keep keys out of this chat and out of tool arguments.

When connect reports connected, check the route with capy_captains and routes_list. Send a short test task to the Captain with send, and show me the answer from result or inbox.

Last, make sure new work reaches you. If this app supports MCP events, subscribe to switchboard.message.created. Otherwise, call wait or inbox to collect new messages.
