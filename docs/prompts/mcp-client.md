Read the setup guide at https://swb.one/docs/setup/mcp-client.

Help me connect an MCP client that supports Streamable HTTP and custom request headers. Streamable HTTP is how the client sends tool requests to SwB.

For a new local SwB server, follow **This computer** at https://swb.one/prompts/deploy.md.

Use `http://localhost:8870` if the client runs on the SwB computer. Use the port shown at startup. Use my public HTTPS address if the client runs elsewhere.

## Owner client

Use owner access for the first client if it will set up other apps. On the SwB computer, set up a private owner client. Use the local SwB address with `/mcp` at the end. Load `SWB_KEY_OWNER` from the instance `.env` inside that client. Keep the key in its local config with file mode `0600`. Send it in the `Authorization: Bearer <key>` header only to the local address. Keep the key out of commands, logs and chat. Check that the client can call `inbox`. Give the owner client https://swb.one/prompts/coordinator.md for the remaining connections.

## Paired client

Ask me to pick a short name for the app, such as `notes-bot`. From an app with SwB owner access, call `connect(what="mcp-client", choose={"agent":"notes-bot"})` with my chosen name. If I use a terminal instead, ask me to run this command in my SwB instance folder.

```bash
swb connect mcp-client --choose agent=notes-bot
```

Use `uvx swb-one connect mcp-client --choose agent=notes-bot` if I run SwB with uvx. The result gives me a pairing code. It works once within 10 minutes.

A scope is a group of memory entries. The key gets `agent:<name>` and any scope I grant to that agent. It also gets the scope of a project named in the pairing. Read `general` if the key has access to it.

If you can edit the client settings on this computer, add an MCP server named `swb`. Use a hidden prompt if I have the pairing code. Send the code to `POST /connect` and read the response in memory. Use `mcp_url` for Streamable HTTP and `key` for the `Authorization: Bearer <key>` header. Write the key straight to the client config with file mode `0600`. Keep the code and key out of commands, logs and chat. For a local server, use the MCP address shown at startup.

If I must edit the settings myself, give me this command. Ask me to run it in a private folder on the computer that runs the client. For a different server, give me the command with its base address. Ask me to enter the pairing code at the hidden prompt.

```bash
bash -c '
  umask 077
  set -C
  read -rsp "Pairing code: " code || exit
  printf "\n"
  printf "{\"code\":\"%s\"}" "$code" |
    curl -fsS http://localhost:8870/connect -H "Content-Type: application/json" \
      --data-binary @- > .swb-pairing.json
'
```

SwB returns the key once. Ask me to open `.swb-pairing.json` in a private editor.

Ask me to add an MCP server named `swb` in the client settings. Ask me to choose Streamable HTTP. Ask me to use the `mcp_url` from the file. For a local server, ask me to use the MCP address shown at startup. Ask me to set `Authorization: Bearer <key>` with the `key` from the file. Ask me to delete the file after setup.

Have the new app call `inbox`. A successful call confirms the connection, even when the inbox is empty.

For a round trip, use an app with SwB owner access. On the SwB computer, you can use a private MCP session instead. Load `SWB_KEY_OWNER` from the instance `.env` inside the client. Send it only as a bearer token to the local SwB address. Keep the key out of commands, logs and chat.

Call `send(to="agent:notes-bot", task="SwB setup test. Reply pong.")`. Replace `notes-bot` with my chosen name. Ask the new agent to read the message with `inbox`. Ask it to answer with `reply` and the item's `handle`. From the owner session, call `wait` with the `conversation` returned by `send`. Show me the reply.

For the first shared memory check, use a scope both agents can read. Ask the new agent to call `memory_list` with the scope in `scopes`.

Ask me to paste the coordinator prompt from https://swb.one/prompts/coordinator.md into the new app. The client can subscribe to `switchboard.message.created` if it supports MCP events. It can also use `wait` or `inbox` to read messages.

For each owner-only connection, give me its matching `swb connect` command. For example, give me `swb connect slack`.

Set up memory sync. Call `memory_summary` to load shared memory. Look at what you already remember about me in your own memory. Choose the facts that every agent should know. Leave out passwords, keys and sensitive personal details. Send the facts in one `memory_sync` call. Set `source` to your memory system, such as `gemini-memory` or `codex-memory`. Use `other` for any other system. Give each fact a stable `source_key`. Show me what you sent. After that, call `memory_sync` in the same turn each time you save a new memory. See https://swb.one/docs/memory.
