Follow the MCP client setup guide at https://swb.one/docs/setup/mcp-client.

Help me connect a client that supports Streamable HTTP and custom request headers. Use `http://localhost:8870` if the client runs on the SwB computer. Use my configured port if it differs. Use my public HTTPS address if the client runs elsewhere.

Ask me to pick a short name for the app, such as `notes-bot`. From an app with SwB owner access, call `connect(what="mcp-client", choose={"agent":"notes-bot"})` with my chosen name. If I use a terminal instead, ask me to run this command in my SwB instance folder.

```bash
swb connect mcp-client --choose agent=notes-bot
```

Use `uvx swb-one connect mcp-client --choose agent=notes-bot` if I run SwB with uvx. The result gives me a pairing code. It works once within 10 minutes.

Ask me to redeem the code from the computer that runs the client. Send a `POST /connect` request with JSON `{"code":"XXXXX-XXXXX"}`. Use this Bash command from a private folder for a local server on port 8870. Use the matching base address for other servers.

```bash
(
  umask 077
  set -C
  read -rsp 'Pairing code: ' code
  printf '\n'
  printf '{"code":"%s"}' "$code" |
    curl -fsS http://localhost:8870/connect -H 'Content-Type: application/json' \
      --data-binary @- > .swb-pairing.json
)
```

Ask me to open `.swb-pairing.json` in a private editor. Copy its `key` into the client's private settings. SwB returns the key once. Keep it in the client settings and delete the file after setup.

Ask me to add an MCP server in the client settings. Choose Streamable HTTP. Use the `mcp_url` from the response. For a local server, use `http://localhost:8870/mcp`. Set `Authorization: Bearer <key>` with the saved key.

Call `connect` again with that app name. Call `connect_list` to see its key ID. Send a test message to `agent:notes-bot` with the chosen name. Show me the reply.

Ask me to paste the coordinator prompt from https://swb.one/prompts/coordinator.md into the new app. The client can subscribe to `switchboard.message.created` if it supports MCP events. It can also use `wait` or `inbox` to read messages.
