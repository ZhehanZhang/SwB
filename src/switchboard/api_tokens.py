"""Owner-console credential metadata. No credential is decrypted for this view."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .audit import iso
from .blocks.webhook import _secret_names
from .claims import CLAIM_TTL_S, NAME_RE
from .env import env_value

if TYPE_CHECKING:
    from .core import Switchboard


def _label(value: str | None) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value) else None


def _actor(value: str | None) -> str | None:
    if not value:
        return None
    kind, _, name = value.partition(":")
    if kind == "admin" or (kind == "key" and name == "owner"):
        return "you"
    return _label(name) if kind in ("connect", "inbox_key", "key", "oauth") else None


def _credential_refs(core: Switchboard) -> dict[str, tuple[str, str, list[str]]]:
    refs: dict[str, tuple[str, str, list[str]]] = {}

    def add(ref: str | None, kind: str, label: str, used_by: str | None) -> None:
        if not ref or not NAME_RE.fullmatch(ref):
            return
        _, _, uses = refs.setdefault(ref, (kind, label, []))
        if used_by and used_by not in uses:
            uses.append(used_by)

    for block in core.blocks.values():
        kind = block.manifest.id
        uses = _label(block.name)
        if kind == "capy":
            add("CAPY_API_KEY", "capy", "Capy API key", uses)
            for ref in sorted({block.send_key_env, *block.keys} - {"CAPY_API_KEY"}):
                add(ref, "capy", "Capy API key", uses)
        elif kind == "slack":
            for workspace, names in block.token_names.items():
                for ref in names:
                    add(ref, "slack", "Slack token", _label(workspace))
        elif kind in ("discord", "telegram"):
            add(block.token_env, kind, f"{block.manifest.title} bot token", uses)
        elif kind == "github":
            add(block.secret_env, "github", "GitHub webhook secret", uses)
            add(block.api.app_id_env, "github", "GitHub App ID", uses)
            add(block.api.private_key_env, "github", "GitHub private key", uses)
            for sink in (block.config.get("sinks") or {}).values():
                add(sink.get("url_env"), "github", "GitHub sink URL", uses)
                if sink.get("signature") == "stripe":
                    add(sink.get("secret_env"), "github", "GitHub sink signing secret", uses)
        elif kind == "claude-routine":
            for routine in block.configured.values():
                add(routine["token_env"], "claude", "Claude routine token", _label(routine["project"]) or uses)
        elif kind == "grok-routine":
            for routine in block.routines().values():
                scope = _label(routine["project"]) or uses
                add(routine["url_env"], "grok", "Grok webhook URL", scope)
                add(routine["secret_env"], "grok", "Grok webhook secret", scope)
        elif kind == "webhook":
            for hook in core.store.q("SELECT id, name, direction FROM webhooks WHERE revoked_at IS NULL"):
                secret, previous, url = _secret_names(hook["id"])
                outbound = hook["direction"] == "out"
                label = "Webhook signing secret" if outbound else "Webhook secret"
                add(secret, kind, label, hook["name"])
                add(previous, kind, label, hook["name"])
                if outbound:
                    add(url, kind, "Webhook URL", hook["name"])
    return refs


async def tokens(request: Request) -> JSONResponse:
    core = request.app.state.core
    now = core.clock()
    refs = _credential_refs(core)
    claims = {r["name"]: r for r in core.store.q(
        """WITH pending AS (
             SELECT name, MAX(expires_at) AS pending_until, MAX(require_https) AS require_https
             FROM claim_links WHERE expires_at > ? AND resolved = 0 GROUP BY name
           )
           SELECT s.name, COALESCE(p.require_https, s.require_https) AS require_https, s.sealed IS NOT NULL AS stored, p.pending_until
             FROM secrets s LEFT JOIN pending p ON p.name = s.name
           UNION ALL
           SELECT name, require_https, 0, pending_until FROM pending WHERE name NOT IN (SELECT name FROM secrets)""",
        (now,),
    ) if NAME_RE.fullmatch(r["name"])}
    saved = {r["target"]: r for r in core.store.q(
        """SELECT target, at, source FROM audit
           WHERE id IN (SELECT MAX(id) FROM audit WHERE action = 'token_claim' AND outcome = 'ok' GROUP BY target)"""
    )}

    waiting: list[dict[str, Any]] = []
    stored: list[dict[str, Any]] = []
    for ref in sorted(refs.keys() | claims.keys()):
        claim = claims.get(ref)
        kind, name, used_by = refs.get(ref, ("generic", "Webhook URL" if claim and claim["require_https"] else "Token", []))
        item = {"id": f"credential:{ref}", "name": name, "label": ref, "kind": kind, "used_by": used_by}
        if claim and (expires := claim["pending_until"]) is not None:
            waiting.append({**item, "asked_by": None, "requested_at": iso(expires - CLAIM_TTL_S), "expires_at": iso(expires)})
        from_env = bool(env_value(core.cfg.env, ref))
        if from_env or (claim and claim["stored"]):
            last = saved.get(ref) if not from_env else None
            stored.append({**item, "added_by": _actor(last["source"]) if last else None,
                           "added_at": iso(last["at"]) if last else None, "revoke_id": None})

    routine = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
    if routine:
        for pending in routine.pending(details=True):
            if label := _label(pending["name"]):
                project = _label(pending["project"])
                waiting.append({"id": f"routine:{label}", "name": "Claude routine token", "label": label, "kind": "claude",
                                "used_by": [project] if project else [], "asked_by": _actor(pending["requested_by"]),
                                "requested_at": iso(pending["created_at"]), "expires_at": iso(pending["expires_at"])})
        for row in core.store.q("SELECT name, project, created_at, created_by FROM routines ORDER BY name"):
            if row["name"] not in routine.configured and (label := _label(row["name"])):
                project = _label(row["project"])
                stored.append({"id": f"routine:{label}", "name": "Claude routine token", "label": label, "kind": "claude",
                               "used_by": [project] if project else [], "added_by": _actor(row["created_by"]),
                               "added_at": iso(row["created_at"]), "revoke_id": None, "routine_block": routine.name})

    for key in core.connections.list():
        project = _label(key["project"])
        stored.append({"id": key["id"], "name": "Agent access key", "label": key["agent"], "kind": "generic",
                       "used_by": [project] if project else [], "added_by": _actor(key["created_by"]),
                       "added_at": iso(key["created_at"]), "revoke_id": key["id"],
                       "memory_scopes": list(core.connections.memory_scopes(key["id"]))})

    for hook in core.hook_wake.rows():
        if agent := _label(hook["agent"]):
            stored.append({"id": f"hook:{agent}", "name": "Muse hook", "label": agent, "kind": "muse", "used_by": [],
                           "added_by": _actor(hook["created_by"]), "added_at": iso(hook["created_at"]), "revoke_id": f"hook:{agent}"})

    waiting.sort(key=lambda row: row["requested_at"] or "", reverse=True)
    return JSONResponse({"waiting": waiting, "stored": stored})


TOKEN_ROUTES = [
    Route("/tokens", tokens, methods=["GET"]),
]
