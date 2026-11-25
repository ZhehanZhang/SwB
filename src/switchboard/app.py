"""The one Starlette app: core pages and endpoints, OAuth, and whatever routes each origin serves."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from . import pages
from .guard import Guard, RateLimiter, client_ip, reject
from .origins.mcp import PRINCIPAL_SCOPE, principal_of

if TYPE_CHECKING:
    from .core import Switchboard

PROTECTED = ("/mcp", "/inbox/")
HEARTBEAT_S = 15.0
POLL_MAX_S = 55.0
AVATAR_MAX_BYTES = 2 * 1024 * 1024


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
        self._anonymous: dict[str, float] = {}

    def _first_anonymous(self, ip: str) -> bool:
        """Token-less requests are logged once per address per 10 minutes, since OAuth discovery starts with one."""
        now = self.core.clock()
        if now - self._anonymous.get(ip, 0) < 600:
            return False
        if len(self._anonymous) > 1000:
            self._anonymous.clear()
        self._anonymous[ip] = now
        return True

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and (scope["path"] == "/mcp" or scope["path"].startswith(PROTECTED)):
            scope.pop(PRINCIPAL_SCOPE, None)
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            auth = headers.get("authorization", "")
            bare = auth.strip()
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else (bare if bare and " " not in bare else None)
            ip = client_ip(scope)
            wait = self.limiter.blocked("bearer_fail", ip)
            if wait is not None:
                await reject(send, 429, "slow_down", "too many failed sign-ins from this address; try again later", wait)
                return
            principal = self.core.auth.verify_bearer(token)
            if principal is None and token:
                self.limiter.hit("bearer_fail", ip)
                self.core.audit.record(origin="http", source="-", client=ip, destination="core", action="auth_failure", outcome="refused",
                                       target=scope["path"], detail=f"unknown or expired bearer token ({token_shape(token)}); agent {headers.get('user-agent', '-')[:80]}")
            elif auth and not token:
                self.core.audit.record(origin="http", source="-", client=ip, destination="core", action="auth_failure", outcome="refused",
                                       target=scope["path"], detail=f"Authorization header is not 'Bearer <token>' (scheme {auth.split(' ')[0][:20]!r}); agent {headers.get('user-agent', '-')[:80]}")
            elif not auth and self._first_anonymous(ip):
                self.core.audit.record(origin="http", source="-", client=ip, destination="core", action="auth_missing", outcome="refused",
                                       target=scope["path"], detail=f"no Authorization header (OAuth discovery, or a key that wasn't sent); agent {headers.get('user-agent', '-')[:80]}")
            if principal is None:
                challenge = self.core.auth.challenge_header("invalid_token" if token else None)
                body = json.dumps({"error": "invalid_token" if token else "unauthorized", "error_description": "sign in through OAuth or send a valid bearer key"}).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"www-authenticate", challenge.encode())]})
                await send({"type": "http.response.body", "body": body})
                return
            scope[PRINCIPAL_SCOPE] = principal
        await self.app(scope, receive, send)


def token_shape(token: str) -> str:
    """What a wrong token looked like, without any of it: length and the usual paste mistakes."""
    notes = [f"{len(token)} chars"]
    if any(ch.isspace() for ch in token):
        notes.append("contains whitespace")
    if token[:1] in "\"'" or token[-1:] in "\"'":
        notes.append("quoted")
    if token.lower().startswith("bearer"):
        notes.append("starts with 'Bearer' twice")
    if "=" in token[:40]:
        notes.append("looks like NAME=value")
    return ", ".join(notes)


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
        extra = ""
        if core.slack is not None:
            notices = {"approved": "Approved and sent.", "rejected": "Rejected.", "send-failed": "Approved, but Slack refused the send; see the audit log.",
                       "decide-refused": "That request was already decided or has expired.",
                       "avatar-saved": "Avatar saved. New posts use it; Slack may take a minute to show it.",
                       "avatar-refused": "Avatar not saved: send a picture of the persona's file type (see its file name), 2 MB at most."}
            extra = pages.slack_body(core.slack.status(), core.slack.store.list_requests(status="pending", limit=50), csrf(email),
                                     notices.get(request.query_params.get("notice", "")), core.slack.persona_rows())
        return pages.admin_page(core.health(), email, [r.as_dict() for r in core.routes.list()], [p.as_dict() for p in core.projects.list(include_archived=True)], changes, extra)

    def csrf(email: str) -> str:
        return hmac.new(core.auth.secret, f"switchboard-csrf:{email}".encode(), hashlib.sha256).hexdigest()

    async def slack_decide(request: Request) -> Response:
        """The owner approves or rejects a Slack write from the root page: Access JWT for an owner plus a CSRF token."""
        from .slack.writes import Actor, WriteError

        if core.slack is None or not core.cfg.access_team_domain:
            return pages.message_page("Not available", "Slack approvals here need Slack and Cloudflare Access configured.", 403)
        email = await core.auth.owner(request)
        if email is None:
            return pages.message_page("Not allowed", "This page needs the owner's sign-in through Cloudflare Access.", 403)
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf") or ""), csrf(email)):
            return pages.message_page("Not allowed", "The form expired; reload the page and try again.", 403)
        verb = request.path_params["verb"]
        if verb not in ("approve", "reject"):
            return Response(status_code=404)
        try:
            req = await core.slack.gate.decide(request.path_params["rid"], verb == "approve", Actor("admin", email))
        except WriteError:
            return RedirectResponse("/?notice=decide-refused", status_code=303)
        outcome = "rejected" if verb == "reject" else ("approved" if req["status"] == "sent" else "send-failed")
        return RedirectResponse(f"/?notice={outcome}", status_code=303)

    async def avatar(request: Request) -> Response:
        """Persona avatars for Slack bot posts; Slack fetches them, so this path is public (no Access)."""
        if core.slack is None:
            return Response(status_code=404)
        name = request.path_params["name"]
        path = core.slack.find_avatar(name)
        if path is None:
            return Response(status_code=404)
        return Response(path.read_bytes(), media_type="image/png" if name.endswith(".png") else ("image/gif" if name.endswith(".gif") else "image/jpeg"),
                        headers={"Cache-Control": "public, max-age=3600"})

    async def avatar_upload(request: Request) -> Response:
        """The owner replaces a persona's picture from the root page: Access JWT for an owner plus a CSRF token."""
        if core.slack is None or not core.cfg.access_team_domain:
            return pages.message_page("Not available", "Avatar uploads need Slack and Cloudflare Access configured.", 403)
        email = await core.auth.owner(request)
        if email is None:
            return pages.message_page("Not allowed", "This page needs the owner's sign-in through Cloudflare Access.", 403)
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf") or ""), csrf(email)):
            return pages.message_page("Not allowed", "The form expired; reload the page and try again.", 403)
        persona = core.slack.config.personas.get(request.path_params["pid"])
        if persona is None or not persona.avatar:
            return pages.message_page("Not available", "That persona has no avatar file; it uses an icon_url or emoji from the config.", 404)
        upload = form.get("file")
        data = await upload.read(AVATAR_MAX_BYTES + 1) if hasattr(upload, "read") else b""
        kind = "png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "jpg" if data.startswith(b"\xff\xd8\xff") else "gif" if data[:6] in (b"GIF87a", b"GIF89a") else None
        want = persona.avatar.rsplit(".", 1)[1].lower().replace("jpeg", "jpg")
        if not data or len(data) > AVATAR_MAX_BYTES or kind != want:
            return RedirectResponse("/?notice=avatar-refused", status_code=303)
        core.slack.avatar_dir.mkdir(parents=True, exist_ok=True)
        tmp = core.slack.avatar_dir / f".{persona.avatar}.tmp"
        tmp.write_bytes(data)
        tmp.replace(core.slack.avatar_dir / persona.avatar)
        core.slack.store.audit(f"admin:{email}", "avatar_uploaded", persona=persona.id, file=persona.avatar, bytes=len(data))
        return RedirectResponse("/?notice=avatar-saved", status_code=303)

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
            Route("/slack/requests/{rid}/{verb}", slack_decide, methods=["POST"]),
            Route("/avatars/{name}", avatar, methods=["GET"]),
            Route("/slack/personas/{pid}/avatar", avatar_upload, methods=["POST"]),
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
