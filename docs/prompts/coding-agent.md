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

In the new client, call `inbox` to check the connection. Ask me to send a test message from another SwB app. Answer with `reply` and the item's `handle`. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.

## Local sessions

Ask if I want SwB to reach a session on my device. A local session is an open workspace for my coding agent. If I want this, guide me through pairing a companion on that device. A companion is a SwB program that runs on that device.

Read https://swb.one/docs/companion for pairing. Read https://swb.one/docs/blocks/local-sessions for the setup for my agent. Ask me to install the `swb` command with `pipx install swb-one`. Ask me to open a session in my coding agent.

Use `swb companion connect https://swb.example.com` with the option for my session. Replace the example address with my SwB address. Keep the options for the features I use. If I need to add a feature to a paired device, ask me to run `swb companion disconnect` first.

| Local session | Companion option |
| --- | --- |
| Claude Code in Herdr | `--cap herdr --cap hooks` |
| Claude Code with status hooks | `--cap hooks` |
| Codex app server | `--cap codex` |
| OpenCode server | `--cap opencode` |
| DeepSeek Harness | `--cap dsh` |

Use the guide to prepare the Codex app server, OpenCode server or DeepSeek Harness bridge when applicable. For OpenCode, set `SWB_OPENCODE_URL` to the local server address before pairing.

A status hook runs a command when an agent reports a change. On macOS or Linux, set up the Claude Code hook from `channel/hooks/hooks.json`. Use the existing hook if I have the SwB Claude Code plugin. The hook runs `swb hook claude-code`.

For Codex status hooks, add `--cap hooks`. Set up `swb hook codex` from `integrations/codex/hooks.json`. Use the local sessions guide for the settings file for each hook. Make sure the agent can find the `swb` command. Open a new session after you set up a hook.

Help me get a companion pairing code in private. The code works once and expires after 10 minutes. Ask me to enter it at the hidden prompt. Ask me to keep the companion running.

From an owner session, call `targets(block="local")`. Check that my session appears. Send a test message to the session address. For a session with status hooks alone, check its mailbox for the message. For a direct delivery, check the reply with `result`.
