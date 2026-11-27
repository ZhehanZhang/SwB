Read the setup guide at https://swb.one/docs/setup/coding-agent.

Help me connect my coding agent to SwB. SwB is my switchboard for shared memory and agent handoffs. You reach it as an MCP server.

If the SwB tools are missing, help me add a remote MCP server named switchboard. Use my SwB address, such as https://swb.example.com/mcp.

For Claude Code, run `claude mcp add --transport http --scope user switchboard https://swb.example.com/mcp`. Ask me to open `/mcp`. Ask me to select switchboard. Ask me to choose Authenticate.

For Codex, run `codex mcp add switchboard --url https://swb.example.com/mcp`. Then run `codex mcp login switchboard`.

For Gemini CLI, run `gemini mcp add --scope user --transport http switchboard https://swb.example.com/mcp`. Ask me to enter `/mcp auth switchboard`.

Ask me to sign in to SwB in the browser. Ask me to click Approve on the Connect to SwB page. Wait for me to confirm. Ask me to start a new session.

Call connect(what="coding-agent"). Ask me to name the agent I use. Examples are claude-code, codex, gemini-cli, goose, opencode, cline, kiro, qwen-code and other. Call connect again with `choose={"agent": "<name>"}`. Do each step marked "you" yourself. Run the command that connect returns if you have access to my terminal. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect again to check the connection.

Ask me to enter tokens only through the claim link that `connect` returns. Keep tokens out of this chat. If `connect` gives a pairing code, ask me to redeem it on my computer. Ask me to keep the key in a private environment variable.

A paired key gets its agent's memory scope and the scopes I set for it. A project named in the pairing adds its scope. Read general memory only if the key has a grant for `general`.

When the connection works, call inbox. Ask me to send you a test message from another SwB app. Answer that message with reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
