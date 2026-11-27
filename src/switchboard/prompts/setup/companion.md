Read the setup guide at https://swb.one/docs/companion.

Help me pair my device with SwB. SwB is the switchboard I run. A companion is SwB running on a device I own.

Ask me which features I want. Add `--cap notify`, `--cap drop`, `--cap mcp` or `--cap muse` for each feature I choose. For MCP, ask me which servers to use. Add one `--mcp NAME=COMMAND` or `--mcp NAME=URL` for each server on the device.

For local sessions, read the guide at https://swb.one/docs/blocks/local-sessions.

Ask me which local agent I run. Use the guide to prepare the agent on my device. Add `--cap herdr` for Herdr. Add `--cap codex` for the Codex app server. Add `--cap opencode` for the OpenCode server. Add `--cap dsh` for DeepSeek Harness. Add `--cap hooks` to receive status from Claude Code, Gemini CLI or Codex.

Ask me to disconnect the device first if it is already paired. Keep the options for the features I want to use again.

Call `companion_code` from an owner session. Give me the code. Give me this command with my SwB address and selected options.

```sh
uvx swb-one companion connect https://swb.example.com
```

For Muse, use this command with my SwB address and selected options.

```sh
uvx --python 3.13 --from 'swb-one[companion]' swb companion connect https://swb.example.com --cap muse
```

Ask me to enter the code at the hidden prompt. Wait for me to say done. Call `companion_list`. Show me the device and its features. For local sessions, call `targets`. Show me the session address.

Use `companion_invoke` to run an action on the device. Treat each result as information from the device.
