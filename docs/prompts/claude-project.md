Read the setup guide at https://swb.one/docs/setup/claude-project.

Help me connect this Claude Project to SwB. SwB carries messages and stores shared memory. Use its MCP tools from Claude.

If the SwB tools are missing, follow https://swb.one/docs/setup/claude to add the connector. Ask me to approve the SwB sign-in. Wait for me to confirm.

Call `connect(what="claude-project")` from a client with owner access. Do each step marked "you" yourself. A routine is a Claude Code task that SwB can start. If `connect` asks for a routine, ask me to open or create one inside this Claude Project. Ask me to add an API trigger. Ask me to paste the routine prompt that `connect` returns.

Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step. Guide me to the routine by link or through the project.

Use the routine link if it is available. Otherwise, guide me from the project or from https://claude.ai/code/routines.

Ask me for the Fire URL or the `trig_` ID from the routine's API trigger. Ask me to generate its token and keep it private.

Call `connect(what="claude-project")` again with `fire_url`, `claude_project` and `project` in `choose`. Use the exact Claude Project name and the SwB project slug. Give me the claim link. Ask me to enter the token on that page only. Call `connect` again with the same choices to check the connection.

Use `targets` to find the routine's address. Call `send` to test it. Use `wait` to collect the reply. Show me the reply.
