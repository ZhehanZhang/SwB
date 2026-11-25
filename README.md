# capy-bridge

A self-hosted remote MCP server that gives every assistant Owner uses (the Claude app, ChatGPT, Capy itself, and
anything else that speaks MCP) one shared environment:

1. **Shared memory and context**: a SQLite store with full-text search that every assistant reads and writes through
   tools, each entry tagged with the assistant that wrote it.
2. **Offloading to Capy**: from a chat, hand a task to Capy (cloud dev machines, repos, PRs, the Captain and its crew),
   then follow it to the result.

It runs as a docker compose app on your-host, behind a Cloudflare tunnel at `https://bridge.example.com`.

## What "shared memory" means, honestly

Claude's and ChatGPT's built-in memories are private to each product. **No MCP server can read or write them**, and
this one doesn't try. "Sync" here means a third store that every assistant uses through the `memory_*` and
`context_pack` tools, plus a short instruction snippet in each product (below) telling the model to load the briefing
at the start and save durable facts as it goes. If an assistant isn't told to use the tools, it won't.

## Tools

| Tool | What it does |
| - | - |
| `context_pack` | Session briefing: profile, recent decisions, project notes, active Capy work, open asks |
| `memory_write` | Create or update an entry (`namespace`, `title`, `body`, optional `key` for upserts, `tags`, `mode=append`) |
| `memory_search` | FTS5 search, optional namespace prefix |
| `memory_read` / `memory_list` / `memory_delete` | Read one entry with its edit history, list newest first with namespace counts, soft-delete |
| `capy_delegate` | Send a task to the Captain (default) or start a thread in an allowlisted project, optionally in the Captain's crew, with files |
| `capy_status` | Thread status, needs-you and ask, PR links, latest messages |
| `capy_list` | Tasks delegated through the bridge (live status), the Captain's crew, or a project's threads |
| `capy_reply` | Follow-up into a thread (steer, queue or interrupt) |
| `capy_result` | Final reply in full, PR links, download links for delivered files, optional transcript tail |
| `capy_captain_digest` | The Captain's DIGEST.md / ONGOING.md / NOTES.md plus crew threads that need the owner |
| `bridge_status` | Who you are signed in as, allowed targets and repos, today's delegation count, misconfiguration |

Namespaces are lowercase paths up to three levels: `profile`, `decisions`, `notes`, `projects/<name>`. The writing
assistant (`claude`, `chatgpt`, `capy`, ...) comes from the credential, never from a tool argument, so an assistant
can't write as another one. Every write keeps a revision.

The Capy API has no pull-request field on threads, so PR links are read from the agent's messages.

## Authentication

The server is its own single-owner OAuth 2.1 authorization server, built to the stricter of Claude's and ChatGPT's
requirements (checked November 2026):

- Protected resource metadata (RFC 9728) at `/.well-known/oauth-protected-resource`, and a `401` with
  `WWW-Authenticate: Bearer resource_metadata=...` on `/mcp`, which Claude needs to start sign-in.
- Authorization server metadata (RFC 8414) with PKCE `S256` only, `client_id_metadata_document_supported`, `none`
  among token auth methods (Claude only picks CIMD then), a `registration_endpoint` for DCR (RFC 7591), and RFC 9207
  `iss` in every authorization response, which lets ChatGPT use its stable redirect URI.
- Clients: Client ID Metadata Documents (Claude's "published identity", ChatGPT's preference) from allowlisted hosts
  only, or Dynamic Client Registration. Redirect URIs are limited to Claude's, ChatGPT's and loopback (Claude Code).
- The `resource` parameter is checked throughout and tokens are bound to `<public_url>/mcp`.
- Access tokens last 1 hour; refresh tokens 30 days, rotated on use, and a replayed refresh token (after a 60 s race
  grace) revokes its whole family. Tokens are stored only as SHA-256 hashes.

**Only Owner can approve a sign-in.** The consent page asks for `CAPY_BRIDGE_OWNER_PASSWORD`; five wrong tries from
one IP (or 20 overall) lock it for 15 minutes. Optionally (`auth.cloudflare_access`), `/authorize` also verifies the
Cloudflare Access JWT and requires an owner email, so a stranger never even sees the form. Cloudflare Access sits only
on `/authorize`; `/mcp`, `/token`, `/register` and the metadata must stay reachable by Anthropic's and OpenAI's
servers.

Non-OAuth clients (Capy, scripts) use static bearer keys: `CAPY_BRIDGE_KEY_<NAME>` in `.env`, where `<NAME>` becomes
the source tag.

## Delegation guardrails

