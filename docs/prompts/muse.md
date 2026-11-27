Setup guide: https://swb.one/docs/setup/muse

Help me connect Meta Muse to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, help me add SwB as a custom MCP server. Use my SwB address, such as https://swb.example.com/mcp. In Claude, use Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a browser. Use + > Add custom MCP server. In Grok, use grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait for me to confirm.

Call connect(what="muse"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="muse") again to check the connection.

I make a bridge token of 64 random letters and digits in my password manager. I save it at the claim link. I open https://gadgets.muse.ai/settings/sdk-tokens. I use Log in. I click Generate token. I click Copy.

On a Linux device with Bluetooth LE, I go to the SwB folder. I run these commands.

```
/usr/bin/python3 -m venv --system-site-packages .venv-muse
.venv-muse/bin/pip install . 'musegadget @ git+https://github.com/facebookincubator/muse-gadget-sdk.git@a5f809f621c4cbdbd37336fe17e7cdb74cebf3fe#subdirectory=linux'
.venv-muse/bin/swb muse-bridge --url <my SwB address> --setup
```

I enter both tokens at the hidden prompts. In the Muse app, I turn on Developer mode at Settings > Devices > Developer mode. I tap Add Device. I pick the Bluetooth name SwB prints.

When the bridge is online, run `send(to="muse:gadget:muse", task="Reply with pong to test SwB.")`. Check the test with result.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

When connect reports connected, send a test message. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
