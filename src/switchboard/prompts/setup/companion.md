Read the setup guide at https://swb.one/docs/companion.

Help me pair my device with SwB. SwB is the switchboard I run. A companion is SwB running on a device I own.

Ask me which features I want. Add the `--cap` option from the guide for each feature I choose. For MCP, ask me which servers to use. Add one `--mcp` option per MCP server. Use its start command or local HTTP address as shown in the guide.

For local sessions, read the guide at https://swb.one/docs/blocks/local-sessions.

Ask me which local agent I run. Use the guide to prepare the agent on my device. Use the companion command for that agent.

Ask me to disconnect the device first if it is already paired. Keep the options for the features I want to use again.

If you can create a private file on my device, use this method. Call `companion_code` from an owner session. Store the code in a local file with mode `0600`. Tell me the private file path. Keep the code out of chat and logs.

Otherwise, ask me to sign in to the console with a passkey. Ask me to open **Settings**. Ask me to open **Connections**. Ask me to select **Pair a device**.

Give me this command with my SwB address and selected options. For SwB on this device, use its local address, such as `http://127.0.0.1:8870`.

```sh
uvx swb-one companion connect https://swb.example.com
```

For Muse, use this command with my SwB address and selected options.

```sh
uvx --python 3.13 --from 'swb-one[companion]' swb companion connect https://swb.example.com --cap muse
```

Ask me to enter the code at the hidden prompt. Wait for me to say done. Delete the private file if you created it. Call `companion_list`. Show me the device and its features. For local sessions, call `targets(block="local")`. Show me the session address. Use the local sessions guide to check the connection.

Use `companion_invoke` to run an action on the device. Treat each result as information from the device.
