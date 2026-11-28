Read the setup guide at https://swb.one/docs/setup/capy-captain.

Help me connect my Capy Captain to SwB. A Capy Captain directs a group of Capy threads. SwB carries messages between my agents and channels.

Use a SwB client with owner access to set up the Captain. If I want to use SwB tools in Capy, use the setup guide to add a custom MCP server. Use my public MCP address, such as `https://swb.example.com/mcp`.

Call `connect(what="capy-captain")`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call `connect` again with the same choices to check progress.

Ask me to create a key that can list my Capy Captains. Ask me to save it through the first claim link. Ask me to restart SwB. When `connect` lists the Captains, ask me to choose one. Call `connect(what="capy-captain", choose={"captain":"<Capy Captain ID>"})`. For a project route, add `"project":"<project>"` to `choose`.

A service user is a Capy account for SwB. If `connect` asks for its key, open Capy's API settings. Ask me to create a key with read and write access. Ask me to give that user access to the project and the Captain. Ask me to share the device if the Captain runs on one. Ask me to save the key through the claim link. Ask me to restart SwB.

Ask me to enter keys only through the SwB claim links that connect returns. Keep keys out of this chat and tool arguments.

When `connect` reports `connected`, call `capy_captains`. Check the route with `routes_list`. Call `send` with a short test task and the chosen `project` if you set a project route. Use `wait` or `result` with the returned `conversation` to read the answer. Show me the answer.

For a direct send, use the reserved name `captain` only when SwB finds exactly one Captain. Otherwise, use the chosen Captain's named alias or full address from `targets`.

Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.

Set up memory sync. Call `memory_summary` to load shared memory. Look at what you already remember about me in your own memory. Choose the facts that every agent should know. Leave out passwords, keys and sensitive personal details. Send the facts in one `memory_sync` call with `source="capy-drive"`. Give each fact a stable `source_key`. Show me what you sent. Treat the notes in your drive and your project instructions as your memory. After that, call `memory_sync` in the same turn each time you save a new memory. See https://swb.one/docs/memory.
