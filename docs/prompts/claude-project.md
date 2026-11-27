Read the setup guide at https://swb.one/docs/setup/claude-project.

Help me connect this Claude Project to SwB. SwB carries messages and stores shared memory. Use its MCP tools from Claude.

If the SwB tools are missing, give me the path `Customize > Connectors > Add > Add custom connector`. Ask me to enter my SwB MCP address. Use `https://swb.example.com/mcp` as the example. Ask me to approve the SwB sign-in. Wait for me to confirm.

Call `connect(what="claude-project")` from a SwB client with unrestricted owner access. Do each step marked "you" yourself. If setup needs a routine, create a Claude Code routine named `swb <project>` in this Claude Project yourself. Add an API trigger. Use the routine prompt that `connect` returns. Use the exact Claude Project name in that prompt.

Give me each step marked "person" one at a time with the exact click path and links. Wait for me to say done after each step.

Give me the routine link if available. For the Routines page path, ask me to open https://claude.ai/code/routines. Ask me to click the banner at the top about hidden project routines. Ask me to open `swb <project>`.

For the project path, ask me to click **+** in the top tab row of the right panel. Ask me to select **Routines**. Ask me to open `swb <project>`. Ask me to click **View full page**.

On the full page, ask me to open the menu next to the routine name and select **Edit**. In the edit form, guide me to the token controls through `Trigger > + > API`. Ask me for the **Fire URL** or the `trig_` ID. Ask me to select **Generate token** as the owner. Ask me to copy the token while **Token generated** is open.

Call `connect(what="claude-project")` again with `fire_url`, `claude_project` and `project` in `choose`. Use the exact Claude Project name and the SwB project slug. Give me the private claim link. Ask me to paste the token into **Token** on that page only. Ask me to select **Save token**. Call `connect` again with the same choices to check the connection.

Use `targets` to find the routine's address. Call `send` to test it. Use `wait` to collect the reply. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
