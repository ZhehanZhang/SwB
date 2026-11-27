Read the setup guide at https://swb.one/docs/setup/mcp-client.

Help me connect a client that supports Streamable HTTP and custom request headers.

For a new local SwB server, ask me to run `uvx swb-one up` in an empty folder. Ask me to open the one-time sign-in link from my terminal. Keep the link private.

Use `http://localhost:8870` if the client runs on the SwB computer. Use the port shown at startup. Use my public HTTPS address if the client runs elsewhere.

Ask me to pick a short name for the app, such as `notes-bot`. From an app with SwB owner access, call `connect(what="mcp-client", choose={"agent":"notes-bot"})` with my chosen name. If I use a terminal instead, ask me to run this command in my SwB instance folder.

```bash
swb connect mcp-client --choose agent=notes-bot
```

Use `uvx swb-one connect mcp-client --choose agent=notes-bot` if I run SwB with uvx. The result gives me a pairing code. It works once within 10 minutes.

Ask me to run this command in a private folder on the computer that runs the client. For a different server, give me the command with its base address. Ask me to enter the pairing code at the hidden prompt.

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

Ask me to add an MCP server in the client settings. Ask me to choose Streamable HTTP. Ask me to use the `mcp_url` from the file. For a local server, ask me to use the MCP address shown at startup. Ask me to set `Authorization: Bearer <key>` with the `key` from the file. Ask me to delete the file after setup.

From the app with SwB owner access, call `connect_list` to see the new agent's key ID. From the same app, send a test message to `agent:notes-bot` with the chosen name. Ask me to have the new agent read its `inbox`. Ask me to have that agent answer with `reply`. Call `result` in the owner app. Show me the reply.

Ask me to paste the coordinator prompt from https://swb.one/prompts/coordinator.md into the new app. The client can subscribe to `switchboard.message.created` if it supports MCP events. It can also use `wait` or `inbox` to read messages.
