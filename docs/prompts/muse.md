Setup guide: https://swb.one/docs/setup/muse

Help me connect Meta Muse to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, first help me add SwB as a custom MCP server here, at my SwB address such as https://swb.example.com/mcp. In Claude, open Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a web browser, then + > Add custom MCP server. In Grok, open grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait until I confirm.

Call connect(what="muse"). Do each step marked "you" yourself. Give me each step marked "person" one at a time, with the exact click path, and wait for me to say done. Then call connect(what="muse") again to check it.

My Muse steps, in order: I make a bridge token of 64 random letters and digits in my password manager and save it at the claim link. I open https://gadgets.muse.ai/settings/sdk-tokens > Log in > Generate token > Copy. On a Linux device with Bluetooth LE, in the SwB folder, I run:
/usr/bin/python3 -m venv --system-site-packages .venv-muse
.venv-muse/bin/pip install . 'musegadget @ git+https://github.com/facebookincubator/muse-gadget-sdk.git@a5f809f621c4cbdbd37336fe17e7cdb74cebf3fe#subdirectory=linux'
.venv-muse/bin/swb muse-bridge --url <my SwB address> --setup
I enter both tokens at the hidden prompts. In the Muse app, I turn on Settings > Devices > Developer mode, tap Add Device and pick the Bluetooth name SwB prints.

When the bridge is online, run the test send(to="muse:gadget:muse", task="Reply with pong to test SwB.") and check it with result.

Ask me for tokens only through the claim link that connect returns, and keep them out of chat.

When connect reports connected, send a test message and show me the reply. Last, subscribe to SwB events: use switchboard.message.created if this app supports MCP events, else call wait or inbox to collect new messages.
