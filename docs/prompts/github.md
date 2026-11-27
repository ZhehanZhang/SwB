Read the setup guide at https://swb.one/docs/setup/github.

Help me connect GitHub to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing, ask me to add SwB as a custom MCP server. Ask me to enter my SwB address, such as https://swb.example.com/mcp. In Claude, ask me to use Customize > Connectors > Add > Custom > Web. In ChatGPT, ask me to open https://chatgpt.com/plugins in a browser. Ask me to use + > Add custom MCP server. In Grok, ask me to use grok.com > Connectors > New Connector > Custom. For other apps, ask me to open the MCP server settings. Wait for me to confirm.

Call `connect(what="github")` first. Use its claim links. Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call `connect(what="github")` again to check the connection.

Ask me to use profile picture > Settings > Developer settings > GitHub Apps > New GitHub App. Ask me to enter the GitHub App name. Ask me to enter the Homepage URL. Under Webhook, ask me to keep Active selected. Ask me to set Webhook URL to my SwB address with `/github/webhook` at the end. Ask me to generate the webhook secret in a private file. SwB accepts secrets of at least 16 characters.

```bash
(umask 077; set -C; openssl rand -hex 32 > "$HOME/.swb-github-secret")
```

Ask me to save the result in my password manager. Ask me to enter it in **Webhook secret**. Ask me to save the same value through the SwB claim link. Ask me to delete the private file after I save the secret.

Under Repository permissions, ask me to set Issues to Read & write. Ask me to set Pull requests to Read & write. Ask me to set Contents to Read-only. Ask me to set Checks to Read-only. Ask me to set Actions to Read-only. Ask me to subscribe to the events in the setup guide. Ask me to select Only on this account. Ask me to click Create GitHub App. Under Private keys, ask me to click Generate a private key. Ask me to use Install App > Install. Ask me to choose Only select repositories. Ask me to select my repositories. Ask me to click Install.

Add my repositories under `blocks.github.spaces` in `config/switchboard.yaml`. Replace `acme/app` with my repository. Replace `demo` with my SwB project. Call `targets` to find the address that replaces `agent:beta`. If the file needs a manual edit, give me the YAML. Ask me to apply it. Restart SwB after I save the webhook secret.

```yaml
blocks:
  github:
    use: github
    spaces:
      acme/app:
        mode:
          mode: watch
          target: agent:beta
        project: demo
```

Call `connect(what="github")` again for any remaining App ID or private key links. Ask me to save the App ID and full `.pem` file through those links. If I switch from a personal access token to an app, use `choose={"credential":"app"}`. If I choose a fine-grained personal access token for writes, use `choose={"credential":"pat"}`. Check the setup with `github_status`. Ask me to check Advanced > Recent deliveries in the app settings.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

If I choose triage or assist, bind the target with `space_mode`. Use my repository and the target from `targets`.

```python
space_mode(block="github", space="acme/app", mode="triage", target="agent:beta")
```

Use `mode="assist"` for assist mode. Omit `target` to keep the binding. Set `target=""` to clear it. The linked project route is the fallback. Check the bound target with `modes_status`. The console mode row shows the bound agent. In triage, SwB shows the default-agent hint only when it uses the fallback.

If I want outside senders to reach the agent, call `visibility_list` for the exact resource names. SwB refuses unknown names in `visibility_set`. The address `agent:beta` points to the mailbox. The rule `target:beta` controls who can reach it. Use `shared` on both resources or the same verified sender ID on both. Ask me who should have access.

```python
visibility_set(resource="channel:github/acme/app", visibility=["github:1234"])
visibility_set(resource="target:beta", visibility=["github:1234"])
```

When `connect` reports `connected`, ask me to create a test issue in the connected repository. Check the delivery. In Watch mode, SwB puts a digest in the target mailbox at the interval set by `modes.digest_minutes` or at 50 events. As the owner, I can read every digest in the console **Inbox** or through MCP `inbox` and `wait`. The digest gives the event count and each event's author, excerpt and link when available. Its trust is `external`. Watch keeps agents asleep. SwB delivers digests through inbox reads instead of push events.
