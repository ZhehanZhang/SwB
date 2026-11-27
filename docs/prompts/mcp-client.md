Read the setup guide at https://swb.one/docs/setup/mcp-client.

Help me connect a client that supports Streamable HTTP and custom request headers.

For a new local SwB server, follow **This computer** at https://swb.one/prompts/deploy.md.

Use `http://localhost:8870` if the client runs on the SwB computer. Use the port shown at startup. Use my public HTTPS address if the client runs elsewhere.

## Owner client

Use owner access for the first client if it will set up other apps. On the SwB computer, set up a private owner client. Load `SWITCHBOARD_KEY_OWNER` from the instance `.env` inside that client. Keep the key only in its local config with file mode `0600`. Send it as a bearer key only to the local SwB address. Keep the key out of commands, logs and chat. Check that the client can call `inbox`. Give the owner client https://swb.one/prompts/coordinator.md for the remaining connections.

## Paired client

Ask me to pick a short name for the app, such as `notes-bot`. From an app with SwB owner access, call `connect(what="mcp-client", choose={"agent":"notes-bot"})` with my chosen name. If I use a terminal instead, ask me to run this command in my SwB instance folder.

```bash
swb connect mcp-client --choose agent=notes-bot
```

Use `uvx swb-one connect mcp-client --choose agent=notes-bot` if I run SwB with uvx. The result gives me a pairing code. It works once within 10 minutes.

The key gets `agent:<name>` and any memory scopes I set for that agent. It also gets the scope of a project named in the pairing. Read general memory only if the key has a grant for `general`.

If you can edit the client settings on this computer, add the server for me. Use a hidden prompt if I have the pairing code. Send the code to `POST /connect` and read the response in memory. Use `mcp_url` for Streamable HTTP and `key` for the `Authorization: Bearer <key>` header. Write the key straight to the client config with file mode `0600`. Keep the code and key out of commands, logs and chat. For a local server, use the MCP address shown at startup.

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

Ask me to add an MCP server in the client settings. Ask me to choose Streamable HTTP. Ask me to use the `mcp_url` from the file. For a local server, ask me to use the MCP address shown at startup. Ask me to set `Authorization: Bearer <key>` with the `key` from the file. Ask me to delete the file after setup.

Have the new app call `inbox`. A successful call confirms the connection, even when the inbox is empty.

For a round trip, use an app with SwB owner access. On the SwB computer, you can use a private MCP session instead. Load `SWITCHBOARD_KEY_OWNER` from the instance `.env` inside the client. Send it only as a bearer token to the local SwB address. Keep the key out of commands, logs and chat.

Send a test message to `agent:notes-bot` with my chosen name. Ask the new agent to read it with `inbox` and answer with `reply`. Call `result` from the same owner app or session. Show me the reply.

Ask me to paste the coordinator prompt from https://swb.one/prompts/coordinator.md into the new app. The client can subscribe to `switchboard.message.created` if it supports MCP events. It can also use `wait` or `inbox` to read messages.

For each owner-only connection, give me its matching `swb connect` command. For example, give me `swb connect slack`.
