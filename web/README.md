# SwB web

Use Node 20.19+ or 22.12+ and run these commands from `web/`:

```sh
npm ci
npm run dev
npm run typecheck
npm run build
```

Vite serves the app at `/` in development. It forwards API requests, claim forms, shared page assets, and legacy redirects to `http://127.0.0.1:8000`. `/login` and `/inbox` are client pages. Inbox feeds use `/inbox/poll` and `/inbox/events` on the server. The proxy preserves authentication, the request origin, and write checks.

The production build writes `../src/switchboard/static/app/index.html` and hashed assets under `../src/switchboard/static/app/assets/`. The HTML and CSS address these assets under `/app-assets/assets/`; client routes still start at `/`. To build without writing into the Python source tree, run `npm run build -- --outDir dist`.

## Shell and components

- The desktop rail and mobile tabs share one navigation list. Search or jump opens with Cmd/Ctrl-K and searches sections and project names.
- The console reads `GET /auth/session` for `signed_in`, `via`, `csrf`, and `methods`. The request helper sends `X-CSRF-Token` on protected writes. A signed-out session leads to the standalone `/login` screen.
- Shared primitives live in `src/components`. Logo tiles use local SVG paths from Simple Icons (CC0) and Lobe Icons (MIT).
