"""The one Starlette app: core pages and endpoints, OAuth, pairing codes, and whatever routes each block serves."""

from __future__ import annotations

import hmac
import json
import re
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Mount, Route

from . import pages
from .api_about import release_version
from .api_display import SLACK_PROFILE_PREFIX
from .claims import CLAIM_PATH, ClaimError, ClaimUnavailable, GroupFieldError
from .config import REQUEST_BASE
from .guard import Guard, RateLimiter, client_ip, reject
from .owner_login import OwnerLogin
from .blocks.mcp import PRINCIPAL_SCOPE, principal_of
from .muse_protocol import BRIDGE_PATH
from .sessions import Owner, login_redirect
from .web import web_routes

if TYPE_CHECKING:
    from .core import Switchboard

INBOX_FEEDS = ("/inbox/poll", "/inbox/events")
HEARTBEAT_S = 15.0
POLL_MAX_S = 55.0
AVATAR_MAX_BYTES = 2 * 1024 * 1024
TOKEN_CLAIM_MESSAGES = {
    "Enter it on a single line, at most 8 KiB.": "Enter a single-line value up to 8 KiB",
    "Enter a public HTTPS URL without credentials or a fragment.": "Enter a public HTTPS webhook URL",
    "That doesn't look like the right kind of token.": "Use a bot token",
}
ROUTINE_CLAIM_MESSAGES = {
    "this link isn't valid; ask the Claude session for a new one": "Request a new link",
    "this link was replaced by a newer registration; use the latest link": "Use the latest link",
    "this link was already used": "Link already used",
    "this link has expired; ask the Claude session to call routine_register again": "Link expired",
    "that doesn't look like a routine token (sk-ant-oat01-…)": "Use a Claude routine token",
}


class RequestBase:
    """Records which public URL (public_url or one of public_url_aliases) a request reached, for issuer and origin checks."""

    def __init__(self, app: Any, core: "Switchboard"):
        self.app = app
        self.core = core

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), None)
        token = REQUEST_BASE.set(self.core.cfg.base_for_host(host))
        try:
            await self.app(scope, receive, send)
        finally:
            REQUEST_BASE.reset(token)


