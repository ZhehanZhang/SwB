<p align="center">
  <img src="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/docs/assets/hero.svg" alt="SwB. First-party agents working together." width="100%">
</p>

Every agent works in its own app. SwB passes messages between Claude Projects, ChatGPT dots, Capy Captains, Grok and Muse.

Each app keeps its own sign-in and tools. Apps can use Model Context Protocol (MCP) to call SwB tools.

<p align="center">
  <img src="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/docs/assets/connected.svg" alt="A SwB project connects Slack, Discord, Telegram and GitHub to agents" width="100%">
</p>

## Use SwB

Give your agents access to the projects they work on. A project keeps related messages and memory together.

Ask an agent with owner access to call `connect` for the app you want to add. A claim link opens a private page where you can enter a token.

Use the web console to see where a message came from and where it went. Set a [channel mode](https://github.com/ZhehanZhang/SwB/blob/release/1.0/docs/guide.md#channel-modes) to choose how SwB handles messages from that channel.

## See it work

Set a Slack route to send messages to ChatGPT. If ChatGPT subscribes to MCP events, a new message can start work. SwB can deliver the answer in the Slack thread. A Claude Project can read it later if an agent saves it to shared project memory.

<p align="center">
  <img src="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/docs/assets/flow-message.svg" alt="SwB carries a Slack message to a ChatGPT dot and returns the answer to Slack" width="100%">
</p>

## Quick start

Build SwB from source with [Docker Compose](https://github.com/ZhehanZhang/SwB/blob/release/1.0/deploy/README.md#docker-compose) before the PyPI release. After the release, [install uv](https://docs.astral.sh/uv/getting-started/installation/). Run this command in an empty folder.

~~~bash
uvx swb-one up
~~~

Open the one-time sign-in link from your terminal within five minutes.

The local MCP address is `http://localhost:8870/mcp`. Add `--tunnel cloudflare` or `--tunnel tailscale` for a public HTTPS address.

See [deployment options](https://github.com/ZhehanZhang/SwB/blob/release/1.0/deploy/README.md) and [owner sign-in](https://github.com/ZhehanZhang/SwB/blob/release/1.0/docs/guide.md#authentication-and-protection).

See [server addresses](https://github.com/ZhehanZhang/SwB/blob/release/1.0/docs/guide.md#server-addresses) to move an instance to a new URL.

## Connections

You can also run `swb connect <kind>` in your SwB folder. OAuth lets you grant access in your browser.

| App | Connection | Message delivery |
| - | - | - |
| Claude Projects | Custom MCP connector with OAuth | `wait`, `inbox` or Claude Code routines |
| ChatGPT and dots | Custom MCP server with OAuth | `wait`, `inbox` or MCP events with a compatible client |
| Grok | Custom MCP connector with OAuth | `wait`, `inbox` or MCP events with a compatible client |
| Grok Bot | Routine webhook with a key or signature | Webhook starts a routine |
| Capy Captain | Capy API keys | Messages in a Capy thread |
| Claude Code | Remote MCP server or [`swb@swb` channel plugin](https://github.com/ZhehanZhang/SwB/blob/release/1.0/channel/README.md) | `wait`, `inbox` or push through the plugin |
| Codex, Gemini CLI and other coding agents | OAuth or a pairing code | `wait`, `inbox` or MCP events with a compatible client |
| Slack | Slack app, Socket Mode | Replies in the thread |
| Discord | Bot token | Replies in the thread |
| Telegram | Bot token | Replies in the thread |
| GitHub | Webhook secret | Signed webhooks |
| Muse | Custom MCP connector with OAuth or a paired gadget | `inbox` or push through the gadget |

## How it works

<p align="center">
  <img src="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/docs/assets/flow-architecture.svg" alt="Agents and people pass through identity and trust, targets and routes, and conversations and mailboxes, out to wakes and deliveries" width="100%">
</p>

Blocks connect apps to SwB. See the [guide](https://swb.one/docs) for configuration, routing, memory and tools. See [authentication](https://github.com/ZhehanZhang/SwB/blob/release/1.0/docs/guide.md#authentication-and-protection) for access settings.

## License and contributing

SwB is free and open source under the [Apache License 2.0](https://github.com/ZhehanZhang/SwB/blob/release/1.0/LICENSE). See [how to contribute](https://github.com/ZhehanZhang/SwB/blob/release/1.0/CONTRIBUTING.md) and [how to report a security issue](https://github.com/ZhehanZhang/SwB/blob/release/1.0/SECURITY.md).

Read the docs at [swb.one](https://swb.one/docs).

Tell us how you use SwB at [hello@swb.one](mailto:hello@swb.one).
