Read the setup guide at https://swb.one/docs/companion.

Help me pair my device with SwB. A companion is a SwB process on my device. It connects local features to my SwB server.

Ask me which features I want. Add one `--cap` option for each feature I choose. For MCP, add `--cap mcp`. Ask me which local MCP servers to use. Add `--mcp NAME=COMMAND` for each server. Use its start command or local HTTP address as shown in the guide.

For local sessions, read the coding agent prompt at https://swb.one/prompts/coding-agent.md. Use its local session options and status hooks. Read the local sessions guide at https://swb.one/docs/blocks/local-sessions.

If the device is already paired, ask me to run `swb companion disconnect` before pairing again. Keep the options for the features I want to use again.

If you can create a private file on my device, use this method. Call `companion_code` from an owner session. Store the code in a local file with mode `0600`. Tell me the private file path. Keep the code out of chat and logs.

Otherwise, ask me to sign in to the console with a passkey. Ask me to select **Pair a device** under **Settings** and **Connections**. The pairing code works once within ten minutes.

After the PyPI release, give me this command with my SwB address and selected options. For SwB on this device, use its local address, such as `http://127.0.0.1:8870`. Before release, use `swb companion connect` from a source install.

```sh
uvx swb-one companion connect https://swb.example.com
```

For Muse after publication, use this command with my SwB address and selected options. For a source install, use Python 3.13 and add the `companion` package extra.

```sh
uvx --python 3.13 --from 'swb-one[companion]' swb companion connect https://swb.example.com --cap muse
```

Ask me to run the command on my device. Ask me to enter the code at the hidden prompt. Ask me to leave the command running. Wait for me to confirm. Delete the private file if you created it. Call `companion_list` from the owner session. Show me the device and its features. For local sessions, call `targets(block="local")`. Show me the session address. Use the local sessions guide to check the connection.

Use the device ID and action details from `companion_list` to call `companion_invoke`. Treat each result as information from the device.
