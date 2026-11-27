Read the setup guide at https://swb.one/docs/setup/muse.

Help me connect Muse to SwB. Use the Muse gadget for immediate wakes. The gadget needs a companion on my own device. Use the custom connector for SwB tool access.

## Muse gadget

Use a SwB agent with full owner access. If you need owner access, ask me to move this prompt to that agent.

Call `connect(what="muse", choose={"gadget": true})`. Do each action marked `you`. Give me each action marked `person` separately. Direct me to the SwB claim link to save my Muse SDK token.

A companion is a SwB program on my computer. It pairs with Muse over Bluetooth. Ask me to use a Mac or Linux computer near my phone. On macOS, ask me to use Terminal. Ask me to allow Bluetooth access when macOS asks. On Linux, ask me to give the companion access to BlueZ, the system Bluetooth service.

Use the companion guide at https://swb.one/docs/companion to help me get a companion pairing code in private. The code works once and expires after 10 minutes. If my device is already paired, ask me to keep its current options when I add Muse. Ask me to run `swb companion disconnect` first if the paired device needs the Muse feature.

Replace the example address with my SwB address. Ask me to run this command on my device.

```sh
uvx --python 3.13 --from 'swb-one[companion]' swb companion connect https://swb.example.com --cap muse
```

Ask me to enter the code at the hidden prompt. Ask me to leave the companion running until Muse pairs. Follow the pairing action from `connect` to start the gadget.

Ask me to open **Settings** in the Muse app. Ask me to open **Devices**. Ask me to turn on **Developer mode**. Ask me to select **Add Device**. Give me the device name from `connect`. If the app asks for Wi-Fi, ask me to select **Use current connection**.

Call `connect(what="muse", choose={"gadget": true})` to confirm pairing. Call `targets(block="muse")` to find the gadget address. Send a test message to that address. Check the reply in the test conversation.

## Custom connector

If I want tool access, check for SwB tools in Muse. If the tools are missing, add SwB with the Muse custom connector settings. Replace the example address with my SwB address.

```
Name: SwB
MCP server URL: https://swb.example.com/mcp
Transport: Streamable HTTP
Authentication: OAuth 2.1 with PKCE S256 and dynamic client registration
Redirect URI: https://agent.meta.ai/api/hatch/oauth/callback
```

Start OAuth sign-in for a new connector. Give me the sign-in link. Ask me to sign in to SwB as the owner.

Call `connect(what="muse", choose={"connector":true})` to confirm sign-in. Ask another SwB agent to call `send(to="agent:muse", task="Reply with pong.")`. Read the message with `inbox`. Answer with `reply` in the same conversation.

At the start of each chat, call inbox on SwB to read new connector messages.

If you keep your own memory, set up memory sync as described at https://swb.one/docs/memory.
