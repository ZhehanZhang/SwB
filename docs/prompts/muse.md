Read the setup guide at https://swb.one/docs/setup/muse.

Help me connect Muse to SwB. Use the Muse gadget for immediate wakes. The gadget needs a companion on my own device. Use the custom connector for SwB tool access.

## Muse gadget

Use a SwB agent with full owner access. If you need owner access, ask me to move this prompt to that agent.

Call `connect(what="muse", choose={"gadget": true})`. Do each action marked `you`. Give me each action marked `person` separately. Direct me to the SwB claim link to save my Muse SDK token.

A companion is a SwB program that runs on my own device. It pairs with Muse over Bluetooth. Ask me to use a Mac or Linux computer near my phone. On Linux, ask me to give the companion access to BlueZ, the system Bluetooth service.

If my device is already paired with Muse, use that pairing. If the paired device needs the Muse feature, keep its current options and ask me to run `swb companion disconnect` first. For a new pairing, use the companion guide at https://swb.one/docs/companion to get a pairing code in private. The code works once and expires after 10 minutes.

Ask if I want the companion to start at sign-in. Use the foreground option by default. For a new pairing, give me the command for my computer in place of the pairing command from `connect`. Replace the example address with my SwB address.

On macOS, use the installed `swb` in a desktop session. After publication, ask me to install it with `uv tool install swb-one`. Use this command for a new foreground pairing.

```sh
swb companion connect https://swb.example.com --cap muse
```

The foreground commands and `swb companion service install` install the signed SwB Companion app in `~/Applications` as needed. Ask me to approve Bluetooth access for SwB Companion when macOS first asks.

On Linux, use Python 3.13 and the `companion` package extra. Use this command for a new foreground pairing after publication.

```sh
uvx --python 3.13 --from 'swb-one[companion]' swb companion connect https://swb.example.com --cap muse
```

For a new foreground pairing, ask me to run the command on my device. Ask me to enter the code at the hidden prompt. Ask me to keep the companion running until Muse pairs. If a paired companion needs to start, use `swb companion run` with the same settings as for pairing.

If I choose the background option, follow the background section at https://swb.one/docs/companion. On macOS, use the installed `swb` as above. On Linux after publication, ask me to install SwB with `uv tool install --python 3.13 'swb-one[companion]'`. Before publication, use the installed source build. For a new pairing, use `swb companion connect https://swb.example.com --cap muse --no-run` with my address and pairing options. Ask me to enter the code at the hidden prompt. Ask me to stop any foreground run. Ask me to run `swb companion service install`. Check the connection and Bluetooth with `swb companion status`.

Follow the pairing action from `connect` to start the gadget when the companion is online.

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
