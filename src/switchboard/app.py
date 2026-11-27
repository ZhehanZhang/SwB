"""The one Starlette app: core pages and endpoints, OAuth, pairing codes, and whatever routes each block serves."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Mount, Route

from . import pages
from .claims import CLAIM_PATH, ClaimError, ClaimUnavailable
from .guard import Guard, RateLimiter, client_ip, reject
from .blocks.mcp import PRINCIPAL_SCOPE, principal_of

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
            if principal.kind == "inbox_key" and not scope["path"].startswith("/inbox/"):
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
        return JSONResponse(core.health())

    async def owner_page(request: Request) -> tuple[str | None, Response | None]:
        if not core.cfg.access_team_domain:
            return None, pages.message_page("Not available", "This needs Cloudflare Access in front of switchboard (auth.cloudflare_access).", 403)
        email = await core.auth.owner(request)
        if email is None:
            return None, pages.message_page("Not allowed", "This page needs the owner's sign-in through Cloudflare Access.", 403)
        return email, None

    async def admin(request: Request) -> Response:
        """The owner's page: status, routes, projects and recent refusals."""
        email, refused = await owner_page(request)
        if refused is not None:
            return refused
        changes = [c for c in core.audit.recent(limit=100) if c["action"] in ("route_set", "route_remove", "auth_failure", "rate_limited")][:15]
        extra = ""
        if core.slack is not None:
            notices = {"approved": "Approved and sent.", "rejected": "Rejected.", "send-failed": "Approved, but Slack refused the send; see the audit log.",
                       "decide-refused": "That request was already decided or has expired.",
                       "avatar-saved": "Avatar saved. New posts use it; Slack may take a minute to show it.",
                       "avatar-refused": "Avatar not saved: send a picture of the persona's file type (see its file name), 2 MB at most."}
            extra = pages.slack_body(core.slack.status(), core.slack.store.list_requests(status="pending", limit=50), csrf(email),
                                     notices.get(request.query_params.get("notice", "")), core.slack.persona_rows())
        rows = [row for b in core.blocks.values() for row in b.page_rows()]
        routine_block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
        return pages.admin_page(core.health(), email, [r.as_dict() for r in core.routes.list()], [p.as_dict() for p in core.projects.list(include_archived=True)], changes, extra,
                                block_rows=rows, connected=core.connections.list(), csrf=csrf(email),
                                routines=routine_block.rows() if routine_block else None, systems=core.project_systems(await core.targets()),
                                visibility=core.visibility.rows([f"target:{t['slugs'][0]}" for t in await core.targets() if t.get("slugs") and t.get("block") != "agent"][:200]))

    def csrf(email: str) -> str:
        return hmac.new(core.auth.secret, f"switchboard-csrf:{email}".encode(), hashlib.sha256).hexdigest()

    def valid_csrf(form: Any, expected: str) -> bool:
        token = form.get("csrf")
        return isinstance(token, str) and hmac.compare_digest(token.encode(), expected.encode())

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
        """Public avatar file for clients such as Slack and Discord, including rendered Captain avatars."""
        name = request.path_params["name"]
        path = core.find_avatar(name)
        if path is None:
            return Response(status_code=404)
        return Response(path.read_bytes(), media_type="image/png" if name.endswith(".png") else ("image/gif" if name.endswith(".gif") else "image/jpeg"),
                        headers={"Cache-Control": "public, max-age=3600"})

    async def targets(request: Request) -> JSONResponse:
        _, refused = await owner_page(request)
        if refused is not None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse({"targets": await core.targets()}, headers={"Cache-Control": "no-store"})

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

    async def owner_form(request: Request) -> tuple[str | None, Any, Response | None]:
        email, refused = await owner_page(request)
        if refused is not None:
            return None, None, refused
        form = await request.form()
        if not valid_csrf(form, csrf(email)):
            return None, None, pages.message_page("Not allowed", "The form expired; reload the page and try again.", 403)
        return email, form, None

    def claim_key_owner(token: str) -> str | None:
        return claim_key_actor if claim_owner_key and hmac.compare_digest(token.encode(), claim_owner_key.encode()) else None

    async def claim_page_access(request: Request) -> tuple[str | None, Response | None]:
        if not core.cfg.access_team_domain and claim_key_actor:
            return None, pages.claim_page("Save a token", "", csrf(f"claim:{request.path_params['code']}"), owner_key=True)
        email, refused = await owner_page(request)
        return (None, refused) if refused is not None else (csrf(email), None)

    async def claim_form(request: Request) -> tuple[str | None, str, Response | None]:
        actor, form, refused = await claim_submission(request)
        token = form.get("token") if form is not None else None
        return actor, token if isinstance(token, str) else "", refused

    async def claim_submission(request: Request) -> tuple[str | None, Any, Response | None]:
        """The owner check for a claim form post (owner key header, owner key field, or Access session plus CSRF) and its form."""
        auth = request.headers.get("authorization")
        if auth is not None:
            scheme, _, key = auth.partition(" ")
            actor = claim_key_owner(key.strip()) if scheme.lower() == "bearer" else None
            if actor is None:
                return None, None, pages.message_page("Not allowed", "This needs the Switchboard owner key.", 403)
            form = await request.form()
        elif not core.cfg.access_team_domain and claim_key_actor:
            form = await request.form()
            key = form.get("owner_key")
            actor = claim_key_owner(key) if isinstance(key, str) else None
            if actor is None or not valid_csrf(form, csrf(f"claim:{request.path_params['code']}")):
                return None, None, pages.message_page("Not allowed", "Use the Switchboard owner key and reload the form if needed.", 403)
        else:
            email, form, refused = await owner_form(request)
            if refused is not None:
                return None, None, refused
            actor = f"admin:{email}"
        return actor, form, None

    async def group_claim(request: Request, group: dict[str, Any]) -> Response:
        from . import credentials

        code = request.path_params["code"]
        if request.method != "POST":
            form_csrf, refused = await claim_page_access(request)
            if refused is not None:
                return refused
            return credentials.page(group, form_csrf, owner_key=not core.cfg.access_team_domain and bool(claim_key_actor),
                                    host=urlsplit(core.base_url or "").hostname or "", now=core.store.clock())
        actor, form, refused = await claim_submission(request)
        if refused is not None:
            return refused
        values = {f.name: v for f in group["fields"] if isinstance(v := form.get(f.name), str)}
        try:
            names = core.claims.consume_group(code, values)
        except ClaimUnavailable as e:
            return pages.message_page("Link not usable", str(e), 410)
        except ClaimError as e:
            return pages.message_page("Not saved", f"{e} Nothing was saved; go back and try again.", 400)
        core.audit.record(origin="http", source=actor, client=client_ip(request.scope), destination="core", action="token_claim",
                          outcome="ok", target=",".join(names), excerpt=group["title"])
        for block in core.blocks.values():
            if saved := getattr(block, "secret_saved", None):
                for name in names:
                    await saved(name)
        return pages.message_page("Saved", f"{group['title']}: {len(names)} credential{'s' if len(names) != 1 else ''} saved. "
                                  "Run connect again to see what's next.", 200)

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
                name = core.claims.inspect(code)
            except ClaimUnavailable as e:
                return pages.message_page("Link not usable", str(e), 410)
            return pages.token_claim_page(name, form_csrf)

        actor, token, refused = await claim_form(request)
        if refused is not None:
            return refused
        try:
            name = core.claims.inspect(code)
            chats = [block for block in core.blocks.values() if isinstance(block, ChatBlock) and block.token_env == name]
            if any(not block.token_re.fullmatch(token.strip()) for block in chats):
                raise ClaimError("That doesn't look like the right kind of token.")
            name = core.claims.consume(code, token)
        except ClaimUnavailable as e:
            return pages.message_page("Link not usable", str(e), 410)
        except ClaimError as e:
            return pages.message_page("Not saved", str(e), 400)
        core.audit.record(origin="http", source=actor, client=client_ip(request.scope), destination="core", action="token_claim",
                          outcome="ok", target=name)
        for block in chats:
            await block.secret_saved("bot_token")
        return pages.message_page("Token saved", "Run connect again to see whether Switchboard needs a restart.", 200)

    async def connect_code(request: Request) -> Response:
        """The owner mints a pairing code from the root page (Access JWT plus CSRF); the code is shown once."""
        from .connect import ConnectError

        email, form, refused = await owner_form(request)
        if refused is not None:
            return refused
        project = str(form.get("project") or "").strip().lower() or None
        try:
            if project:
                core.projects.get(project)
            code = core.connections.mint(agent=str(form.get("agent") or ""), project=project, client=None, by=f"admin:{email}")
        except (ConnectError, ValueError) as e:
            return pages.message_page("Not minted", str(e), 400)
        core.audit.record(origin="http", source=f"admin:{email}", client=client_ip(request.scope), destination="core", action="connect_code", outcome="ok", target=code["agent"])
        return pages.connect_code_page(code, core.cfg.public_url or "")

    async def visibility_set(request: Request) -> Response:
        from .visibility import VisibilityError

        email, form, refused = await owner_form(request)
        if refused is not None:
            return refused
        resource = str(form.get("resource") or "")
        try:
            rule = core.visibility.set(resource, str(form.get("visibility") or ""), f"admin:{email}", core.clock())
        except VisibilityError as e:
            return pages.message_page("Not changed", str(e), 400)
        core.audit.record(origin="http", source=f"admin:{email}", client=client_ip(request.scope), destination="core", action="visibility_set", outcome="ok",
                          target=resource, detail=str(rule))
        return RedirectResponse("/", status_code=303)

    async def routine_add(request: Request) -> Response:
        from .blocks import BlockError
        from .core import CoreError

        email, form, refused = await owner_form(request)
        if refused is not None:
            return refused
        block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
        if block is None:
            return pages.message_page("Not available", "The claude-routine block isn't configured (blocks.claude-routine).", 400)
        try:
            out = block.add(fire=str(form.get("fire_url") or ""), token=str(form.get("token") or ""), project=str(form.get("project") or "") or None,
                            name=str(form.get("name") or "") or None, agent="claude", actor=f"admin:{email}")
        except (BlockError, CoreError) as e:
            return pages.message_page("Not added", str(e), 400)
        core.audit.record(origin="http", source=f"admin:{email}", client=client_ip(request.scope), destination=block.name, action="routine_add", outcome="ok",
                          target=out["name"], excerpt=f"{out['routine_id']} for {out['project'] or 'general'}")
        return RedirectResponse("/", status_code=303)

    async def routine_claim(request: Request) -> Response:
        """Use a one-time routine registration link to save a token."""
        from .blocks import BlockError
        from .core import CoreError
        from .projects import ProjectError

        code = request.path_params["code"]
        if request.method == "POST":
            actor, token, refused = await claim_form(request)
        else:
            form_csrf, refused = await claim_page_access(request)
        if refused is not None:
            return refused
        block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
        if block is None:
            return pages.message_page("Not available", "The claude-routine block isn't configured (blocks.claude-routine).", 400)
        if request.method != "POST":
            try:
                return pages.routine_claim_page(block.claim(code), form_csrf)
            except BlockError as e:
                return pages.message_page("Link not usable", str(e), 410)
        try:
            out = block.complete(code, token, actor=actor)
        except (BlockError, CoreError, ProjectError) as e:
            return pages.message_page("Not saved", str(e), 400)
        core.audit.record(origin="http", source=actor, client=client_ip(request.scope), destination=block.name, action="routine_claim", outcome="ok",
                          target=out["name"], excerpt=f"{out['routine_id']} for {out['project']}" + (" (new project)" if out["created_project"] else ""))
        return pages.message_page("Routine saved", f"{out['name']} ({out['routine_id']}) now answers for switchboard project {out['project']}"
                                  + (", which was just created" if out["created_project"] else "") + ". You can close this tab; the Claude session "
                                  "sees it in routine_list.", 200)

    async def routine_remove(request: Request) -> Response:
        from .blocks import BlockError

        email, _, refused = await owner_form(request)
        if refused is not None:
            return refused
        block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
        name = request.path_params["name"]
        try:
            ok = block.remove(name) if block else False
        except BlockError as e:
            return pages.message_page("Not removed", str(e), 400)
        core.audit.record(origin="http", source=f"admin:{email}", client=client_ip(request.scope), destination="claude-routine", action="routine_remove",
                          outcome="ok" if ok else "refused", target=name)
        return RedirectResponse("/", status_code=303)

    async def connect_revoke(request: Request) -> Response:
        email, _, refused = await owner_form(request)
        if refused is not None:
            return refused
        n = core.connections.revoke(key_id=request.path_params["kid"])
        core.audit.record(origin="http", source=f"admin:{email}", client=client_ip(request.scope), destination="core", action="connect_revoke", outcome="ok" if n else "refused",
                          target=request.path_params["kid"])
        return RedirectResponse("/", status_code=303)

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
                             "note": "Store the key now; switchboard keeps only its hash. Revoke it from the root page."},
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
    origin_routes = sorted((r for o in core.origins.values() for r in o.routes()), key=lambda r: isinstance(r, Mount) and not r.path)
    app: Any = Starlette(
        routes=[
            Route("/", admin, methods=["GET"]),
            Route("/slack/requests/{rid}/{verb}", slack_decide, methods=["POST"]),
            Route("/avatars/{name}", avatar, methods=["GET"]),
            Route("/slack/personas/{pid}/avatar", avatar_upload, methods=["POST"]),
            Route("/healthz", healthz),
            Route("/api/targets", targets),
            Route("/inbox/poll", inbox_poll, methods=["GET"]),
            Route("/inbox/events", inbox_events, methods=["GET"]),
            Route("/connect", connect_redeem, methods=["POST"]),
            Route("/pairing/code", connect_code, methods=["POST"]),
            Route("/pairing/keys/{kid}/revoke", connect_revoke, methods=["POST"]),
            Route("/visibility", visibility_set, methods=["POST"]),
            Route("/routines", routine_add, methods=["POST"]),
            Route("/routines/{name}/remove", routine_remove, methods=["POST"]),
            Route("/routines/claim/{code}", routine_claim, methods=["GET", "POST"]),
            Route(CLAIM_PATH + "/{code}", token_claim, methods=["GET", "POST"]),
            Route("/claim-assets/{name}", claim_asset, methods=["GET"]),
            *pages.routes(),
            *core.auth.routes(),
            *origin_routes,
        ],
        lifespan=lifespan,
    )
    for origin in core.origins.values():
        app = origin.wrap(app)

    def refused(ip: str, path: str, why: str) -> None:
        core.audit.record(origin="http", source="-", client=ip, destination="core", action="rate_limited", outcome="refused", target=path, detail=why)

    gate = Guard(BearerGate(app, core, limiter), limiter, on_refusal=refused)
    gate.starlette = app
    return gate
