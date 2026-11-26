# Switchboard web

Use Node 20.19+ or 22.12+ and run these commands from `web/`:

```sh
npm ci
npm run dev
npm run typecheck
npm run build
```

Vite serves the app at `/` in development. It forwards `/api`, `/classic`, claim and form routes, `/healthz`, and classic assets to Switchboard at `http://127.0.0.1:8000` without rewriting the request's Origin. `/inbox` is a client page; only `/inbox/poll` and `/inbox/events` are forwarded. The proxy does not bypass Cloudflare Access or the server's write-origin checks.

The production build writes `../src/switchboard/static/app/index.html` and hashed assets under `../src/switchboard/static/app/assets/`. The HTML and CSS address these assets under `/app-assets/assets/`; client routes still start at `/`. To build without writing into the Python source tree, run `npm run build -- --outDir dist`.

## Shell and components

- Canvas uses both `/` and `/canvas`. Canvas, Agents, Inbox, Provision, Projects, Memory, and Audit are heading-only placeholders, including their section subpaths. The command menu navigates to these same pages.
- The health lamp reads `GET /api/health` (`status: "ok" | "degraded"`, `problems: string[]`, optional `commit`). The account menu reads `GET /api/session` (`email` and `csrf`); only the email is displayed. When the session succeeds, Sign out goes to Cloudflare Access at `/cdn-cgi/access/logout`.
- The theme uses the existing `siteAppearance` cookie, with `localStorage.theme` as a fallback and light, dark, or system choices. The cookie is shared across subdomains of `example.com` and `example.com`.
- `src/components/console` holds the block, lamp, jack, lock, and SVG cord primitives. `DataTable` takes TanStack column definitions and actual rows; pass `DataState` props for loading or errors. `Sheet` is the inspector dialog, with a desktop side sheet and a mobile bottom sheet. `ClaimLink` takes a server-issued `/claim/…` or `/routines/claim/…` URL and its ISO expiry; it does not mint a link or collect a token.