- **Allowlist**: `capy_delegate` targets are `captain` or a `delegate.projects` alias; anything else is refused. A
  task may name repos, and each must be on that project's `repos` list (for the Captain, any listed repo). The Capy
  API can't pin a thread to repos, so the hard boundary is the API key's principal: use a service user whose project
  access is the same allowlist.
- **Thread scope**: `capy_status`, `capy_result` and `capy_reply` only touch the Captain, its crew, and threads in
  allowlisted projects.
- **Daily cap**: `delegate.daily_cap` successful delegations per calendar day in `delegate.timezone`.
- **Log**: every delegation and reply, including refusals, is recorded with time, assistant, OAuth client, target and
  thread (`docker compose exec capy-bridge capy-bridge log`). Identical retries the same day are deduplicated through
  the API's `requestId` / `clientKey`.
- The Capy API key is only ever sent as a header to api.capy.ai; it is not logged, and access logs drop query strings.

## Running it

```bash
cp .env.example .env && chmod 600 .env        # the owner types the values
cp config/capy-bridge.example.yaml config/capy-bridge.yaml
docker compose up -d --build                  # MCP on http://127.0.0.1:8870/mcp
docker compose --profile tunnel up -d         # adds cloudflared (TUNNEL_TOKEN in .env)
curl -s http://127.0.0.1:8870/healthz
```

Without `CAPY_API_KEY` the server runs **degraded**: memory works, the offload tools answer that the key is missing,
and `/healthz` and the status page at `/` say what's off. Tests run in the image build: `docker build --target test .`.

Markdown export: `docker compose exec capy-bridge capy-bridge export /data/export`. Setting `memory.mirror_dir`
rewrites the export on every change (atomic writes, plus `INDEX.md`); it's one-way, so edits to the files are not read
back.

The consent and status pages follow the owner's sites (example.com, status.example.com, id.example.com): the same
stone grounds and type, hairline cards, pill buttons, the Arc backdrop, the signature, and the shared
`siteAppearance` theme cookie. The CSS is a hand port of example-org/id-site's Tailwind components, served from `/assets`.

## Connecting the assistants

- **Claude** (claude.ai, Desktop, mobile): Settings → Connectors → Add custom connector → URL
  `https://bridge.example.com/mcp`, sign in with OAuth (published identity or automatic registration both work), approve
  with the owner password. Enable it per chat or in a Project.
- **ChatGPT**: Settings → Apps & Connectors → Advanced → Developer mode on, then Create → URL
  `https://bridge.example.com/mcp`, authentication OAuth.
- **Claude Code**: `claude mcp add --transport http capy-bridge https://bridge.example.com/mcp`.
- **Capy**: add an MCP server with URL `https://bridge.example.com/mcp` and header
  `Authorization: Bearer <CAPY_BRIDGE_KEY_CAPY>`.

## Instruction snippets

**Claude Project instructions** (or Settings → Profile → personal preferences):

> You have the capy-bridge connector, a memory shared with my other assistants and Capy. At the start of a
> conversation, call context_pack (pass project=<name> when the chat is about one). When I state a durable preference,
> fact or decision, save it with memory_write (profile, decisions or projects/<name>; use a key for things that get
> updated) and tell me in one line. Search memory before asking me something I may have said before. For work that
> needs a real machine, a repo, a PR or a long run, offer to hand it to Capy with capy_delegate, then follow it with
> capy_status and capy_result.

**ChatGPT custom instructions** ("What would you like ChatGPT to know / how should it respond"):

> When the capy-bridge connector is enabled: call context_pack first. Save durable preferences, facts and decisions
> with memory_write (namespaces profile, decisions, projects/<name>) and say so briefly; search memory before asking me
> something I may have said. Offload coding, repo, PR and long-running work to Capy with capy_delegate and report its
> status with capy_status / capy_result. Your own ChatGPT memory is separate; the shared one is capy-bridge.

**Capy (AGENTS.md or scope instructions)**:

> Shared memory with the owner's other assistants lives in the capy-bridge MCP server: read context_pack at the start of
> work, and record decisions and durable facts with memory_write (source is tagged automatically).

## Layout

```
src/capy_bridge/
  app.py         Starlette app, bearer gate on /mcp, health and status page
  oauth.py       OAuth 2.1 authorization server (DCR, CIMD, PKCE, refresh rotation, Access check)
  mcp_server.py  MCP tools (MCP Python SDK, streamable HTTP, stateless)
  core.py        delegation guardrails, Capy reads, context pack
  capy_api.py    Capy public API client
  memory.py      memory store logic and markdown export
  store.py       SQLite schema (memories + FTS5, OAuth state, delegation log)
  pages.py, static/  sign-in, status pages and their assets
```
