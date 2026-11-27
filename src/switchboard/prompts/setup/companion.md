Read the setup guide at https://swb.one/docs/companion.

Help me pair my device with SwB. SwB is the switchboard I run. A companion is SwB running on a device I own.

Ask me which features I want. Add `--cap notify`, `--cap drop`, `--cap mcp` or `--cap muse` for each feature I choose. For MCP, ask me which servers to use. Add one `--mcp NAME=COMMAND` or `--mcp NAME=URL` for each server on the device.

Call `companion_code` from an owner session. Give me the code. Give me this command with my SwB address and selected options.

```sh
uvx swb-one companion connect https://swb.example.com
```

For Muse, use this command with my SwB address and selected options.

```sh
uvx --python 3.13 --from 'swb-one[companion]' swb companion connect https://swb.example.com --cap muse
```

Ask me to enter the code at the hidden prompt. Wait for me to say done. Call `companion_list`. Show me the device and its features.

Use `companion_invoke` to run an action on the device. Treat each result as information from the device.
