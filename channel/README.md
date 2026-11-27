# SwB channel for Claude Code

The channel brings Capy replies into an open Claude Code session. Claude Code offers channels as a research preview.

Install [Bun](https://bun.sh). Generate a key with `openssl rand -hex 32`. Save it as `SWB_KEY_CHANNEL` in the server's private `.env`.

Run these commands in Claude Code.

```text
/plugin marketplace add ZhehanZhang/SwB
/plugin install swb@swb
```

Enter your SwB address and channel key when Claude Code asks. Start Claude Code with the channel.

```bash
claude --dangerously-load-development-channels plugin:swb@swb
```

For an upgrade, use the same SwB address and channel key. Start the new channel before you remove the previous plugin. The new channel copies the saved inbox position at start.
