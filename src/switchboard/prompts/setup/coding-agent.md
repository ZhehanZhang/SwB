Read the setup guide at https://swb.one/docs/setup/coding-agent.

Help me connect my coding agent to SwB. SwB carries messages and stores shared memory.

Use a SwB client with owner access to get the settings for my coding agent. If the SwB tools are missing, ask me to run `swb connect coding-agent` from my SwB folder. If I run SwB with uvx, use `uvx swb-one` in place of `swb`. Ask me to keep its output in my private terminal.

Call connect(what="coding-agent"). Ask me to name the agent I use. Call `connect` again with `choose={"agent":"<name>"}`. In my terminal, use `swb connect coding-agent --choose agent=<name>` instead. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step.

If `connect` gives OAuth settings, apply them on my computer. Use the matching command below as an alternative. Replace the example address with my SwB MCP address. Ask me to sign in to SwB in the browser.

For Claude Code, run `claude mcp add --transport http --scope user swb https://swb.example.com/mcp`. Ask me to use `/mcp` to sign in to SwB.

For Codex, run `codex mcp add swb --url https://swb.example.com/mcp`. Run `codex mcp login swb` to sign in.

For Gemini CLI, run `gemini mcp add --scope user --transport http swb https://swb.example.com/mcp`. Ask me to use `/mcp auth swb` to sign in.

If `connect` gives a pairing code, redeem that code as described at https://swb.one/prompts/mcp-client.md. Use the settings returned for my coding agent. Keep the code and key out of chat.

A scope is a group of memory entries. A paired key gets its agent scope and any scope I grant. A project named in the pairing adds its scope. Read `general` if the key has access to it.

In the new client, call `inbox` to check the connection. Ask me to send a test message from another SwB app. Answer with `reply` and the item's `handle`. For on-demand reads, use `wait` or `inbox`.
