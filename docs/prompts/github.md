Setup guide: https://swb.one/docs/setup/github

Help me connect GitHub to SwB, my self-hosted switchboard for agents and channels. You reach SwB as an MCP server.

If the SwB tools are missing in this app, help me add SwB as a custom MCP server. Use my SwB address, such as https://swb.example.com/mcp. In Claude, use Customize > Connectors > Add > Custom > Web. In ChatGPT, open https://chatgpt.com/plugins in a browser. Use + > Add custom MCP server. In Grok, use grok.com > Connectors > New Connector > Custom. In other apps, use the MCP server settings. Wait for me to confirm.

Call connect(what="github"). Do each step marked "you" yourself. Give me each step marked "person" one at a time with the exact click path. Wait for me to say done after each step. Call connect(what="github") again to check the connection.

I use profile picture > Settings > Developer settings > GitHub Apps > New GitHub App. I enter the GitHub App name. I enter the Homepage URL. Under Webhook, I keep Active selected. I set Webhook URL to my SwB address with `/github/webhook` at the end. I generate a random 64-character secret in my password manager. I enter it in Webhook secret.

Under Repository permissions, I set Issues to Read & write. I set Pull requests to Read & write. I set Contents to Read-only. I set Checks to Read-only. I set Actions to Read-only. I subscribe to `issues`, `issue_comment`, `pull_request`, `pull_request_review`, `pull_request_review_comment`, `pull_request_review_thread`, `push`, `check_run`, `check_suite` and `workflow_run`. I select Only on this account. I click Create GitHub App. Under Private keys, I click Generate a private key. I use Install App > Install. I choose Only select repositories. I click Install.

For agent writes, call `connect(what="github", choose={"credential":"app"})`. I save the App ID. I save the `.pem` file. Add the repositories under `blocks.github.spaces` or `blocks.github.routes` in `config/switchboard.yaml`. If I need to edit the file myself, give me the YAML. Check the setup with github_status. Ask me to check Advanced > Recent deliveries in the app settings.

Ask me to enter tokens only through the claim link that connect returns. Keep tokens out of this chat.

When connect reports connected, send a test message. Show me the reply. Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.