class BearerGate:
    """Requires a valid OAuth access token or bearer key on /mcp and the inbox feeds; adds the principal to the ASGI scope.

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
        path = scope.get("path", "")
        inbox_feed = path.rstrip("/") in INBOX_FEEDS
        muse_bridge = path == BRIDGE_PATH
        if scope["type"] == "http" and (path.startswith("/mcp") or inbox_feed or muse_bridge):
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
            if muse_bridge:
                muse = self.core.blocks.get("muse")
                principal = (muse.verify_bridge(token) if muse and muse.manifest.id == "muse"
                             and auth.lower().startswith("bearer ") else None)
            else:
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
            if principal.kind == "inbox_key" and not inbox_feed:
                body = json.dumps({"error": "insufficient_scope", "error_description": f"{principal.client_id} only reads {principal.source}'s inbox (/inbox/poll, /inbox/events)"}).encode()
                await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json")]})
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
    claim_owner_key = core.cfg.env.get("SWITCHBOARD_KEY_OWNER")
    claim_principal = core.auth.verify_bearer(claim_owner_key)
    claim_key_actor = (claim_principal.client_id if claim_principal is not None and claim_principal.kind == "key"
                       and claim_principal.source == "owner" and claim_principal.is_unrestricted_owner else None)

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({**core.health(public=True), "version": release_version(core.cfg.env)})

    async def owner_page(request: Request) -> tuple[Owner | None, Response | None]:
        owner = await core.auth.owner(request)
        if owner is None:
            return None, login_redirect(request)
        return owner, None

    async def slack_decide(request: Request) -> Response:
        """The owner approves or rejects a Slack write."""
        from .slack.writes import Actor, WriteError

        owner, _, refused = await owner_form(request)
        if refused is not None:
            return refused
        if core.slack is None:
            return pages.message_page("Not available", "Slack approvals need Slack configured.", 403)
        verb = request.path_params["verb"]
        if verb not in ("approve", "reject"):
            return Response(status_code=404)
        try:
            result = await core.slack.gate.decide(request.path_params["rid"], verb == "approve", Actor("admin", owner.label))
        except WriteError:
            return RedirectResponse("/inbox?notice=decide-refused", status_code=303)
        return RedirectResponse("/inbox?notice=send-failed" if result["status"] == "failed" else "/inbox", status_code=303)

    async def avatar(request: Request) -> Response:
        """Public avatar file for clients such as Slack and Discord, including rendered Captain avatars."""
        name = request.path_params["name"]
        private = name.startswith(SLACK_PROFILE_PREFIX)
        if private:
            _, refused = await core.auth.protect_owner(request)
            if refused is not None:
                return refused
        path = core.find_avatar(name)
        if path is None:
            return Response(status_code=404)
        return Response(path.read_bytes(), media_type="image/png" if name.endswith(".png") else ("image/gif" if name.endswith(".gif") else "image/jpeg"),
                        headers={"Cache-Control": "private, max-age=3600" if private else "public, max-age=3600",
                                 "X-Content-Type-Options": "nosniff"})

    async def targets(request: Request) -> JSONResponse:
        _, refused = await core.auth.protect_owner(request)
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if refused is not None:
            refused.headers.update(headers)
            return refused
        return JSONResponse({"targets": await core.targets()}, headers=headers)

    async def avatar_upload(request: Request) -> Response:
        """The owner replaces a persona's picture."""
        owner, form, refused = await owner_form(request)
        if refused is not None:
            return refused
        if core.slack is None:
            await request.close()
            return Response(status_code=403)
        persona = core.slack.config.personas.get(request.path_params["pid"])
        if persona is None or not persona.avatar:
            await request.close()
            return Response(status_code=404)
        upload = form.get("file")
        try:
            data = await upload.read(AVATAR_MAX_BYTES + 1) if hasattr(upload, "read") else b""
        finally:
            await request.close()
        kind = "png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "jpg" if data.startswith(b"\xff\xd8\xff") else "gif" if data[:6] in (b"GIF87a", b"GIF89a") else None
        want = persona.avatar.rsplit(".", 1)[1].lower().replace("jpeg", "jpg")
        if not data or len(data) > AVATAR_MAX_BYTES or kind != want:
            return RedirectResponse("/agents?notice=avatar-refused", status_code=303)
        core.slack.avatar_dir.mkdir(parents=True, exist_ok=True)
        tmp = core.slack.avatar_dir / f".{persona.avatar}.tmp"
        tmp.write_bytes(data)
        tmp.replace(core.slack.avatar_dir / persona.avatar)
        core.slack.store.audit(f"admin:{owner.label}", "avatar_uploaded", persona=persona.id, file=persona.avatar, bytes=len(data))
        return RedirectResponse("/agents?notice=avatar-saved", status_code=303)

    async def owner_form(request: Request) -> tuple[Owner | None, Any, Response | None]:
        owner, refused = await core.auth.protect_owner(request)
        return (None, None, refused) if refused is not None else (owner, await request.form(), None)

    def claim_key_owner(token: str) -> str | None:
        return claim_key_actor if claim_owner_key and hmac.compare_digest(token.encode(), claim_owner_key.encode()) else None

    async def claim_page_access(request: Request) -> tuple[str | None, Response | None]:
        if not core.cfg.owner_sign_in and claim_key_actor:
            return None, pages.claim_page("Save a token", "", core.auth.csrf(f"claim:{request.path_params['code']}"), owner_key=True)
        owner, refused = await owner_page(request)
        return (None, refused) if refused is not None else (core.auth.csrf(owner.csrf_key), None)

    async def claim_form(request: Request) -> tuple[str | None, str, Response | None]:
        actor, form, refused = await claim_submission(request)
        token = form.get("token") if form is not None else None
        return actor, token if isinstance(token, str) else "", refused

    async def claim_submission(request: Request) -> tuple[str | None, Any, Response | None]:
        if not core.auth.origin_ok(request):
            return None, None, JSONResponse({"error": "csrf"}, status_code=403)
        auth = request.headers.get("authorization")
        if auth is not None:
            scheme, _, key = auth.partition(" ")
            actor = claim_key_owner(key.strip()) if scheme.lower() == "bearer" else None
            if actor is None:
                return None, None, pages.claim_message_page("Use the SwB owner key", 403)
            form = await request.form()
        elif not core.cfg.owner_sign_in and claim_key_actor:
            form = await request.form()
            key = form.get("owner_key")
            actor = claim_key_owner(key) if isinstance(key, str) else None
            if actor is None:
                return None, None, pages.claim_message_page("Use the SwB owner key", 403)
            if not await core.auth.valid_csrf(request, f"claim:{request.path_params['code']}"):
                return None, None, JSONResponse({"error": "csrf"}, status_code=403)
        else:
            owner, form, refused = await owner_form(request)
            if refused is not None:
                return None, None, refused
            actor = f"admin:{owner.label}"
        return actor, form, None

    async def group_claim(request: Request, group: dict[str, Any]) -> Response:
        from . import credentials

        code = request.path_params["code"]
        if request.method != "POST":
            owner_key = not core.cfg.owner_sign_in and bool(claim_key_actor)
            if owner_key:
                form_csrf = core.auth.csrf(f"claim:{code}")
            else:
                form_csrf, refused = await claim_page_access(request)
                if refused is not None:
                    return refused
            return credentials.page(group, form_csrf, owner_key=owner_key,
                                    host=request.url.hostname or "", now=core.clock())
        actor, form, refused = await claim_submission(request)
        if refused is not None:
            return refused
        values = {f.name: v for f in group["fields"] if isinstance(v := form.get(f.name), str)}
        try:
            names = await core.claims.consume_group(code, values)
        except ClaimUnavailable:
            return pages.claim_message_page("Request a new link", 410)
        except GroupFieldError as e:
            return pages.claim_message_page(f"Check {e.field.label}", 400)
        core.audit.record(origin="http", source=actor, client=client_ip(request.scope), destination="core", action="token_claim",
                          outcome="ok", target=",".join(names), excerpt=group["title"])
        for block in core.blocks.values():
            if saved := getattr(block, "secret_saved", None):
                for name in names:
                    await saved(name)
        return pages.claim_message_page(f"{len(names)} credential{'s' if len(names) != 1 else ''} saved", 200)

    async def claim_asset(request: Request) -> Response:
        from . import credentials

        return credentials.asset(request.path_params["name"])

    async def token_claim(request: Request) -> Response:
        from .chat import ChatBlock

        code = request.path_params["code"]
        if (group := core.claims.group(code)) is not None:
            return await group_claim(request, group)
        if request.method != "POST":
            form_csrf, refused = await claim_page_access(request)
            if refused is not None:
                return refused
            try:
                claim = core.claims.inspect_details(code)
            except ClaimUnavailable:
                return pages.claim_message_page("Request a new link", 410)
            return pages.token_claim_page(claim["name"], form_csrf, require_https=claim["require_https"],
                                          host=request.url.hostname or "", expires_at=claim["expires_at"], now=core.clock())

        actor, token, refused = await claim_form(request)
        if refused is not None:
            return refused
        try:
            name = core.claims.inspect(code)
            chats = [block for block in core.blocks.values() if isinstance(block, ChatBlock) and block.token_env == name]
            if any(not block.token_re.fullmatch(token.strip()) for block in chats):
                raise ClaimError("That doesn't look like the right kind of token.")
            name = core.claims.consume(code, token)
        except ClaimUnavailable:
            return pages.claim_message_page("Request a new link", 410)
        except ClaimError as e:
            return pages.claim_message_page(TOKEN_CLAIM_MESSAGES.get(str(e), str(e)), 400)
        core.audit.record(origin="http", source=actor, client=client_ip(request.scope), destination="core", action="token_claim",
                          outcome="ok", target=name)
        for block in chats:
            await block.secret_saved("bot_token")
        return pages.claim_message_page("Token saved", 200)

    async def connect_code(request: Request) -> Response:
        """The owner mints a pairing code; the code is shown once."""
        from .connect import ConnectError

        owner, form, refused = await owner_form(request)
        if refused is not None:
            return refused
        project = str(form.get("project") or "").strip().lower() or None
        try:
            if project:
                core.projects.get(project)
            code = core.connections.mint(agent=str(form.get("agent") or ""), project=project, client=None, by=f"admin:{owner.label}")
        except (ConnectError, ValueError):
            return pages.message_page("Pairing code unavailable", "", 400)
        core.audit.record(origin="http", source=f"admin:{owner.label}", client=client_ip(request.scope), destination="core", action="connect_code", outcome="ok", target=code["agent"])
        return pages.connect_code_page(code, core.cfg.public_url or "")

    async def routine_claim(request: Request) -> Response:
        """Use a one-time routine registration link to save a token."""
        from .blocks import BlockError
        from .core import CoreError
        from .projects import ProjectError

        registration = request.path_params.get("registration")
        if registration is not None and not re.fullmatch(r"[a-f0-9]{32}", registration):
            return Response(status_code=404)
        code = request.path_params.get("code", "")
        if registration is not None and request.method == "POST":
            owner, form, refused = await owner_form(request)
            if refused is None:
                value = form.get("token")
                actor, token = f"admin:{owner.label}", value if isinstance(value, str) else ""
        elif registration is not None:
            owner, refused = await owner_page(request)
            if refused is None:
                form_csrf = core.auth.csrf(owner.csrf_key)
        elif request.method == "POST":
            actor, token, refused = await claim_form(request)
        else:
            form_csrf, refused = await claim_page_access(request)
        if refused is not None:
            return refused
        block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
        if block is None:
            return pages.message_page("Claude routine setup required", "", 400)
        if request.method != "POST":
            try:
                return pages.routine_claim_page(block.claim(code, registration=registration), form_csrf,
                                                host=request.url.hostname or "", now=core.clock())
            except BlockError as e:
                return pages.claim_message_page(ROUTINE_CLAIM_MESSAGES.get(str(e), "Request a new link"), 410)
        try:
            out = block.complete(code, token, actor=actor, registration=registration)
        except (BlockError, CoreError, ProjectError) as e:
            return pages.claim_message_page(ROUTINE_CLAIM_MESSAGES.get(str(e), "Request a new link"), 400)
        core.audit.record(origin="http", source=actor, client=client_ip(request.scope), destination=block.name, action="routine_claim", outcome="ok",
                          target=out["name"], excerpt=f"{out['routine_id']} for {out['project']}" + (" (new project)" if out["created_project"] else ""))
        return pages.claim_message_page("Routine and project saved" if out["created_project"] else "Routine saved", 200)

    async def connect_redeem(request: Request) -> JSONResponse:
        """A client redeems a pairing code for its own bearer key (a machine path, outside Access). The key is shown once."""
        from .connect import ConnectError

        ip = client_ip(request.scope)
        wait = limiter.blocked("connect_fail", ip)
        if wait is not None:
            return JSONResponse({"error": "slow_down", "error_description": "too many wrong codes from this address"}, status_code=429)
        try:
            body = await request.json()
            out = core.connections.redeem(str((body or {}).get("code") or ""))
        except (ConnectError, ValueError, TypeError) as e:
            limiter.hit("connect_fail", ip)
            core.audit.record(origin="http", source="-", client=ip, destination="core", action="connect_redeem", outcome="refused", detail=str(e)[:200])
            return JSONResponse({"error": "invalid_grant", "error_description": str(e) if isinstance(e, ConnectError) else "send JSON {\"code\": \"...\"}"}, status_code=400)
        core.audit.record(origin="http", source=out["agent"], client=ip, destination="core", action="connect_redeem", outcome="ok", target=out["key_id"])
        base = core.cfg.public_url or ""
        return JSONResponse({**out, "mcp_url": f"{base}/mcp", "inbox_events_url": f"{base}/inbox/events", "header": "Authorization: Bearer <key>",
                             "note": "Store the key now. Revoke it in Tokens at /tokens."},
                            headers={"Cache-Control": "no-store"})

    async def inbox_poll(request: Request) -> JSONResponse:
        """Long-poll for the Claude Code channel and scripts: the caller's own inbox items after ?after=."""
        p = principal_of(request)
        if p.tools is not None and "inbox" not in p.tools:
            return JSONResponse({"error": "forbidden", "error_description": f"the {p.source} key can't read an inbox"}, status_code=403)
        if request.query_params.get("agent") not in (None, "", p.source):
            return JSONResponse({"error": "forbidden", "error_description": f"you read {p.source}'s inbox only"}, status_code=403)
        try:
            timeout = min(POLL_MAX_S, max(0.0, float(request.query_params.get("timeout", "25"))))
        except ValueError:
            timeout = 25.0
        since = _cursor(request)
        out = await core.inbox.wait(owner=p.source, since=since if since is not None else core.inbox.head(),
                                    timeout=timeout, reader=f"channel:{p.source}", advance=False)
        return JSONResponse(out)

    async def inbox_events(request: Request) -> Response:
        """Server-Sent Events: one 'item' frame per inbox item, id = its cursor, so Last-Event-ID resumes after a drop."""
        p = principal_of(request)
        if p.tools is not None and "inbox" not in p.tools:
            return JSONResponse({"error": "forbidden", "error_description": f"the {p.source} key can't read an inbox"}, status_code=403)
        if request.query_params.get("agent") not in (None, "", p.source):
            return JSONResponse({"error": "forbidden", "error_description": f"you read {p.source}'s inbox only"}, status_code=403)
        agent = p.source
        start = _cursor(request)

        async def frames() -> AsyncIterator[bytes]:
            cursor = start if start is not None else core.inbox.head()
            yield f"retry: 3000\nevent: ready\ndata: {json.dumps({'cursor': cursor, 'agent': agent})}\n\n".encode()
            while True:
                got = await core.inbox.wait(owner=agent, since=cursor, timeout=HEARTBEAT_S, reader=f"channel:{p.source}", advance=False)
                for item in got["items"]:
                    if item["kind"] == "watch_digest":
                        continue
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

    limiter = RateLimiter()
    login = OwnerLogin(core.auth, limiter)
    origin_routes = sorted((r for o in core.origins.values() for r in o.routes()), key=lambda r: isinstance(r, Mount) and not r.path)
    app: Any = Starlette(
        routes=[
            Route("/slack/requests/{rid}/{verb}", slack_decide, methods=["POST"]),
            Route("/avatars/{name}", avatar, methods=["GET"]),
            Route("/slack/personas/{pid}/avatar", avatar_upload, methods=["POST"]),
            Route("/healthz", healthz),
            Route("/api/targets", targets),
            Route("/inbox/poll", inbox_poll, methods=["GET"]),
            Route("/inbox/events", inbox_events, methods=["GET"]),
            Route("/connect", connect_redeem, methods=["POST"]),
            pages.PageRoute("/pairing/code", connect_code, pages.PAIRING_TITLE, methods=["POST"]),
            pages.PageRoute("/routines/claim/{code}", routine_claim, pages.ROUTINE_CLAIM_TITLE, methods=["GET", "POST"]),
            pages.PageRoute("/routines/claim/owner/{registration}", routine_claim, pages.ROUTINE_CLAIM_TITLE, methods=["GET", "POST"]),
            pages.PageRoute(CLAIM_PATH + "/{code}", token_claim, pages.TOKEN_CLAIM_TITLE, methods=["GET", "POST"]),
            Route("/claim-assets/{name}", claim_asset, methods=["GET"]),
            *pages.routes(),
            *login.routes(),
            *core.auth.routes(),
            *web_routes(),
            *origin_routes,
        ],
        lifespan=lifespan,
    )
    app.state.core = core
    for origin in core.origins.values():
        app = origin.wrap(app)

    def refused(ip: str, path: str, why: str) -> None:
        core.audit.record(origin="http", source="-", client=ip, destination="core", action="rate_limited", outcome="refused", target=path, detail=why)

    gate = Guard(RequestBase(BearerGate(app, core, limiter), core), limiter, on_refusal=refused)
    gate.starlette = app
    return gate
