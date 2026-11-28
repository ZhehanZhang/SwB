Read the setup guide at https://swb.one/docs/setup/dots.

Help me connect my ChatGPT dot to SwB. A dot is my personal ChatGPT agent. SwB carries messages between my agents and channels.

Check your tools first. If the SwB tools are missing, follow https://swb.one/docs/setup/chatgpt to add the connector. Ask me to add the plugin to my dot.

Call `connect(what="chatgpt")` to check the sign-in. Ask me for my dot's name. From a client with owner access, call `connect(what="dots", choose={"dot":"<name>"})`. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Call `connect` again with the same choices. For direct delivery, set `delivery: mailbox` in the dots block in `config/switchboard.yaml`. Restart SwB after the change.

When `connect` reports that the dot is ready, find its address with `targets(block="dots")`. Ask me to send a test to that address from another SwB app.

The default mailbox is `chatgpt`. If your app supports MCP events, subscribe to switchboard.message.created for your SwB mailbox. Otherwise, call wait or inbox at the start of each task. SwB checks your callback address before it sends events. Use the event's `message_id` with `get_message`. Answer with `reply` and the message's `handle`. Tell me how you received the test.

If you keep your own memory, set up memory sync as described at https://swb.one/docs/memory.
