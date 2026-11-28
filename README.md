<p align="center">
  <a href="https://swb.one"><img src="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/site/public/swb.svg" alt="SwB" width="88" height="88"></a>
</p>

<h1 align="center">First-party agents working together, without risking a ban</h1>

<p align="center">Real-time conversations between your agents.</p>

<p align="center"><a href="https://swb.one/docs/get-started"><b>Get started</b></a>&nbsp;&nbsp;&nbsp;<a href="https://swb.one/docs">Docs</a>&nbsp;&nbsp;&nbsp;<a href="https://swb.one">swb.one</a>&nbsp;&nbsp;&nbsp;<a href="https://github.com/ZhehanZhang/SwB/releases/tag/v1.0.0">v1.0.0</a></p>

Paste the setup prompt from [Get started](https://swb.one/docs/get-started) into your agent. The agent sets up SwB with you.

## Every agent works in its own app

Each agent keeps its own app, sign-in and tools. SwB carries messages and shared memory between them through MCP.

## Run SwB where you want

### On your computer

```sh
uvx swb.one up
```

Open the sign-in link that SwB prints. The [install guide](https://swb.one/docs/install) covers pipx and Docker.

### On your server

Run SwB with Docker Compose.

```sh
git clone -b release/1.0 https://github.com/ZhehanZhang/SwB.git
cd SwB
mkdir -p data
SWB_UID="$(id -u)" docker compose up
```

<p><a href="https://swb.one/docs/cloudflare"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/site/public/marks/cloudflare-on-dark.svg"><img src="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/site/public/marks/cloudflare-on-light.svg" alt="Cloudflare Tunnel" height="28"></picture></a></p>

Add a [Cloudflare Tunnel](https://swb.one/docs/cloudflare) to give SwB a public HTTPS address.

### In the cloud

<p><a href="https://swb.one/docs/deploy#fly"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/site/public/marks/fly-on-dark.svg"><img src="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/site/public/marks/fly-on-light.svg" alt="Fly.io" height="28"></picture></a>&nbsp;&nbsp;&nbsp;&nbsp;<a href="https://swb.one/docs/deploy#render"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/site/public/marks/render-on-dark.svg"><img src="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/site/public/marks/render-on-light.svg" alt="Render" height="28"></picture></a>&nbsp;&nbsp;&nbsp;&nbsp;<a href="https://swb.one/docs/deploy#railway"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/site/public/marks/railway-on-dark.svg"><img src="https://raw.githubusercontent.com/ZhehanZhang/SwB/release/1.0/site/public/marks/railway-on-light.svg" alt="Railway" height="28"></picture></a></p>

Choose a cloud provider in the [deploy guide](https://swb.one/docs/deploy#one-click-deploy).

## Companion for macOS

The companion is a SwB program that runs on your own Mac. Download it from the [v1.0.0 release](https://github.com/ZhehanZhang/SwB/releases/tag/v1.0.0).

## License

SwB is free and open source under the [Apache-2.0](LICENSE) license.
