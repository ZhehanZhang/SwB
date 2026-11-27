Read the setup guide at https://swb.one/docs/setup/muse.

Help me connect Meta Muse to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing, ask me to add SwB as a custom MCP server. Ask me to enter my SwB address, such as https://swb.example.com/mcp. In Claude, ask me to use Customize > Connectors > Add > Custom > Web. In ChatGPT, ask me to open https://chatgpt.com/plugins in a browser. Ask me to use + > Add custom MCP server. In Grok, ask me to use grok.com > Connectors > New Connector > Custom. For other apps, ask me to open the MCP server settings. Wait for me to confirm.

Call connect(what="muse"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="muse") again to check the connection.

Ask me to make a bridge token of 64 random letters and digits in my password manager. Ask me to save it at the claim link. Ask me to open https://gadgets.muse.ai/settings/sdk-tokens. Ask me to use Log in. Ask me to click Generate token. Ask me to click Copy.

Ask me to run the commands below on a Linux device with Bluetooth Low Energy. Use my SwB address in the last command you give me.

```
/usr/bin/python3 -m venv --system-site-packages .venv-muse
.venv-muse/bin/pip install swb-one 'musegadget @ git+https://github.com/facebookincubator/muse-gadget-sdk.git@a5f809f621c4cbdbd37336fe17e7cdb74cebf3fe#subdirectory=linux'
.venv-muse/bin/swb muse-bridge --url https://swb.example.com --setup
```

Ask me to enter both tokens at the hidden prompts. In the Muse app, ask me to turn on Developer mode at Settings > Devices > Developer mode. Ask me to tap Add Device. Ask me to pick the Bluetooth name SwB prints.

When the bridge is online, run `send(to="muse:gadget:muse", task="Reply with pong to test SwB.")`. Check the test with result.

Use the claim link only for the bridge token. Keep tokens out of this chat.

When connect reports connected, send a test message. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
