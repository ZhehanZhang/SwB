"""The one Starlette app: core pages and endpoints, OAuth, and whatever routes each origin serves."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from . import pages
from .guard import Guard, RateLimiter, client_ip, reject
from .origins.mcp import PRINCIPAL_SCOPE, principal_of

if TYPE_CHECKING:
    from .core import Switchboard

PROTECTED = ("/mcp", "/inbox/")
HEARTBEAT_S = 15.0
POLL_MAX_S = 55.0


class BearerGate:
    """Requires a valid OAuth access token or bearer key on /mcp and /inbox/; puts the principal on the ASGI scope.

    Wrong tokens are audited and count against the caller's IP: after too many, that IP gets 429 for a while,
    even with a good token, so guessing keys is slow. Requests without any token are the normal OAuth discovery
    step and are neither audited nor counted.
    """

    def __init__(self, app: Any, core: "Switchboard", limiter: RateLimiter):
        self.app = app
        self.core = core
        self.limiter = limiter

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and (scope["path"] == "/mcp" or scope["path"].startswith(PROTECTED)):
            scope.pop(PRINCIPAL_SCOPE, None)
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            auth = headers.get("authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
            ip = client_ip(scope)
            wait = self.limiter.blocked("bearer_fail", ip)
            if wait is not None:
                await reject(send, 429, "slow_down", "too many failed sign-ins from this address; try again later", wait)
                return
            principal = self.core.auth.verify_bearer(token)
            if principal is None and token:
                self.limiter.hit("bearer_fail", ip)
                self.core.audit.record(origin="http", source="-", client=ip, destination="core", action="auth_failure", outcome="refused",
                                       target=scope["path"], detail="invalid or expired bearer token")
            if principal is None:
                challenge = self.core.auth.challenge_header("invalid_token" if token else None)
                body = json.dumps({"error": "invalid_token" if token else "unauthorized", "error_description": "sign in through OAuth or send a valid bearer key"}).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"www-authenticate", challenge.encode())]})
                await send({"type": "http.response.body", "body": body})
                return
            scope[PRINCIPAL_SCOPE] = principal
        await self.app(scope, receive, send)


def _cursor(request: Request) -> int | None:
    raw = request.query_params.get("after") or request.headers.get("last-event-id")
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def create_app(core: "Switchboard") -> Any:
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse(core.health())

    async def admin(request: Request) -> Response:
        """The owner's page at the root: status, routes, projects and recent refusals. Cloudflare Access plus SSO in front, the JWT checked here."""
        if not core.cfg.access_team_domain:
            return pages.message_page("Not available", "The admin page needs Cloudflare Access in front of it (auth.cloudflare_access).", 403)
        email = await core.auth.owner(request)
        if email is None:
            return pages.message_page("Not allowed", "This page needs the owner's SSO through Cloudflare Access.", 403)
        changes = [c for c in core.audit.recent(limit=100) if c["action"] in ("route_set", "route_remove", "auth_failure", "rate_limited")][:15]
        return pages.admin_page(core.health(), email, [r.as_dict() for r in core.routes.list()], [p.as_dict() for p in core.projects.list(include_archived=True)], changes)

    async def inbox_poll(request: Request) -> JSONResponse:
        """Long-poll for the Claude Code channel and scripts: items for ?agent= (default every agent) after ?after=."""
        p = principal_of(request)
        try:
            timeout = min(POLL_MAX_S, max(0.0, float(request.query_params.get("timeout", "25"))))
        except ValueError:
            timeout = 25.0
        since = _cursor(request)
        out = await core.inbox.wait(owner=request.query_params.get("agent") or None, since=since if since is not None else core.inbox.head(),
                                    timeout=timeout, reader=f"channel:{p.source}", advance=False)
        return JSONResponse(out)

    async def inbox_events(request: Request) -> Response:
        """Server-Sent Events: one 'item' frame per inbox item, id = its cursor, so Last-Event-ID resumes after a drop."""
        p = principal_of(request)
        agent = request.query_params.get("agent") or None
        start = _cursor(request)

        async def frames() -> AsyncIterator[bytes]:
            cursor = start if start is not None else core.inbox.head()
            yield f"retry: 3000\nevent: ready\ndata: {json.dumps({'cursor': cursor, 'agent': agent})}\n\n".encode()
            while True:
                got = await core.inbox.wait(owner=agent, since=cursor, timeout=HEARTBEAT_S, reader=f"channel:{p.source}", advance=False)
                for item in got["items"]:
                    yield f"id: {item['cursor']}\nevent: item\ndata: {json.dumps(item)}\n\n".encode()
                if got["timed_out"]:
                    yield b"event: heartbeat\ndata: {}\n\n"
                cursor = got["cursor"]
                if await request.is_disconnected():
                    return

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @asynccontextmanager
    async def lifespan(app: Starlette):  # type: ignore[no-untyped-def]
        async with AsyncExitStack() as stack:
            for origin in core.origins.values():
                cm = origin.lifespan()
                if cm is not None:
                    await stack.enter_async_context(cm)
            await core.start()
            try:
                yield
            finally:
                await core.aclose()

    origin_routes = [r for o in core.origins.values() for r in o.routes()]
    app: Any = Starlette(
        routes=[
            Route("/", admin, methods=["GET"]),
            Route("/healthz", healthz),
            Route("/inbox/poll", inbox_poll, methods=["GET"]),
            Route("/inbox/events", inbox_events, methods=["GET"]),
            *pages.routes(),
            *core.auth.routes(),
            *origin_routes,
        ],
        lifespan=lifespan,
    )
    for origin in core.origins.values():
        app = origin.wrap(app)
    limiter = RateLimiter()

    def refused(ip: str, path: str, why: str) -> None:
        core.audit.record(origin="http", source="-", client=ip, destination="core", action="rate_limited", outcome="refused", target=path, detail=why)

    gate = Guard(BearerGate(app, core, limiter), limiter, on_refusal=refused)
    gate.starlette = app
    return gate
