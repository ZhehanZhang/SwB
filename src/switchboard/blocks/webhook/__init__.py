"""Custom webhooks: any service that can POST a webhook reaches the owner's agents, and agents can POST out.

Inbound. The owner creates a named hook with webhook_create. SwB gives it an unguessable URL (/hooks/wh_<32 hex>) and a
claim link where the owner saves the secret: the provider's signing secret for a preset (Stripe, Linear, Sentry,
Standard Webhooks and Svix), or a secret he chose for HMAC, a bearer token or a secret in the path. Every request is
rate limited by hook and by client address (as trusted_proxies resolves it), capped in size, verified on the raw body
and checked for replay before anything parses it. Each refusal returns 4xx with an audit row. An accepted event passes
the same two visibility gates as a GitHub event: the hook's channel, then the target. A target that admits the hook gets
a wake framed as external content. A target that refuses it gets a passive context item, never a work item (E-41, E-52).

Outbound. A hook created with direction out is a send target, webhook:out:<name>. A send to it POSTs a JSON message to
the owner's HTTPS URL, signed with the Standard Webhooks scheme, so any receiver can check it with a stock library.

SwB never shows a secret. Secrets go in through claim links and stay sealed in the store.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

import httpx
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...auth import Principal
from ...claims import Field, valid_webhook_url
from ...config import ConfigError
from ...guard import RateLimiter, client_ip
from ...inbox import CONTEXT_NOTE
from ...projects import ProjectError
from ...refusals import OWNER_REQUIRED
from ...store import dumps
from ...subject import subject, wake_text
from ...trust import AuthoredText, FramedText, frame_parts, join_parts, neutralize
from ...visibility import Who
from .. import Action, Address, Block, BlockError, BlockRefused, Call, Manifest, TargetKind
from . import verify as v

if TYPE_CHECKING:
    from ...core import Switchboard

SCHEMA = """
CREATE TABLE IF NOT EXISTS webhooks (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  direction TEXT NOT NULL,
  verify TEXT NOT NULL,
  settings TEXT NOT NULL DEFAULT '{}',
  target TEXT,
  project TEXT,
  created_at REAL NOT NULL,
  created_by TEXT NOT NULL,
  rotated_at REAL,
  previous_until REAL,
  revoked_at REAL,
  events INTEGER NOT NULL DEFAULT 0,
  refused INTEGER NOT NULL DEFAULT 0,
  last_event_at REAL,
  last_refusal TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS webhooks_active_name ON webhooks(name) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS webhook_seen (hook TEXT NOT NULL, key TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (hook, key));
"""

HOOK_PATH = "/hooks"
HOOK_ID_RE = re.compile(r"wh_[0-9a-f]{32}")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
FIELD_RE = re.compile(r"[A-Za-z0-9_-]{1,64}(?:\.[A-Za-z0-9_-]{1,64}){0,7}")
EVENT_RE = re.compile(r"[A-Za-z0-9_.:/-]{1,100}")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_.-]{1,120})\}")
DIRECTIONS = ("in", "out")
MAX_FIELDS = 20
TEMPLATE_LIMIT = 300
VALUE_LIMIT = 500
PAYLOAD_LIMIT = 4000
FORM_FIELDS = 200
DEFAULT_BODY_KB = 256
MAX_BODY_KB = 1024
SEEN_TTL_S = 86_400
MAX_GRACE_S = 7 * 86_400
RETRY_DELAYS = (0.0, 0.5, 1.0, 2.0)
LIMITS = {
    "webhook_ip": (60, 60.0),
    "webhook_hook": (600, 600.0),
    "webhook_fail": (20, 600.0),
}
PRESET_EVENT = {"stripe": ("type",), "standard": ("type",)}
GENERIC_EVENT = ("type", "event", "event_type", "action")
log = logging.getLogger("switchboard.webhook")
SECRET_PATH_RE = re.compile(r"(/hooks/wh_[0-9a-f]{32})/[^?\s]*")


class _RedactHookSecrets(logging.Filter):
    """Keep path secrets out of the HTTP access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and any(isinstance(a, str) and "/hooks/" in a for a in record.args):
            record.args = tuple(SECRET_PATH_RE.sub(r"\1/<secret>", a) if isinstance(a, str) else a for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactHookSecrets())


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8", "replace")


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _pick(data: Any, path: str) -> Any:
    for key in path.split("."):
        if isinstance(data, dict):
            data = data.get(key)
        elif isinstance(data, list) and key.isdigit() and int(key) < len(data):
            data = data[int(key)]
        else:
            return None
    return data


def _value(value: Any, limit: int = VALUE_LIMIT) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


def _text(value: Any) -> str:
    value = _value(value, 200)
    return "" if value is None else value if isinstance(value, str) else json.dumps(value)


def _secret_names(hook_id: str) -> tuple[str, str, str]:
    base = "WEBHOOK_" + hook_id.removeprefix("wh_").upper()
    return f"{base}_SECRET", f"{base}_PREVIOUS", f"{base}_URL"


class WebhookBlock(Block):
    manifest = Manifest(id="webhook", title="Webhooks", kind="both", level_in="webhook", level_out="L1",
                        speaks=("event", "message"), verbs={"send": "send"}, targets=(TargetKind("out"),),
                        tools=tuple(f"webhook_{name}" for name in ("create", "list", "rotate", "revoke", "test")))

    def __init__(self, core: Switchboard, config: dict[str, Any]):
        super().__init__(core, config)
        size = config.get("max_body_kb", DEFAULT_BODY_KB)
        if type(size) is not int or not 1 <= size <= MAX_BODY_KB:
            raise ConfigError(f"{self.name}.max_body_kb must be a whole number from 1 to {MAX_BODY_KB}")
        self.max_body = size * 1024
        self.limiter = RateLimiter(clock=core.clock, limits=LIMITS)
        self.http: httpx.AsyncClient | None = None
        for statement in filter(str.strip, SCHEMA.split(";")):
            core.store.x(statement)

    # Storage ------------------------------------------------------------------------------------

    def _active(self, name: str, direction: str | None = None) -> dict[str, Any]:
        row = self.core.store.one("SELECT * FROM webhooks WHERE name = ? AND revoked_at IS NULL", (str(name or "").strip().lower(),))
        if row is None or (direction and row["direction"] != direction):
            raise BlockError(f"no active {'inbound ' if direction == 'in' else 'outbound ' if direction == 'out' else ''}webhook named {name!r}; see webhook_list")
        return dict(row)

    def _secrets(self, row: dict[str, Any]) -> list[str]:
        current, previous, _ = _secret_names(row["id"])
        out = [s for s in (self.core.secret(current),) if s]
        if row["previous_until"] and row["previous_until"] > self.core.clock() and (old := self.core.secret(previous)) and old not in out:
            out.append(old)
        return out

    def _url(self, row: dict[str, Any]) -> str | None:
        if row["direction"] == "out":
            return None
        url = f"{self.core.base_url.rstrip('/')}{HOOK_PATH}/{row['id']}"
        return url + "/<secret>" if row["verify"] == "path" else url

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        opts = json.loads(row["settings"] or "{}")
        current, _, url_name = _secret_names(row["id"])
        ready = bool(self.core.secret(current)) and (row["direction"] == "in" or bool(self.core.secret(url_name)))
        out = {"name": row["name"], "id": row["id"], "direction": row["direction"],
               "verify": row["verify"] if row["direction"] == "in" else "standard", "secret": "set" if ready else "waiting",
               "events": row["events"], "refused": row["refused"], "last_event_at": _iso(row["last_event_at"]),
               "last_refusal": row["last_refusal"], "created_at": _iso(row["created_at"]), "rotated_at": _iso(row["rotated_at"]),
               "previous_until": _iso(row["previous_until"]) if row["previous_until"] and row["previous_until"] > self.core.clock() else None,
               "revoked_at": _iso(row["revoked_at"])}
        if row["direction"] == "in":
            out.update(url=self._url(row), to=row["target"], project=row["project"],
                       **{key: opts[key] for key in ("header", "prefix", "encoding", "tolerance_s", "fields", "template", "event_field") if key in opts})
        else:
            out["address"] = f"{self.name}:out:{row['name']}"
        return out

    def _count(self, hook_id: str, *, refused: str | None = None) -> None:
        if refused:
            self.core.store.x("UPDATE webhooks SET refused = refused + 1, last_refusal = ? WHERE id = ?", (refused, hook_id))
        else:
            self.core.store.x("UPDATE webhooks SET events = events + 1, last_event_at = ? WHERE id = ?", (self.core.clock(), hook_id))

    def _first_seen(self, hook_id: str, key: str) -> bool:
        now = self.core.clock()

        def claim(db: Any) -> bool:
            db.execute("DELETE FROM webhook_seen WHERE at <= ?", (now - SEEN_TTL_S,))
            return db.execute("INSERT OR IGNORE INTO webhook_seen(hook, key, at) VALUES (?, ?, ?)", (hook_id, key[:300], now)).rowcount == 1

        return bool(self.core.store.tx(claim))

    # Lifecycle ----------------------------------------------------------------------------------

    def routes(self) -> list[Route]:
        return [Route(f"{HOOK_PATH}/{{hook}}", self.receive, methods=["POST"]),
                Route(f"{HOOK_PATH}/{{hook}}/{{secret}}", self.receive, methods=["POST"])]

    async def start(self) -> None:
        self.http = httpx.AsyncClient(timeout=10, transport=self.core.http_transport, follow_redirects=False,
                                      headers={"User-Agent": "SwB-Webhook"})

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()

    def status(self) -> dict[str, Any]:
        rows = [dict(r) for r in self.core.store.q("SELECT * FROM webhooks WHERE revoked_at IS NULL ORDER BY name")]
        views = [self._view(r) for r in rows]
        return {"configured": True, "state": "connected", "inbound": sum(r["direction"] == "in" for r in views),
                "outbound": sum(r["direction"] == "out" for r in views), "waiting": [r["name"] for r in views if r["secret"] == "waiting"],
                "events": sum(r["events"] for r in views), "refused": sum(r["refused"] for r in views)}

    async def targets(self) -> list[dict[str, Any]]:
        return [{"kind": "out", "id": r["name"], "name": f"Webhook {r['name']}", "slugs": [f"webhook-{r['name']}"]}
                for r in self.core.store.q("SELECT name FROM webhooks WHERE direction = 'out' AND revoked_at IS NULL ORDER BY name")]

    def actions(self) -> list[Action]:
        return [Action("create", "Create a webhook", "Create a named inbound or outbound webhook.", self.create),
                Action("list", "List webhooks", "List webhooks with their URL, target, counts and secret state.", self.list_hooks, read_only=True),
                Action("rotate", "Rotate a webhook secret", "Ask for a new webhook secret, keeping the old one for a grace period.", self.rotate),
                Action("revoke", "Revoke a webhook", "Revoke a webhook and delete its secrets.", self.revoke, destructive=True, idempotent=True),
                Action("test", "Test a webhook", "Send a signed sample event through the webhook.", self.test),
                Action("send", "Send to a webhook", "POST a signed message to an outbound webhook.", self.send, capped=True)]

    def mcp_tools(self, mcp: Any, kit: Any) -> None:
        from .mcp_tools import register

        register(self, mcp, kit)

    # Owner tools --------------------------------------------------------------------------------

    @staticmethod
    def _owner(call: Call) -> None:
        if not call.principal.is_unrestricted_owner:
            raise BlockRefused(OWNER_REQUIRED, OWNER_REQUIRED)

    async def create(self, call: Call, *, name: str, direction: str = "in", verify: str | None = None, to: str | None = None,
                     project: str | None = None, header: str | None = None, prefix: str | None = None, encoding: str | None = None,
                     tolerance_s: int | None = None, fields: list[str] | None = None, template: str | None = None,
                     event_field: str | None = None) -> dict[str, Any]:
        self._owner(call)
        name = str(name or "").strip().lower()
        if not NAME_RE.fullmatch(name):
            raise BlockError("name must be 1 to 40 lowercase letters, digits or dashes, starting with a letter or digit")
        if direction not in DIRECTIONS:
            raise BlockError("direction must be in or out")
        if self.core.store.one("SELECT 1 FROM webhooks WHERE name = ? AND revoked_at IS NULL", (name,)):
            raise BlockError(f"webhook {name!r} exists; rotate or revoke it, or pick another name")
        hook_id = "wh_" + secrets.token_hex(16)
        current, _, url_name = _secret_names(hook_id)
        now = self.core.clock()
        actor = f"{call.principal.kind}:{call.principal.source}"
        if direction == "out":
            if any(value is not None for value in (verify, to, project, header, prefix, encoding, tolerance_s, fields, template, event_field)):
                raise BlockError("an outbound webhook takes only a name; its URL and signing secret go in through the link")
            self.core.store.x("INSERT INTO webhooks(id, name, direction, verify, created_at, created_by) VALUES (?, ?, 'out', 'standard', ?, ?)",
                              (hook_id, name, now, actor))
            link = self.core.claims.mint_group(f"Webhook {name}", [Field(url_name, "Webhook URL", "url"),
                                                                    Field(current, "Signing secret", "webhook_secret")],
                                               self.core.base_url)
            call.note(target=name, ref=hook_id)
            return {**self._view(self._active(name)), "next": [
                {"by": "person", "do": "Save the receiver's HTTPS URL and a signing secret.", "link": link},
                {"by": "you", "do": f"Send to {self.name}:out:{name} after the link is used."}]}
        try:
            verify = v.scheme(verify)
            opts = v.settings(verify, header=header, prefix=prefix, encoding=encoding, tolerance_s=tolerance_s)
        except ValueError as exc:
            raise BlockError(str(exc)) from None
        if fields is not None:
            if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FIELDS or any(not isinstance(f, str) or not FIELD_RE.fullmatch(f) for f in fields):
                raise BlockError(f"fields must list 1 to {MAX_FIELDS} dotted paths such as data.object.id")
            opts["fields"] = list(dict.fromkeys(fields))
        if template is not None:
            if not isinstance(template, str) or not template.strip() or len(template) > TEMPLATE_LIMIT or any(ord(c) < 32 for c in template):
                raise BlockError(f"template must be one line of up to {TEMPLATE_LIMIT} characters")
            if any(not FIELD_RE.fullmatch(path) for path in PLACEHOLDER_RE.findall(template)):
                raise BlockError("template placeholders look like {data.object.id}")
            opts["template"] = template.strip()
        if event_field is not None:
            if not isinstance(event_field, str) or not FIELD_RE.fullmatch(event_field):
                raise BlockError("event_field must be a dotted path such as event.type")
            opts["event_field"] = event_field
        if not isinstance(to, str) or not to.strip():
            raise BlockError("Pass to: an agent (agent:claude), a route, a session or another target from targets")
        to = to.strip()
        try:
            addr = await self.core.resolve_to(to)
        except Exception as exc:
            raise BlockError(str(exc)) from None
        if addr.block == self.name:
            raise BlockError("an inbound webhook can't target another webhook; choose an agent, route or session")
        if project is not None:
            try:
                project = self.core.projects.get(project).slug
            except ProjectError as exc:
                raise BlockError(str(exc)) from None
        self.core.store.x("INSERT INTO webhooks(id, name, direction, verify, settings, target, project, created_at, created_by) "
                          "VALUES (?, ?, 'in', ?, ?, ?, ?, ?, ?)", (hook_id, name, verify, dumps(opts), to, project, now, actor))
        channel = f"channel:{self.name}/{name}"
        if self.core.visibility.explicit(channel) is None:
            self.core.visibility.set(channel, "shared", actor, now)
        link = self.core.claims.mint(current, self.core.base_url, kind="webhook_secret", label=f"Webhook secret for {name}")
        call.note(target=name, ref=hook_id)
        view = self._view(self._active(name))
        steps = [{"by": "person", "do": self._secret_step(verify), "link": link},
                 {"by": "person", "do": f"Paste {view['url']} into the provider's webhook settings."}]
        if share := await self._target_step(name, to, addr):
            steps.append(share)
        steps.append({"by": "you", "do": f"Call webhook_test with name {name} after the link is used."})
        return {**view, "next": steps}

    @staticmethod
    def _secret_step(verify: str) -> str:
        if verify in v.PRESETS:
            return "Copy the signing secret from the provider and save it here."
        if verify == "path":
            return "Save a long random secret here, then add it to the end of the URL."
        return "Save a long random secret here, and set the same secret in the sender."

    async def _target_step(self, name: str, to: str, addr: Address) -> dict[str, Any] | None:
        """The visibility_set call that lets this hook wake its target, when the target is private to it."""
        who = Who(owner=False, workspace=f"{self.name}-{name}")
        slugs = self.core.slug_map(await self.core.targets()) if addr.block != "agent" else {}
        resources = self.core.target_visibility_resources(to, addr, slugs=slugs)
        if not resources or self.core.visibility.allows(who, *resources):
            return None
        rule = self.core.visibility.rule(*resources)
        entry = f"workspace:{self.name}-{name}"
        value = sorted({*rule, entry}) if isinstance(rule, tuple) else [entry]
        return {"by": "you", "do": f"Events wait as context until {resources[0]} admits this webhook.",
                "call": {"tool": "visibility_set", "arguments": {"resource": resources[0], "visibility": value}}}

    async def list_hooks(self, call: Call, *, include_revoked: bool = False) -> dict[str, Any]:
        self._owner(call)
        where = "" if include_revoked else "WHERE revoked_at IS NULL"
        return {"webhooks": [self._view(dict(r)) for r in self.core.store.q(f"SELECT * FROM webhooks {where} ORDER BY name, created_at")]}

    async def rotate(self, call: Call, *, name: str, grace_s: int = 0) -> dict[str, Any]:
        self._owner(call)
        if type(grace_s) is not int or not 0 <= grace_s <= MAX_GRACE_S:
            raise BlockError(f"grace_s must be a whole number of seconds from 0 to {MAX_GRACE_S}")
        row = self._active(name)
        current, previous, url_name = _secret_names(row["id"])
        now = self.core.clock()
        old = self.core.secret(current)
        if grace_s and old:
            self.core.claims.save_secret(previous, old)
        else:
            self.core.claims.save_secret(previous, None)
        self.core.store.x("UPDATE webhooks SET rotated_at = ?, previous_until = ? WHERE id = ?",
                          (now, now + grace_s if grace_s and old else None, row["id"]))
        call.note(target=row["name"], ref=row["id"])
        if row["direction"] == "in":
            link = self.core.claims.mint(current, self.core.base_url, kind="webhook_secret", label=f"Webhook secret for {row['name']}")
            step = "Save the new secret here, then set it in the sender."
        else:
            link = self.core.claims.mint_group(f"Webhook {row['name']}", [Field(url_name, "Webhook URL", "url", optional=True),
                                                                           Field(current, "Signing secret", "webhook_secret")],
                                               self.core.base_url)
            step = "Save the new signing secret here, and a new URL if it changed. Then set the secret in the receiver."
        return {**self._view(self._active(name)), "next": [{"by": "person", "do": step, "link": link}]}

    async def revoke(self, call: Call, *, name: str) -> dict[str, Any]:
        self._owner(call)
        row = self._active(name)
        self.core.store.x("UPDATE webhooks SET revoked_at = ? WHERE id = ?", (self.core.clock(), row["id"]))
        for secret in _secret_names(row["id"]):
            self.core.claims.save_secret(secret, None)
        call.note(target=row["name"], ref=row["id"])
        return {"ok": True, "name": row["name"], "revoked": True}

    async def test(self, call: Call, *, name: str) -> dict[str, Any]:
        self._owner(call)
        row = self._active(name)
        if row["direction"] == "out":
            return await self._deliver(row, {"type": "swb.test", "data": {"text": "SwB test message", "webhook": row["name"]}})
        now = int(self.core.clock())
        found = self._secrets(row)
        if not found:
            raise BlockError(f"webhook {row['name']!r} waits for its secret; open the link from webhook_create or webhook_rotate")
        opts = json.loads(row["settings"] or "{}")
        body = _json(self._sample(row["verify"], now))
        headers = {k.lower(): val for k, val in v.sign(row["verify"], opts, found[0], body, now, msg_id=f"msg_{secrets.token_hex(8)}").items()}
        headers["content-type"] = "application/json"
        status, response, work = self._ingest(row, headers, body, "webhook_test", found[0] if row["verify"] == "path" else None)
        if work is None:
            return {"ok": False, "status": status, **response}
        outcome = await self._dispatch(*work)
        return {"ok": outcome["outcome"] == "woke", "status": status, **outcome}

    @staticmethod
    def _sample(verify: str, now: int) -> dict[str, Any]:
        ident = secrets.token_hex(8)
        if verify == "stripe":
            return {"id": f"evt_test_{ident}", "object": "event", "type": "swb.test", "created": now,
                    "data": {"object": {"id": f"test_{ident}", "object": "test"}}}
        if verify == "linear":
            return {"type": "Test", "action": "create", "webhookTimestamp": now * 1000, "webhookId": ident,
                    "data": {"id": ident, "title": "SwB test event"}}
        if verify == "sentry":
            return {"action": "triggered", "data": {"event": {"event_id": ident, "title": "SwB test event"}}}
        return {"type": "swb.test", "id": ident, "timestamp": _iso(now), "data": {"text": "SwB test event"}}

    # Inbound ------------------------------------------------------------------------------------

    def _refuse(self, row: dict[str, Any] | None, ip: str, reason: str, status: int, *, retry_after: float | None = None,
                body: dict[str, Any] | None = None) -> JSONResponse:
        target = row["name"] if row else "unknown"
        if row:
            self._count(row["id"], refused=reason)
        self.core.audit.record_refusal(origin=self.name, client=ip, destination=self.name, action="webhook", target=target, detail=reason)
        headers = {"Retry-After": str(int(retry_after + 0.999))} if retry_after is not None else None
        return JSONResponse(body or {"error": reason}, status, headers=headers)

    async def receive(self, request: Request) -> Response:
        ip = client_ip(request.scope)
        hook_id, path_secret = request.path_params["hook"], request.path_params.get("secret")
        if (wait := self.limiter.blocked("webhook_fail", ip)) is not None:
            return self._refuse(None, ip, "rate limit", 429, retry_after=wait)
        found = self.core.store.one("SELECT * FROM webhooks WHERE id = ?", (hook_id,)) if HOOK_ID_RE.fullmatch(hook_id) else None
        row = dict(found) if found is not None else None
        if row is None or row["direction"] != "in":
            self.limiter.hit("webhook_fail", ip)
            return self._refuse(None, ip, "unknown webhook", 404)
        if row["revoked_at"]:
            self.limiter.hit("webhook_fail", ip)
            return self._refuse(row, ip, "revoked webhook", 410)
        for bucket, key in (("webhook_hook", row["id"]), ("webhook_ip", f"{row['id']}:{ip}")):
            if (wait := self.limiter.blocked(bucket, key)) is not None:
                return self._refuse(row, ip, "rate limit", 429, retry_after=wait)
        self.limiter.hit("webhook_ip", f"{row['id']}:{ip}")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > self.max_body:
                return self._refuse(row, ip, "body too large", 413)
            chunks.append(chunk)
        headers = {k.lower(): val for k, val in request.headers.items()}
        status, response, work = self._ingest(row, headers, b"".join(chunks), ip, path_secret)
        if status in (401, 404):
            self.limiter.hit("webhook_fail", ip)
        elif status == 200:
            self.limiter.hit("webhook_hook", row["id"])
        return JSONResponse(response, status, background=BackgroundTask(self._dispatch, *work) if work else None)

    def _ingest(self, row: dict[str, Any], headers: dict[str, str], body: bytes, ip: str,
                path_secret: str | None) -> tuple[int, dict[str, Any], tuple[Any, ...] | None]:
        """Verify and parse one request. Returns the status, the response body, and the dispatch work when accepted."""
        found = self._secrets(row)
        if not found:
            self._refuse(row, ip, "secret not set", 503)
            return 503, {"error": "secret not set"}, None
        opts = json.loads(row["settings"] or "{}")
        try:
            verified = v.verify_any(row["verify"], opts, found, body, headers, self.core.clock(), path_secret=path_secret)
        except v.VerifyError as exc:
            status = 404 if exc.reason == "unknown webhook" else 401
            self._refuse(row, ip, exc.reason, status)
            return status, {"error": "unknown webhook" if status == 404 else "invalid signature"}, None
        try:
            data = self._parse(body, headers.get("content-type", ""))
        except ValueError:
            self._refuse(row, ip, "invalid body", 400)
            return 400, {"error": "invalid body"}, None
        if verified.replay_key and not self._first_seen(row["id"], verified.replay_key):
            self._refuse(row, ip, "duplicate delivery", 200)
            return 200, {"ok": True, "duplicate": True}, None
        self._count(row["id"])
        digest = hashlib.sha256(body).hexdigest()
        delivery = verified.delivery or f"d_{digest[:16]}"
        return 200, {"ok": True}, (row, opts, data, headers, delivery, ip)

    @staticmethod
    def _parse(body: bytes, content_type: str) -> Any:
        lead = body.lstrip()[:1]
        if lead in (b"{", b"[") or "json" in content_type.lower():
            if not body.strip():
                return {}
            try:
                return json.loads(body)
            except RecursionError:
                raise ValueError("nested too deep") from None
        text = body.decode("utf-8", "replace")
        if "application/x-www-form-urlencoded" in content_type.lower():
            return dict(parse_qsl(text, keep_blank_values=True, max_num_fields=FORM_FIELDS))
        return text if text.strip() else {}

    @staticmethod
    def _event(row: dict[str, Any], opts: dict[str, Any], data: Any, headers: dict[str, str]) -> str | None:
        verify = row["verify"]
        if verify == "linear":
            value = ".".join(str(x) for x in (_pick(data, "type"), _pick(data, "action")) if isinstance(x, str))
        elif verify == "sentry":
            value = ".".join(x for x in (headers.get("sentry-hook-resource"), _pick(data, "action")) if isinstance(x, str) and x)
        elif "event_field" in opts:
            value = _pick(data, opts["event_field"])
        else:
            value = next((x for path in PRESET_EVENT.get(verify, GENERIC_EVENT) if isinstance(x := _pick(data, path), str) and x), None)
        return value if isinstance(value, str) and EVENT_RE.fullmatch(value) else None

    def _parts(self, name: str, opts: dict[str, Any], data: Any) -> dict[str, AuthoredText]:
        author = f"{self.name}:{name}"
        parts: dict[str, AuthoredText] = {}
        if template := opts.get("template"):
            parts["summary"] = {"text": PLACEHOLDER_RE.sub(lambda m: _text(_pick(data, m[1])), template), "author_id": author, "owner": False}
        if fields := opts.get("fields"):
            picked = {path: _value(_pick(data, path)) for path in fields}
            parts["fields"] = {"text": json.dumps(picked, ensure_ascii=False, indent=1), "author_id": author, "owner": False}
        elif not template or data:
            raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            parts["payload"] = {"text": raw if len(raw) <= PAYLOAD_LIMIT else raw[:PAYLOAD_LIMIT] + "...", "author_id": author, "owner": False}
        return parts

    async def _dispatch(self, row: dict[str, Any], opts: dict[str, Any], data: Any, headers: dict[str, str], delivery: str,
                        ip: str) -> dict[str, Any]:
        from ...core import CoreError

        name, target, project = row["name"], row["target"], row["project"]
        event = self._event(row, opts, data, headers)
        generated = {"webhook": name, "event": event, "delivery": neutralize(delivery)}
        rendered = frame_parts(self._parts(name, opts, data), f"in webhook {name}")
        details = FramedText(join_parts(rendered))
        title = subject(f"Webhook {name}", "", None, event or "Event", verb="", trust="external")
        tail = f"Webhook {name} delivery {generated['delivery']}."
        text = wake_text(title, details, None, tail)
        who = Who(owner=False, workspace=f"{self.name}-{name}")

        def audit(action: str, outcome: str, detail: str | None = None) -> None:
            self.core.audit.record(origin=self.name, source=f"{self.name}:{name}", client=ip, destination=self.name, action=action,
                                   outcome=outcome, target=target, ref=delivery, title=title, detail=detail)

        async def note_context(address: Address | None, reason: str) -> dict[str, Any]:
            item = {**generated, "subject": title, "text": text, "trust": "external", "target": target, "note": CONTEXT_NOTE,
                    "from": {"block": self.name, "space": f"{self.name}-{name}", "trust": "external"}}
            agents = self.core.target_agents(address) if address else set()
            for agent in sorted(agents or {"owner"}):
                found = self.core.store.one("SELECT handle FROM handles WHERE owner = ? AND destination = ? AND ref = ? AND project IS ?",
                                            (agent, self.name, name, project))
                handle = found["handle"] if found else self.core.inbox.open_handle(
                    owner=agent, destination=self.name, ref=name, project=project, title=f"Webhook {name}", meta={}, status="following")
                await self.core.inbox.add(handle, "context", item)
            audit("context_noted", "ok", reason)
            return {"outcome": "context", "reason": reason, "delivery": delivery}

        def drop(reason: str) -> dict[str, Any]:
            audit("webhook", "dropped", reason)
            return {"outcome": "dropped", "reason": reason, "delivery": delivery}

        if not self.core.visibility.allows(who, f"channel:{self.name}/{name}"):
            return drop("channel access restricted")
        try:
            addr = await self.core.resolve_to(target)
        except (BlockError, CoreError, ValueError):
            return await note_context(None, "target is unavailable")
        if addr.block == "agent" and addr.id not in self.core.known_agents():
            return await note_context(None, "target is unavailable")
        try:
            slugs = self.core.slug_map(await self.core.targets()) if addr.block != "agent" else {}
            resources = self.core.target_visibility_resources(target, addr, slugs=slugs)
            if not resources or not self.core.visibility.allows(who, *resources):
                return await note_context(addr, "target access restricted")
            principal = Principal(self.name, ip, self.name, origin=self.name, trust="external", visibility_who=who)
            out = await self.core.send(principal, to=target, task=text, project=project, title=title,
                                       handle_meta={"boundary": project} if project else None)
        except (BlockError, CoreError, ValueError) as exc:
            log.warning("webhook %s wake failed: %s", name, type(exc).__name__)
            audit("webhook", "error", type(exc).__name__)
            return await note_context(addr, "target delivery failed")
        audit("webhook", "ok", "woke")
        return {"outcome": "woke", "delivery": delivery, "conversation": out.get("conversation") if isinstance(out, dict) else None}

    # Outbound -----------------------------------------------------------------------------------

    async def send(self, call: Call, *, task: str, target: str | None = None, title: str | None = None, project: str | None = None,
                   **_: Any) -> dict[str, Any]:
        kind, _, ident = (target or "").partition(":")
        if kind != "out" or not ident:
            raise BlockError(f"send to {self.name}:out:<name>; see webhook_list")
        row = self._active(ident, "out")
        principal = call.principal
        who = principal.visibility_who or Who(owner=principal.trust == "owner")
        if not who.owner and not self.core.visibility.allows(who, f"target:{self.name}-{row['name']}"):
            raise BlockRefused(OWNER_REQUIRED, f"webhook {row['name']} is private to the owner")
        call.note(target=f"{self.name}:out:{row['name']}", title=title, excerpt=task)
        return await self._deliver(row, {"type": "swb.message", "data": {
            "subject": title, "text": task, "from": principal.source, "trust": principal.trust, "project": project,
            "conversation": call.handle}}, call=call)

    async def _deliver(self, row: dict[str, Any], message: dict[str, Any], *, call: Call | None = None) -> dict[str, Any]:
        current, previous, url_name = _secret_names(row["id"])
        url, secret = self.core.secret(url_name), self.core.secret(current)
        if not url or not secret:
            raise BlockError(f"webhook {row['name']!r} waits for its URL and signing secret; open the link from webhook_create")
        if not valid_webhook_url(url):
            raise BlockError(f"webhook {row['name']!r} has an invalid URL; save a public HTTPS URL through webhook_rotate")
        keys = [secret] + ([old] if row["previous_until"] and row["previous_until"] > self.core.clock()
                           and (old := self.core.secret(previous)) and old != secret else [])
        msg_id = "msg_" + secrets.token_hex(12)
        body = _json({"id": msg_id, **message, "timestamp": _iso(self.core.clock())})
        if self.http is None:
            await self.start()
        status, delivered = None, False
        for delay in RETRY_DELAYS:
            if delay:
                await asyncio.sleep(delay)
            stamp = int(self.core.clock())
            try:
                signature = " ".join(v.standard_signature(v.standard_key(k), msg_id, stamp, body) for k in keys)
            except v.VerifyError:
                raise BlockError(f"webhook {row['name']!r} has an unusable signing secret; rotate it") from None
            headers = {"content-type": "application/json", "webhook-id": msg_id, "webhook-timestamp": str(stamp),
                       "webhook-signature": signature}
            try:
                response = await self.http.post(url, content=body, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException):
                continue
            status = response.status_code
            if status < 500:
                delivered = 200 <= status < 300
                break
        if delivered:
            self._count(row["id"])
        else:
            self._count(row["id"], refused=f"receiver status {status}" if status else "receiver unreachable")
        if call is not None:
            call.note(ref=msg_id, outcome="ok" if delivered else "error", detail=None if delivered else f"status {status}")
        return {"ok": delivered, "delivered": delivered, "status": status, "webhook": row["name"], "id": msg_id}
