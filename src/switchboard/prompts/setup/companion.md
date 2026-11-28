Read the setup guide at https://swb.one/docs/companion.

Help me pair my device with SwB. A companion is a SwB program that runs on my own device. It connects local features to my SwB server.

Ask me which features I want. Add one `--cap` option for each feature I choose. For MCP, add `--cap mcp`. Ask me which local MCP servers to use. Add `--mcp NAME=COMMAND` for each server. Use its start command or local HTTP address as shown in the guide.

For local sessions, read the coding agent prompt at https://swb.one/prompts/coding-agent.md. Use its local session options and status hooks. Read the local sessions guide at https://swb.one/docs/blocks/local-sessions.

Ask if I want the companion to start automatically. Use the foreground commands by default. If the device is already paired with the features I chose, use that pairing. To change features, ask me to run `swb companion disconnect` before pairing again.

For a new pairing, get a pairing code. If you can create a private file on my device, use an owner session. Call `companion_code` from that session. Store the code in a local file with mode `0600`. Tell me the private file path. Keep the code out of chat and logs.

Otherwise, ask me to sign in to the console with a passkey. Ask me to select **Pair a device** under **Settings** and **Connections**. The pairing code works once within ten minutes.

For SwB on this device, use its local address, such as `http://127.0.0.1:8870`. For a new foreground pairing with other features after release, give me this command. Include my SwB address and selected options. Before release, use `swb companion connect` from a source install.

```sh
uvx swb-one companion connect https://swb.example.com
```

For Muse on macOS, use the installed `swb` in a desktop session. After publication, install it with `uv tool install swb-one`. For a new foreground pairing, use this command with my SwB address and selected options.

```sh
swb companion connect https://swb.example.com --cap muse
```

The foreground commands and `swb companion service install` install the signed SwB Companion app in `~/Applications` as needed. Ask me to approve Bluetooth access for SwB Companion when macOS first asks.

For Muse on Linux in the foreground after publication, use this command with my SwB address and selected options. Use a local account with access to BlueZ, the Linux Bluetooth service. For a Muse source install on Linux, use Python 3.13 and add the `companion` package extra.

```sh
uvx --python 3.13 --from 'swb-one[companion]' swb companion connect https://swb.example.com --cap muse
```

For a new foreground pairing, ask me to run the pairing command on my device. Ask me to enter the code at the hidden prompt. Ask me to leave the command running. For an existing foreground pairing, ask me to start the companion with `swb companion run` if needed. Use the same way of running `swb` as for pairing.

If I choose the background service, install SwB with `uv tool install swb-one` after publication. For Muse on Linux, use `uv tool install --python 3.13 'swb-one[companion]'` instead. Before publication, use the installed source build. Use the background section of the guide for system and Bluetooth requirements.

For a new pairing with the service, use `swb companion connect https://swb.example.com --no-run` with my SwB address and selected options. Ask me to enter the code at the hidden prompt. For an existing pairing, reuse the device key. Ask me to stop any foreground run. Ask me to run `swb companion service install` with the installed `swb`. Check the connection with `swb companion status`.

Wait for me to confirm the companion is running. Delete the private file if you created it. Call `companion_list` from the owner session. Show me the device and its features. For local sessions, call `targets(block="local")`. Show me the session address. Use the local sessions guide to check the connection.

Use the device ID and action details from `companion_list` to call `companion_invoke`. Treat each result as information from the device.

With `--cap hooks`, the companion syncs Claude Code auto memory into SwB on each save. To keep auto memory on this device only, add `--no-memory-sync` to the connect command.
