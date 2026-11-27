"""The one Starlette app: core pages and endpoints, OAuth, pairing codes, and whatever routes each block serves."""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Mount, Route

from . import pages
from .api_about import release_version
from .api_display import SLACK_PROFILE_PREFIX
from .auth import COMPANION_KEY_PREFIX
from .claims import CLAIM_PATH, ClaimError, ClaimInputError, ClaimUnavailable, GroupFieldError
from .companion_routes import COMPANION_ACTIONS, companion_routes
from .config import REQUEST_BASE
from .deployment import OWNER_KEY
from .env import env_value
from .guard import Guard, RateLimiter, audit_client_ip, client_ip, reject
from .owner_login import OwnerLogin
from .blocks.mcp import PRINCIPAL_SCOPE, principal_of
from .sessions import Owner, login_redirect
from .web import web_routes

if TYPE_CHECKING:
    from .core import Switchboard

INBOX_FEEDS = ("/inbox/poll", "/inbox/events")
HEARTBEAT_S = 15.0
POLL_MAX_S = 55.0
AVATAR_MAX_BYTES = 2 * 1024 * 1024
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
    """Authenticates MCP, inbox and companion requests with distinct bearer credentials.

    Wrong tokens on protected endpoints count toward per-address lockout. Anonymous MCP and inbox requests
    can start OAuth discovery without counting as failed sign-ins.
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

    def _companion_refused(self, scope: dict, endpoint: str | None, detail: str, token: str | None = None) -> None:
        companion_id = self.core.companions.key_id(token) if token else None
        self.core.audit.record_refusal(origin="http", client=audit_client_ip(scope), destination="core",
                                       action="companion_refused", detail=detail, target=companion_id or endpoint,
                                       ref=endpoint if companion_id else None)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        inbox_feed = path.rstrip("/") in INBOX_FEEDS
        mcp = path.startswith("/mcp")
        companion_api = path.startswith("/companion/")
        companion = companion_api and path.rstrip("/") != "/companion/pair"
        headers = Headers(scope=scope)
        auth = headers.get("authorization", "")
        bare = auth.strip()
        bearer = auth[:7].lower() == "bearer "
        token = auth[7:].strip() if bearer else (bare if bare and " " not in bare else None)
        wrong_realm = not companion_api and token is not None and token.startswith(COMPANION_KEY_PREFIX)
        mcp_or_inbox = mcp or inbox_feed
        if wrong_realm and not mcp_or_inbox:
            target = CLAIM_PATH if path.startswith(CLAIM_PATH + "/") else None
            self._companion_refused(scope, target, "companion key outside the companion API", token)
            response = JSONResponse({"error": "invalid_token", "error_description": "Use a credential for this endpoint."},
                                    status_code=401, headers={"WWW-Authenticate": 'Bearer error="invalid_token"', "Cache-Control": "no-store"})
            return await response(scope, receive, send)

        if mcp_or_inbox or companion:
            scope.pop(PRINCIPAL_SCOPE, None)
            ip = client_ip(scope)
            if companion:
                wait = self.limiter.blocked("companion_bearer_fail", ip)
                if wait is not None:
                    self._companion_refused(scope, "/companion", "companion sign-in rate limit", token)
                    await reject(send, 429, "slow_down", "Too many failed companion sign-ins.", wait)
                    return
                principal = self.core.companions.authenticate(token or "") if bearer else None
                if principal is None:
                    self.limiter.hit("companion_bearer_fail", ip)
                    detail = "unknown or revoked companion key" if token else "missing companion key"
                    self._companion_refused(scope, "/companion", detail, token)
                    self.core.audit.record_refusal(origin="http", client=audit_client_ip(scope), destination="core", action="auth_failure",
                                                   detail=detail, target="/companion")
                    await reject(send, 401, "invalid_token", "Use a valid companion key.")
                    return
                scope[PRINCIPAL_SCOPE] = principal
                return await self.app(scope, receive, send)
            wait = self.limiter.blocked("bearer_fail", ip)
            if wait is not None:
                if wrong_realm:
                    self._companion_refused(scope, "/inbox" if inbox_feed else "/mcp", "sign-in rate limit", token)
                await reject(send, 429, "slow_down", "too many failed sign-ins from this address; try again later", wait)
                return
            principal = self.core.auth.verify_bearer(token)
            if principal is None and token:
                self.limiter.hit("bearer_fail", ip)
                if wrong_realm:
                    endpoint = "/inbox" if inbox_feed else "/mcp"
                    detail = "companion key outside the companion API"
                    self._companion_refused(scope, endpoint, detail, token)
                    self.core.audit.record_refusal(origin="http", client=audit_client_ip(scope), destination="core",
                                                   action="auth_failure", target=endpoint, detail=detail)
                else:
                    self.core.audit.record(origin="http", source="-", client=ip, destination="core", action="auth_failure", outcome="refused",
                                           target=path, detail=f"unknown or expired bearer token ({token_shape(token)}); agent {headers.get('user-agent', '-')[:80]}")
            elif auth and not token:
                self.core.audit.record(origin="http", source="-", client=ip, destination="core", action="auth_failure", outcome="refused",
                                       target=path, detail="Authorization header is not 'Bearer <token>'")
            elif not auth and self._first_anonymous(ip):
                self.core.audit.record(origin="http", source="-", client=ip, destination="core", action="auth_missing", outcome="refused",
                                       target=path, detail=f"no Authorization header (OAuth discovery, or a key that wasn't sent); agent {headers.get('user-agent', '-')[:80]}")
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


def create_app(core: "Switchboard", *, on_started: Callable[[], None] | None = None) -> Any:
    claim_owner_key = env_value(core.cfg.env, OWNER_KEY)
    claim_principal = core.auth.verify_bearer(claim_owner_key)
    claim_key_actor = (claim_principal.client_id if claim_principal is not None and claim_principal.kind == "key"
                       and claim_principal.source == "owner" and claim_principal.is_unrestricted_owner else None)

    async def healthz(request: Request) -> JSONResponse:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        owner_key = scheme.lower() == "bearer" and claim_key_owner(token.strip()) is not None
        owner = owner_key or await core.auth.owner(request) is not None
        if owner:
            await core.modes.refresh()
        health = core.health(public=not owner)
        return JSONResponse({**(health if owner else {"status": health["status"]}), "version": release_version(core.cfg.env)},
                            headers={"Cache-Control": "no-store"})

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

    def claim_error(request: Request, status: int, error: str, message: str) -> Response:
        if request.headers.get("accept") == "application/json":
            return JSONResponse({"error": error, "message": message}, status_code=status,
                                headers={"Cache-Control": "no-store"})
        return pages.claim_message_page(message, status)

    async def claim_submission(request: Request, *, unavailable: str = "", browser_only: bool = False) -> tuple[str | None, Any, str, Response | None]:
        if not core.auth.origin_ok(request):
            return None, None, "", core.auth.csrf_refused(request, target=CLAIM_PATH)
        csrf_key = None
        auth = request.headers.get("authorization")
        if auth is not None and not browser_only:
            scheme, _, key = auth.partition(" ")
            actor = claim_key_owner(key.strip()) if scheme.lower() == "bearer" else None
        elif not browser_only and not core.cfg.owner_sign_in and claim_key_actor:
            form = await request.form()
            key = form.get("owner_key")
            actor = claim_key_owner(key) if isinstance(key, str) else None
            csrf_key = f"claim:{request.path_params['code']}"
        else:
            owner = await core.auth.owner(request)
            if owner is None:
                return None, None, "", JSONResponse({"error": "signed_out"}, status_code=401)
            actor = f"admin:{owner.label}"
            csrf_key = owner.csrf_key
        if actor is None:
            return None, None, "", claim_error(request, 403, "claim_owner_key", "Use the SwB owner key")
        if (csrf_key is not None and (not unavailable or not core.cfg.owner_sign_in)
                and not await core.auth.valid_csrf(request, csrf_key)):
            return None, None, "", core.auth.csrf_refused(request, target=CLAIM_PATH)
        if unavailable:
            return None, None, "", claim_error(request, 410, "claim_unavailable", unavailable)
        return actor, await request.form(), core.auth.csrf(csrf_key) if csrf_key is not None else "", None

    async def group_claim(request: Request, code: str, group: dict[str, Any], actor: str, form: Any,
                          csrf: str, owner_key: bool) -> Response:
        from . import credentials

        values = {f.name: v for f in group["fields"] if isinstance(v := form.get(f.name), str)}
        try:
            names = await core.claims.consume_group(code, values)
        except ClaimUnavailable:
            return claim_error(request, 410, "claim_unavailable", "Request a new link")
        except GroupFieldError as e:
            if request.headers.get("accept") == "application/json":
                message = f"{e}. {e.guidance}" if e.guidance else str(e)
                return claim_error(request, 400, "claim_input", message)
            return credentials.page(group, csrf, owner_key=owner_key, host=request.url.hostname or "",
                                    now=core.clock(), error=e)
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
        from . import credentials
        from .chat import ChatBlock

        code = request.path_params["code"]
        owner_key = not core.cfg.owner_sign_in and bool(claim_key_actor)
        group = core.claims.group(code)
        claim = None
        if group is None:
            try:
                claim = core.claims.inspect_details(code)
            except ClaimUnavailable:
                pass

        def show_claim(csrf: str, error: str = "") -> Response:
            return pages.token_claim_page(claim["name"], csrf, require_https=claim["require_https"], kind=claim["kind"],
                                          label=claim["label"], owner_key=owner_key,
                                          host=request.url.hostname or "", expires_at=claim["expires_at"],
                                          now=core.clock(), error=error)

        if request.method != "POST":
            form_csrf, refused = await claim_page_access(request)
            if refused is not None and not owner_key:
                return refused
            if owner_key:
                if group is None and (claim is None or not claim["label"]):
                    return refused
                form_csrf = core.auth.csrf(f"claim:{code}")
            if group is not None:
                return credentials.page(group, form_csrf, owner_key=owner_key,
                                        host=request.url.hostname or "", now=core.clock())
            return show_claim(form_csrf) if claim is not None else pages.claim_message_page("Request a new link", 410)

        unavailable = "Request a new link" if group is None and claim is None else ""
        actor, form, form_csrf, refused = await claim_submission(request, unavailable=unavailable)
        if refused is not None:
            return refused
        if group is not None:
            return await group_claim(request, code, group, actor, form, form_csrf, owner_key)
        value = form.get("token")
        token = value if isinstance(value, str) else ""
        try:
            name = claim["name"]
            chats = [block for block in core.blocks.values() if isinstance(block, ChatBlock) and block.token_env == name]
            if any(not block.token_re.fullmatch(token.strip()) for block in chats):
                raise ClaimInputError("Use a bot token")
            name = await core.claims.submit(code, token)
        except ClaimUnavailable:
            return claim_error(request, 410, "claim_unavailable", "Request a new link")
        except ClaimError as e:
            message = str(e) if isinstance(e, ClaimInputError) else "Check the value"
            if request.headers.get("accept") == "application/json":
                return claim_error(request, 400, "claim_input", message)
            return show_claim(form_csrf, message)
        core.audit.record(origin="http", source=actor, client=client_ip(request.scope), destination="core", action="token_claim",
                          outcome="ok", target=name)
        for block in chats:
            await block.secret_saved("bot_token")
        label = claim["label"] or ("Webhook URL" if claim["kind"] == "url" or claim["require_https"] else "Token")
        return pages.claim_message_page(f"{label} saved", 200)

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
        block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
        claim, unavailable = None, ""
        if block is not None:
            try:
                claim = block.claim(code, registration=registration)
            except BlockError as e:
                unavailable = ROUTINE_CLAIM_MESSAGES.get(str(e), "Request a new link")
        if request.method == "POST":
            actor, form, form_csrf, refused = await claim_submission(request, unavailable=unavailable, browser_only=registration is not None)
        elif registration is not None:
            owner, refused = await owner_page(request)
            if refused is None:
                form_csrf = core.auth.csrf(owner.csrf_key)
        else:
            form_csrf, refused = await claim_page_access(request)
        if refused is not None:
            return refused
        if block is None:
            return pages.message_page("Claude routine setup required", "", 400)
        if unavailable:
            return claim_error(request, 410, "claim_unavailable", unavailable)
        if request.method != "POST":
            return pages.routine_claim_page(claim, form_csrf, host=request.url.hostname or "", now=core.clock())
        try:
            value = form.get("token")
            token = value if isinstance(value, str) else ""
            out = block.complete(code, token, actor=actor, registration=registration)
        except (BlockError, CoreError, ProjectError) as e:
            message = ROUTINE_CLAIM_MESSAGES.get(str(e))
            if message == "Use a Claude routine token":
                if request.headers.get("accept") == "application/json":
                    return claim_error(request, 400, "claim_input", message)
                return pages.routine_claim_page(claim, form_csrf, host=request.url.hostname or "", now=core.clock(), error=message)
            if message is not None:
                return claim_error(request, 410, "claim_unavailable", message)
            return claim_error(request, 400, "claim_input", "Request a new link")
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
                                    timeout=timeout, reader=f"channel:{p.source}", advance=False,
                                    scopes=None if p.kind == "inbox_key" else p.scopes)
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
                got = await core.inbox.wait(owner=agent, since=cursor, timeout=HEARTBEAT_S, reader=f"channel:{p.source}",
                                            advance=False, scopes=None if p.kind == "inbox_key" else p.scopes)
                for item in got["items"]:
                    if item["kind"] in ("watch_digest", "context"):
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
                if on_started is not None:
                    on_started()
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
            *companion_routes(core, limiter, HEARTBEAT_S),
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

    def large_body(scope: dict) -> None:
        head, _, verb = scope["path"].rstrip("/").rpartition("/")
        if head != "/companion":
            return
        detail = "Companion request exceeds the body limit."
        if verb == "pair":
            core.audit.record_refusal(origin="http", client=audit_client_ip(scope), destination="core", action="companion_pair", detail=detail)
        elif action := COMPANION_ACTIONS.get(verb):
            scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
            if scheme.lower() == "bearer" and (companion_id := core.companions.key_id(token.strip())):
                core.audit.record_refusal(origin="companion", client=audit_client_ip(scope), destination="core",
                                          action=f"companion_{action}", target=companion_id, detail=detail)

    gate = Guard(RequestBase(BearerGate(app, core, limiter), core), limiter, on_refusal=refused,
                 on_large_body=large_body, trusted_proxies=core.cfg.trusted_proxies)
    gate.starlette = app
    return gate
