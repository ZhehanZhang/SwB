"""Owner console reads, mounted behind the app's Cloudflare Access check."""

from __future__ import annotations

import asyncio
import json
import math
import re
from importlib.resources import files
from ipaddress import ip_address
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .agents import PRESETS
from .api_display import (
    CAPY_AVATAR_URL, captain_avatar_url, captain_target, captain_targets, capy_label, conversation_captain, display_name,
    local_avatar_url, slack_profile_avatar,
)
from .slack.avatar_files import agent_avatar_url
from .audit import iso
from .blocks import BLOCKS, ON_DEMAND_BLOCKS, load
from .env import env_value
from .inbox_filters import InboxFilters, unread_handles
from .memory import GENERAL, NAMESPACE_RE, MemoryError_, summary as memory_summary
from .modes import CHAT_DROP_ACTION, ChatDrop
from .owner_delivery import OWNER_DELIVERY_PROBLEM
from .projects import ProjectError
from .refusals import CSRF_AUDIT_PATHS, OWNER_NOTE_REQUIRED, SIGN_IN_FAILURES
from .routes import route_audit_name
from .subject import display_subject
from .targets import SLUG_RE
from .visibility import show

_SQLITE_MAX = 2**63 - 1
_ROUTE_DEFAULTS = ("model", "mode", "delivery")
_ROUTE_KINDS = ("captain", "project", "thread", "routine")
_REPO = re.compile(r"[\w.-]+/[\w.-]+")
_AUTH_PATHS = ("/mcp", "/inbox/poll", "/inbox/events", "/register", "/token", "/connect/redeem", *SIGN_IN_FAILURES, *CSRF_AUDIT_PATHS)
_MEMORY_FIELDS = ("id", "scope", "namespace", "key", "title", "tags", "source", "created_at", "updated_at",
                  "path", "created_by", "content_by", "content_trust", "sync_source")
_PROMPT_PACKAGE = "switchboard.prompts.setup"
_PROMPT_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PROJECT = r"[a-z0-9][a-z0-9-]{0,47}"
_SCOPE = rf"(?:general|project:{_PROJECT})"
_CAPY_SENDERS = ("capy_thread", "capy", "captain")
_CHAT_SENDERS = ("slack", "discord", "telegram")
_SOURCE_NAMES = {"github": "GitHub", "slack": "Slack", "discord": "Discord", "telegram": "Telegram", "webhook": "Webhook"}
_AGENT_NAMES = {"grok-bot": "Grok Bot", "grok-routine": "Grok Bot", "dots": "ChatGPT dots", "dot": "ChatGPT dots"}
_WEBHOOK_DETAILS = frozenset({
    "webhook secret unavailable", "missing signature", "invalid signature", "invalid JSON", "expected a JSON object", "invalid delivery id",
    "unhandled event", "not completed", "unrouted repository", "channel mode ignored event", "channel mode off",
    "work mode requires an owner event", "sender ignored",
    "channel access restricted", "target access restricted", "sender rate limit", "assist target is missing",
    "triage target is missing", "work target is missing", "assist target is unavailable", "triage target is unavailable",
    "work target is unavailable", "CoreError", "BlockError", "ValueError",
})


def _fields(row: Any, *names: str) -> dict[str, Any]:
    return {key: row[key] for key in names if key in row.keys()}


def _scalars(row: dict[str, Any], *names: str) -> dict[str, Any]:
    return {k: v for k, v in _fields(row, *names).items() if v is None or isinstance(v, (str, int, float))}


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _reference(value: Any) -> str | None:
    return value if isinstance(value, str) and value and not re.search(r"://|[\r\n]", value) else None


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _number(request: Request, name: str, default: int, low: int, high: int = _SQLITE_MAX) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    if not re.fullmatch(r"[0-9]{1,19}", raw) or not low <= int(raw) <= high:
        raise ValueError(f"invalid {name}")
    return int(raw)


async def health(request: Request) -> JSONResponse:
    core = request.app.state.core
    report = core.health()
    problems = core.cfg.problems()
    if OWNER_DELIVERY_PROBLEM in report["problems"]:
        problems.append(OWNER_DELIVERY_PROBLEM)
    # Provider diagnostics can include upstream errors and URLs. Show which block needs attention, not its raw error.
    problems += [f"{name}: check block status" for name, block in core.blocks.items() if block.problems()]
    revision = env_value(core.cfg.env, "SWB_REVISION") or ""
    return JSONResponse({
        **_fields(report, "status", "mirror", "static_keys", "turn_cap", "mcp_enabled", "owner_delivery"),
        **({"commit": revision.lower()} if re.fullmatch(r"[0-9a-fA-F]{40}", revision) else {}),
        "blocks": {name: _fields(row, "use", "kind", "level", "online") for name, row in report["blocks"].items()},
        "problems": problems,
    })


async def routes(request: Request) -> JSONResponse:
    core = request.app.state.core
    configured = core.routes.list()
    captains = await captain_targets(core, (route.destination for route in configured if route.target.get("captain")))
    items = []
    for route in configured:
        block = core.blocks.get(route.destination)
        kinds = tuple(t.kind for t in block.manifest.targets) if block else _ROUTE_KINDS
        kind, ident = next(((k, ident) for k in kinds if (ident := _reference(route.target.get(k)))), (None, None))
        name = _reference(route.target.get("name")) or ident if ident else None
        if kind in ("captain", "thread"):
            name = capy_label(f"{route.destination}:{kind}:{ident}", route.destination, captains, name=name) or name
        defaults: dict[str, Any] = {k: val for k in _ROUTE_DEFAULTS if (val := _reference(route.defaults.get(k)))}
        if isinstance(repos := route.defaults.get("repos"), list):
            defaults["repos"] = [repo for repo in repos if isinstance(repo, str) and _REPO.fullmatch(repo)]
        items.append({
            **_fields(route.as_dict(), "id", "destination", "agent", "project", "level", "allow_override", "note", "updated_by", "updated_at"),
            "target": {"destination": route.destination, "kind": kind, "id": ident, "name": name},
            "defaults": defaults,
        })
    return JSONResponse({"routes": items})


async def visibility(request: Request) -> JSONResponse:
    core = request.app.state.core
    return JSONResponse({"resources": core.visibility.rows(await core.visibility_resources()),
                         "defaults": {k: show(v) for k, v in core.visibility.defaults.items()}})


async def audit(request: Request) -> JSONResponse:
    try:
        limit = _number(request, "limit", 50, 1, 200)
        cursor = _number(request, "cursor", 0, 1)
    except ValueError as exc:
        return _error(str(exc))
    core = request.app.state.core
    params = request.query_params
    actions = list(dict.fromkeys(a.strip() for name in ("action", "actions") for a in params.get(name, "").split(",") if a.strip()))
    if len(actions) > 50 or any(len(a) > 100 for a in actions):
        return _error("invalid actions")
    try:
        rows, next_cursor = core.audit.page(actions=actions, origin=params.get("origin"), outcome=params.get("outcome"),
                                            limit=limit, cursor=cursor or None)
    except ValueError:
        return _error("invalid cursor")
    captains = await captain_targets(core, (
        name for row in rows for name in (row["destination"], (row["target"] or "").partition(":")[0])
        if row["action"] in ("route_set", "route_remove", "auto_route")
        or "jam_" in ((row["target"] or "") + (row["ref"] or "")).lower()
    ))
    return JSONResponse({"items": [_audit_item(core, row, captains) for row in rows], "next_cursor": next_cursor})


def _audit_item(core: Any, row: Any, captains: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields = _fields(row, "id", "origin", "source", "client", "destination", "action", "outcome", "target", "ref")
    action, target, ref = fields["action"], fields["target"] or "", fields["ref"]
    project: str | None = None
    metadata: dict[str, Any] = {}
    detail = None
    recorded_detail = row["detail"]
    if action == "webhook" and recorded_detail in _WEBHOOK_DETAILS:
        detail = recorded_detail
    elif action == "auth_failure" and (recorded_detail in SIGN_IN_FAILURES.get(target, ())
                                      or (recorded_detail == "csrf" and (not target or target in CSRF_AUDIT_PATHS))):
        detail = recorded_detail
    elif action == CHAT_DROP_ACTION and fields["outcome"] == "dropped" and recorded_detail in ChatDrop:
        detail = recorded_detail
    elif action == "triage.notify_owner" and fields["outcome"] == "refused" and recorded_detail == OWNER_NOTE_REQUIRED:
        detail = recorded_detail
    elif action == "owner_delivery" and recorded_detail in ("active", "project", "global", "owner_delivery_pending"):
        detail = recorded_detail
    elif action == "tool_refused" and fields["destination"] == "mcp":
        from .blocks.mcp.policy import REFUSAL_DETAILS

        if recorded_detail in REFUSAL_DETAILS:
            detail = recorded_detail
    if action in ("auth_failure", "auth_missing", "oauth_refused", "rate_limited"):
        if fields["target"] not in _AUTH_PATHS:
            fields["target"] = None
        fields["ref"] = None
        try:
            fields["client"] = str(ip_address(fields["client"])) if "%" not in fields["client"] else None
        except ValueError:
            fields["client"] = None
    else:
        for value, field in ((target, "target_label"), (ref, "ref_label")):
            if label := capy_label(value, fields["destination"], captains):
                metadata[field] = label
        if captain := captain_target(target, fields["destination"], captains):
            metadata["target_avatar_url"] = captain_avatar_url(core, captain)
        if ref and _reference(ref):
            project = core.inbox.project_for_ref(fields["destination"], ref)
        if match := re.fullmatch(rf"project:({_PROJECT})", target):
            project = project or match[1]
        if action in ("route_set", "route_remove", "auto_route"):
            if match := re.fullmatch(rf"(\*|{_PROJECT})/(\*|{_PROJECT})", target):
                if match[1] != "*":
                    metadata["agent"] = match[1]
                if match[2] != "*":
                    project = project or match[2]
            if fields["outcome"] == "ok":
                try:
                    route = json.loads(row["excerpt"])
                except ValueError:
                    route = None
                if isinstance(route, dict):
                    if name := route_audit_name(route.get("target_name")):
                        metadata["target_name"] = capy_label(name, fields["destination"], captains) or name
                        if captain := captain_target(name, fields["destination"], captains):
                            metadata["target_avatar_url"] = captain_avatar_url(core, captain)
                    if type(count := route.get("repo_count")) is int and count >= 0:
                        metadata["repo_count"] = count
        elif action in ("auto_link", "project_create", "project_update") and re.fullmatch(_PROJECT, target):
            project = project or target
            if action in ("project_create", "project_update") and fields["outcome"] == "ok":
                detail = recorded_detail
        elif action in ("routine_add", "routine_claim"):
            if match := re.fullmatch(rf"(?:routine )?(trig_[A-Za-z0-9]{{1,80}}) for ({_PROJECT})( \(new project\))?", row["excerpt"]):
                metadata["routine_id"] = match[1]
                if match[3]:
                    metadata["created_project"] = True
                if match[2] != GENERAL:
                    project = project or match[2]
        elif action == "routine_register":
            if match := re.fullmatch(rf"routine register for ({_PROJECT}): (pending|candidates)", row["excerpt"]):
                metadata["state"] = match[2]
                if match[1] != GENERAL:
                    project = project or match[1]
        elif action in ("memory_move", "memory_promote") and fields["destination"] == "memory":
            if match := re.fullmatch(rf"({_SCOPE}) -> ({_SCOPE})", target):
                metadata.update(from_scope=match[1], to_scope=match[2])
                scope = match[1] if match[2] == GENERAL else match[2]
                if scope != GENERAL:
                    project = project or scope.removeprefix("project:")
            if re.fullmatch(r"mem_[0-9]{13}[a-f0-9]{8}", row["excerpt"]):
                metadata["memory_id"] = row["excerpt"]
        elif action == "memory_write" and fields["destination"] == "memory" and re.fullmatch(_SCOPE, target):
            metadata["scope"] = target
            detail = recorded_detail
        elif action == "memory_delete" and recorded_detail == "Deleted memory.":
            detail = recorded_detail
        elif action == "turn_cap":
            if match := re.fullmatch(r"paused at ([0-9]{1,10}) turns", row["detail"] or ""):
                metadata["turn_cap"] = int(match[1])
                detail = recorded_detail
        elif action == "visibility_set" and row["detail"] in ("shared", "private"):
            metadata["visibility"] = row["detail"]
            detail = recorded_detail
    return {**fields, "at": iso(row["at"]), "project": project, "metadata": metadata, "detail": detail,
            "excerpt": "" if not row["excerpt"] else None}


async def _agents(core: Any) -> dict[str, Any]:
    targets = await core.targets()
    routines = []
    for block in core.blocks.values():
        if block.manifest.id == "claude-routine":
            routines.extend({"block": block.name, **_fields(r, "name", "kind", "agent", "project", "routine_id", "claude_project",
                                                           "slugs", "source", "status", "primary", "visibility", "created_by",
                                                           "created_at", "last_seen_at")} for r in block.rows(details=True))
        elif block.manifest.id == "grok-routine":
            slugs = {t["id"]: t["slugs"] for t in targets if t["block"] == block.name}
            routines.extend({"block": block.name, "kind": "project" if r["project"] else "general", "agent": "grok",
                             "routine_id": None, "slugs": slugs.get(r["name"], []),
                             "visibility": show(core.visibility.rule(f"target:{r['name']}")),
                             **_fields(r, "name", "project", "source", "status", "primary", "created_by", "created_at",
                                       "last_seen_at")} for r in block.status(details=True)["routines"])
    claims = []
    claude = next((block for block in core.blocks.values() if block.manifest.id == "claude-routine"), None)
    for row in reversed(claude.pending(details=True)) if claude else ():
        new_project = row["new_project"]
        claims.append({**_fields(row, "name", "routine_id", "project", "claude_project", "primary"), "created_by": row["requested_by"],
                       "claim_url": f"/routines/claim/owner/{row['registration']}",
                       "created_at": iso(row["created_at"]), "expires_at": iso(row["expires_at"]),
                       "new_project": _scalars(new_project, "name", "description") if isinstance(new_project, dict) else None})
    keys = [{**_fields(k, "id", "agent", "project", "client", "created_by"), "created_at": iso(k["created_at"]), "last_used_at": iso(k["last_used_at"])}
            for k in core.connections.list()]
    discovered: dict[str, list[dict[str, Any]]] = {"captain": [], "dot": []}
    configured_routes = core.routes.list()
    for target in targets:
        kind = target["kind"]
        if kind in discovered:
            slugs = [s for s in target["slugs"] if SLUG_RE.fullmatch(s)]
            names = {n.casefold() for n in [target["id"], target["address"], *slugs]}
            projects = {r.project for r in configured_routes if r.project and r.destination == target["block"]
                        and isinstance(value := r.target.get(kind), str) and value.casefold() in names}
            if target.get("project"):
                projects.add(target["project"])
            avatar_url = captain_avatar_url(core, target) or (agent_avatar_url(kind) if kind == "dot" else None)
            discovered[kind].append({
                **_fields(target, "id", "name", "kind", "block", "address", "slugs", "level", "project", "created_at"),
                "created_by": _reference(target.get("created_by")),
                **({"avatar_url": avatar_url} if avatar_url else {}),
                "projects": sorted(projects),
                "source": target.get("source", "discovered"),
                "last_seen_at": target.get("last_seen_at"),
                "online": core.blocks[target["block"]].online,
                "visibility": show(core.visibility.rule(*(f"target:{s}" for s in slugs))) if slugs else show(core.visibility.defaults["target"]),
            })
    return {"routines": routines, "keys": keys, "pending_claims": claims, "captains": discovered["captain"], "dots": discovered["dot"],
            "static_keys": [k.name for k in core.cfg.static_keys]}


async def agents(request: Request) -> JSONResponse:
    return JSONResponse(await _agents(request.app.state.core))


async def projects(request: Request) -> JSONResponse:
    core = request.app.state.core
    systems = core.project_systems(await core.targets())
    projects = []
    raw_links = {}
    for project in core.projects.list(include_archived=True):
        raw_links[project.slug] = project.links
        links: dict[str, Any] = {key: val for key in core.projects.link_keys if (val := _reference(project.links.get(key)))}
        if isinstance(repos := project.links.get("repos"), list):
            links["repos"] = [repo for repo in repos if isinstance(repo, str) and _REPO.fullmatch(repo)]
        projects.append({**_fields(project.as_dict(), "slug", "scope", "name", "description", "archived", "private", "created_at", "updated_at"),
                         "links": links})
    safe_links = {project["slug"]: project["links"] for project in projects}
    slack_channels: dict[str, list[str]] = {}
    for block in core.blocks.values():
        if block.manifest.id == "slack":
            for workspace, channels in block.channel_projects.items():
                for channel, project in channels.items():
                    if project:
                        slack_channels.setdefault(project, []).append(f"{workspace}/{channel}")
    for row in systems:
        slug = row["slug"]
        mapping = row["systems"]
        links = safe_links.get(slug, {})
        for key, val in list(mapping.items()):
            if not _reference(val) or (key in raw_links.get(slug, {}) and key not in links):
                del mapping[key]
        if repos := links.get("repos"):
            mapping["repos"] = ", ".join(repos)
        if channels := slack_channels.get(slug):
            mapping["slack channels"] = ", ".join(sorted(set(channels)))
    return JSONResponse({"projects": projects, "systems": systems})


def _sender(core: Any, sender: Any, destination: str, captains: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(sender, dict):
        return None
    out = _scalars(sender, "kind", "name", "label", "agent", "auth", "origin", "trust", "workspace", "channel", "channel_name", "user", "user_name",
                   "thread_id", "thread_ts", "ts", "message_id", "author", "title", "dot", "block", "space", "repo", "topic", "topic_name",
                   "root", "message")
    avatar = local_avatar_url(core, sender.get("avatar_url"))
    if out.get("kind") == "watch":
        provider = _provider(core, out.get("block"))
        out["name"] = f"{_SOURCE_NAMES[provider]} watch" if provider in _SOURCE_NAMES else "Watch digest"
    elif out.get("kind") in _CAPY_SENDERS:
        upstream = sender.get("sender")
        ident = _reference(upstream.get("id") if isinstance(upstream, dict) else out.get("thread_id"))
        captain = conversation_captain(ident, destination, None, captains)
        if captain:
            avatar = captain_avatar_url(core, captain) or CAPY_AVATAR_URL
            if name := display_name(captain.get("name")):
                out["author"] = out["name"] = name
    elif agent := _reference(out.get("agent")):
        out["name"] = _agent_name(core, agent, out.get("label"))
        avatar = avatar or _agent_avatar(agent, out["name"])
    elif name := display_name(out.get("user_name")) or display_name(out.get("author")):
        out["name"] = name
    if not avatar and out.get("kind") not in _CAPY_SENDERS:
        avatar = agent_avatar_url(_reference(out.get("kind")) or "")
    if avatar:
        out["avatar_url"] = avatar
    return out or None


def _agent_name(core: Any, agent: str, label: Any = None) -> str:
    agent = agent.lower()
    if agent == "owner" or agent.startswith("admin:"):
        return "Owner"
    default = _SOURCE_NAMES.get(agent) or _AGENT_NAMES.get(agent) or (PRESETS[agent].label if agent in PRESETS else None) or display_name(label)
    return display_name(core.agents.label(agent, default)) or agent


def _agent_avatar(agent: str, label: str = "") -> str | None:
    return CAPY_AVATAR_URL if agent.lower() == "capy" else agent_avatar_url(agent.lower()) or agent_avatar_url(label)


def _provider(core: Any, name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    block = core.blocks.get(name)
    return block.manifest.id if block else (name if name in _SOURCE_NAMES else None)


def _inbox_first_items(core: Any, handles: list[str]) -> dict[str, tuple[str, dict[str, Any]]]:
    if not handles:
        return {}
    rows = core.store.q(
        "SELECT i.handle, i.kind, i.data FROM handles h JOIN inbox i ON i.id = "
        "(SELECT id FROM inbox WHERE handle = h.handle ORDER BY id LIMIT 1) "
        "WHERE h.handle IN (" + ",".join("?" for _ in handles) + ")",
        handles,
    )
    return {r["handle"]: (r["kind"], json.loads(r["data"])) for r in rows}


def _conversation_target(core: Any, row: dict[str, Any], target: str | None, kind: str | None,
                         captains: dict[str, dict[str, Any]]) -> tuple[str, str | None, str | None]:
    destination = row["destination"]
    meta, destination_kind = row["meta"], _provider(core, destination)
    raw = _reference(row.get("target")) or _reference(target)
    label = raw
    provider = destination_kind
    if raw:
        block, sep, ident = raw.partition(":")
        if sep and block == "agent":
            if ident == "grok" and _object(row["state"].get("routine")).get("fired") is True:
                return "Grok Bot", "routine", agent_avatar_url("grok-bot")
            label = _agent_name(core, ident)
            return label, "agent", _agent_avatar(ident, label)
        if sep and block == "project":
            try:
                label = route_audit_name(core.projects.get(ident).name) or ident
            except ProjectError:
                label = ident
            return label, "project", None
        if sep and ":" in ident:
            kind, ident = ident.split(":", 1)
            provider = _provider(core, block) or provider
            if isinstance(info := meta.get("target"), dict) and info.get(kind) == ident:
                label = route_audit_name(info.get("name")) or label
            if kind == "dot" and isinstance(dot := meta.get("dot"), str) and SLUG_RE.fullmatch(dot):
                label = dot
        if kind == "agent" or not sep and (raw in core.agents or raw == row["owner"]):
            label = _agent_name(core, ident if sep else raw)
            return label, "agent", _agent_avatar(ident if sep else raw, label)
    elif destination_kind == "capy":
        raw = _reference(row["ref"])
    captain = conversation_captain(raw, destination, kind or ("captain" if raw == "captain" else None), captains)
    if captain or kind == "captain" or raw == "captain":
        label = capy_label(raw, destination, captains, name=label)
        if captain:
            label = capy_label(captain["address"], destination, captains)
        return label or "Captain", "captain", (captain_avatar_url(core, captain) if captain else None) or CAPY_AVATAR_URL
    label = capy_label(raw, destination, captains, name=label) or label
    agent = row["owner"]
    if not label:
        label = _agent_name(core, agent)
        return label, "agent", _agent_avatar(agent, label)
    return label, kind or ("thread" if provider == "capy" else None), agent_avatar_url(kind or "") or agent_avatar_url(provider or "")


def _watch_sender(payload: dict[str, Any], sender: dict[str, Any]) -> dict[str, Any]:
    return {**_fields(payload, "block", "space", "channel", "repo"), **sender, "kind": "watch"}


def _telegram_label(block: Any, space: str | None, chat: str | None, topic: Any, name: str | None = None,
                    topic_name: Any = None) -> str:
    if block and chat and re.fullmatch(r"-?[0-9]{1,20}", chat):
        name = name or route_audit_name(block._group_names.get(int(chat)) or block._seen.get(int(chat), {}).get("title"))
    label = name or space or "Telegram"
    if re.fullmatch(r"[1-9][0-9]{0,17}", str(topic)) and int(topic) > 1:
        known = route_audit_name(block.topic_name(chat, int(topic))) if block and chat else None
        label += " › " + (known or route_audit_name(topic_name) or f"Topic {topic}")
    return label


def _discord_label(block: Any, channel: str | None, thread: str | None, name: str | None = None) -> str:
    channels = block.channels if block else {}
    info = channels.get(channel)
    if not thread and info and info.type in (10, 11, 12) and info.parent_id:
        thread = channel
        info = channels.get(info.parent_id)
    channel_name = name or route_audit_name(info.name if info else None)
    label = "#" + channel_name.lstrip("#") if channel_name else "Channel"
    if thread:
        info = channels.get(thread)
        label += " › " + (route_audit_name(info.name if info else None) or "Thread")
    return label


def _channel_target(core: Any, destination: str, raw: str | None, location: dict[str, str]) -> tuple[str, str] | None:
    if not raw:
        return None
    block = destination
    prefix, _, rest = raw.partition(":")
    typed, _, ident = rest.partition(":")
    if ident and _provider(core, prefix) in _SOURCE_NAMES and typed in ("chat", "channel", "thread", "issue", "repo"):
        block, raw = prefix, ident
    provider = _provider(core, block)
    if provider in ("discord", "telegram"):
        match = re.fullmatch(r"(?:(?P<space>[^:]*[a-zA-Z][^:]*):)?(?P<chat>-?[0-9]{1,20})(?::(?P<thread>[0-9]{1,20}))?", raw)
        if match:
            space, chat, thread = match.groups()
            engine = core.blocks.get(block)
            label = (_telegram_label(engine, space or location.get("space"), chat, thread) if provider == "telegram"
                     else _discord_label(engine, chat, thread))
            return label, provider
    if provider == "slack":
        match = re.fullmatch(r"(?:(?P<space>[^:]+):)?(?P<channel>[CGD][A-Z0-9]+|#[\w.-]+)", raw)
        if match:
            space, channel = match.groups()
            runtime = core.slack.runtimes.get(space or location.get("space")) if core.slack else None
            name = route_audit_name(runtime.channel_names.get(channel)) if runtime else None
            if not name and location.get("channel") == channel:
                name = location.get("channel_label")
            return ("#" + (name or channel).lstrip("#") if name or channel.startswith("#") else
                    "Direct message" if channel.startswith("D") else "Channel"), provider
    if provider == "github" and (match := re.fullmatch(r"([\w.-]+/[\w.-]+)[#:](\d+)", raw)):
        return f"{match[1]}#{match[2]}", provider
    return None


def _conversation_location(core: Any, row: dict[str, Any], sender: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
    meta = row["meta"]
    mode = _object(meta.get("channel_mode")) or _object(payload.get("channel_mode"))
    origin = _reference(sender.get("block") or sender.get("origin") or sender.get("kind") or sender.get("agent"))
    block = _reference(mode.get("block")) or (origin if _provider(core, origin) in _SOURCE_NAMES else row["destination"])
    relay: dict[str, Any] = {}
    slack = core.slack
    if block == "slack" or row["owner"] == "slack" or row["ref"] == "slack":
        relay = _object(meta.get("slack_thread")) or (slack.store.relay(row["handle"]) if slack else None) or {}
        block = "slack"
    elif _provider(core, block) in ("discord", "telegram") or row["owner"] in ("discord", "telegram"):
        if any(b.manifest.id in ("discord", "telegram") for b in core.blocks.values()):
            stored = core.store.one("SELECT block, space, channel, reply_to, root FROM chat_relays WHERE handle = ?", (row["handle"],))
            if stored is not None:
                relay = dict(stored)
                block = relay["block"]
    provider = _provider(core, block)
    if provider not in _SOURCE_NAMES:
        return {}
    direct = block == row["destination"]
    space = _reference(sender.get("workspace") or sender.get("space") or relay.get("workspace") or relay.get("space")
                       or mode.get("space") or (meta.get("workspace") or meta.get("space") if direct else None))
    channel = _reference(mode.get("channel") or sender.get("channel") or relay.get("channel") or (meta.get("channel") if direct else None))
    name = route_audit_name(sender.get("channel_name") or relay.get("channel_name"))
    if direct and provider in ("discord", "telegram"):
        ref_space, sep, ref_channel = row["ref"].partition(":")
        if sep and ref_space and re.fullmatch(r"-?[0-9]+(?::[0-9]+)?", ref_channel):
            space = space or ref_space
            if row.get("target") == row["ref"]:
                channel = channel or ref_channel
    if provider == "github":
        repo = next((s for s in (sender.get("repo"), mode.get("repo"), payload.get("repo"), channel, space,
                                 row["ref"] if direct else None) if isinstance(s, str) and _REPO.fullmatch(s)), None)
        issue = next((str(n) for n in (sender.get("number"), mode.get("number"), payload.get("number"))
                      if re.fullmatch(r"[1-9][0-9]{0,18}", str(n))), None)
        name = f"{repo}#{issue}" if repo and issue else repo or channel
        channel = repo or channel
    elif provider == "slack":
        if slack and space and channel and (rt := slack.runtimes.get(space)):
            name = name or route_audit_name(rt.channel_names.get(channel))
        if channel and channel.startswith("D") and (not name or name == channel):
            name = "Direct message"
        elif name and name != channel:
            name = "#" + name.lstrip("#")
        elif channel:
            name = "Channel" if re.fullmatch(r"[CG][A-Z0-9]+", channel) else "#" + channel.lstrip("#")
    elif provider == "discord":
        parent = _reference(mode.get("parent") or sender.get("channel") or relay.get("channel")) or channel
        discord = core.blocks.get(block)
        root = _reference(relay.get("root"))
        thread = _reference(sender.get("thread")) or (root if discord and root in discord.channels else None)
        if parent != channel:
            thread = thread or channel
        if parent or name:
            name = _discord_label(discord, parent, thread, name)
    elif provider == "telegram":
        chat, _, raw_topic = (channel or "").partition(":")
        telegram = core.blocks.get(block)
        topic = sender.get("topic") or raw_topic
        if telegram and chat:
            if not topic and (message := _reference(sender.get("message") or relay.get("reply_to") or sender.get("root") or relay.get("root"))):
                stored = core.store.one("SELECT topic FROM telegram_topics WHERE block = ? AND chat = ? AND message = ?", (block, chat, message))
                topic = stored["topic"] if stored else None
        if re.fullmatch(r"[1-9][0-9]{0,19}", str(topic)) and str(topic) != "1":
            channel = f"{chat}:{topic}" if chat else channel
        name = _telegram_label(telegram, space, chat, topic, name, sender.get("topic_name"))
    label = name or (space if provider == "telegram" else channel) or channel or space
    return {"source_block": block, "source_kind": provider, **({"space": space} if space else {}),
            **({"channel": channel} if channel else {}), **({"channel_label": label} if label else {})}


def _conversation_subject(row: dict[str, Any], payload: dict[str, Any], location: dict[str, str], item_kind: str,
                          sender_name: str) -> str:
    meta = row["meta"]
    place = location.get("space") or ""
    channel = location.get("channel_label") or ""
    provider = _SOURCE_NAMES.get(location.get("source_kind", ""), "")
    routes = {row["destination"], provider, place, channel}
    if provider:
        routes.update(f"{provider} {label}" for label in (place, channel, f"{place} {channel}") if label.strip())
    if dot := _reference(meta.get("dot")):
        routes.update((dot, f"dot {dot}"))
    if item_kind == "watch_digest":
        fallback = f"Watch digest: {channel}" if channel else "Watch digest"
    elif row["destination"] in ("claude-routine", "grok-routine") or meta.get("routine"):
        fallback = f"Wake: {row['destination']}"
    elif provider:
        fallback = f"{provider} message"
    elif row["destination"] == "mailbox":
        fallback = f"Handoff from {sender_name}"
    else:
        fallback = "Message"
    authored = next((title for value in (meta.get("display_subject"), row["title"], payload.get("subject"))
                     if isinstance(value, str) and (title := display_subject(value, None, "", route_labels=routes))), None)
    body = payload.get("text")
    return authored or display_subject(None, body if isinstance(body, str) else None, fallback, route_labels=routes)


def _conversation(core: Any, row: dict[str, Any], endpoints: tuple[Any, str | None], first: tuple[str, dict[str, Any]],
                  captains: dict[str, dict[str, Any]]) -> dict[str, Any]:
    endpoint_sender, target = endpoints
    item_kind, payload = first
    meta, destination = row["meta"], row["destination"]
    sender = _object(meta.get("display_from")) or _object(meta.get("from")) or _object(meta.get("source"))
    if not sender and (destination == "mailbox" or _provider(core, destination) in _SOURCE_NAMES):
        sender = _object(endpoint_sender)
    if item_kind == "watch_digest":
        sender = _watch_sender(payload, sender)
    location = _conversation_location(core, row, sender, payload)
    address = _channel_target(core, destination, _reference(row.get("target") or target), location)
    if address and not sender and row["owner"].lower() in core.agents:
        sender = {"kind": "agent", "agent": row["owner"]}
    if sender.get("kind") != "watch" and location.get("source_kind") in _CHAT_SENDERS and (
        not sender.get("kind") or (sender.get("kind") == "agent" and sender.get("agent") == location["source_kind"])
    ):
        sender = {**sender, "kind": location["source_kind"]}
        sender.pop("agent", None)
    if not sender:
        agent = row["ref"] if destination == "mailbox" and _reference(row["ref"]) else row["owner"]
        sender = {"kind": "agent", "agent": agent}
    first_sender = _sender(core, sender, destination, captains) or {}
    sender_name = (display_name(first_sender.get("name")) or display_name(first_sender.get("user_name"))
                   or display_name(first_sender.get("author")) or location.get("channel_label")
                   or display_name(first_sender.get("user")) or display_name(first_sender.get("label"))
                   or ("Captain" if first_sender.get("kind") == "captain" else "Capy thread" if first_sender.get("kind") in _CAPY_SENDERS else None)
                   or _SOURCE_NAMES.get(location.get("source_kind", "")) or _agent_name(core, row["owner"]))
    if not sender_name:
        block = core.blocks.get(destination)
        sender_name = block.manifest.title if block else "Unknown sender"
    first_sender["name"] = sender_name
    if first_sender.get("kind") in _CAPY_SENDERS:
        first_sender.setdefault("avatar_url", CAPY_AVATAR_URL)
    first_target, target_kind, avatar_url = _conversation_target(core, row, target, _reference(payload.get("target_kind")), captains)
    if address:
        first_target, target_kind = address
        avatar_url = None
    mode = _object(meta.get("channel_mode")) or _object(payload.get("channel_mode"))
    project = mode["project"] if "project" in mode and (mode["project"] is None or isinstance(mode["project"], str)) else row["project"]
    if project is None and "project" not in mode and _provider(core, destination) in ("telegram", "discord"):
        block = core.blocks.get(destination)
        if block and (space := block.spaces.get(row["ref"].partition(":")[0])):
            project = space.project
    return {**_fields(row, "handle", "owner", "destination", "ref", "target", "open", "status"),
            **location, "project": project, "kind": item_kind or None,
            "title": _conversation_subject(row, payload, location, item_kind, sender_name),
            "created_at": iso(row["created_at"]), "updated_at": iso(row["updated_at"]),
            "first_sender": first_sender, "first_target": first_target, "target_kind": target_kind,
            **({"target_avatar_url": avatar_url} if avatar_url else {}),
            "destination_kind": _provider(core, destination) or destination,
            **({"for_owner": True, "resolved_by": meta["resolved_by"]} if meta.get("for_owner") is True
               and meta.get("resolved_by") in ("active", "project", "global") else {})}


def _owner_mailbox_hidden(row: dict[str, Any]) -> bool:
    return row["destination"] == "mailbox" and (
        row["owner"] == "owner" or (not row["owner"] and row["meta"].get("for_owner") is True)
        or row["meta"].get("owner_archived") is True
    )


async def _conversations(core: Any, rows: dict[str, dict[str, Any]], detail_senders: tuple[Any, ...] = ()) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    handles = list(rows)
    endpoints = core.inbox.first_endpoints(handles)
    first = _inbox_first_items(core, handles)
    destinations = {row["destination"] for row in rows.values()}
    for handle, row in rows.items():
        sender, target = endpoints.get(handle, (None, None))
        _, payload = first.get(handle, ("", {}))
        address = _reference(row.get("target") or target)
        if address and ":captain:" in address:
            destinations.add(address.split(":", 1)[0])
        if (address == "captain" or payload.get("target_kind") == "captain"
                or _provider(core, row["destination"]) != "capy" and any(
                    _object(value).get("kind") in _CAPY_SENDERS
                    for value in (row["meta"].get("display_from"), row["meta"].get("from"), sender, *detail_senders)
                )):
            destinations.update(core.blocks)
    captains = await captain_targets(core, destinations)
    return (
        {handle: _conversation(core, row, endpoints.get(handle, (None, None)), first.get(handle, ("", {})), captains)
         for handle, row in rows.items()},
        captains,
    )


async def _pending_write(core: Any, row: dict[str, Any], agents: dict[str, str]) -> dict[str, Any]:
    persona = (row.get("persona") or {}).get("name")
    requester = row.get("requester") or {}
    key = row["key_id"]
    user = requester.get("user") or key.removeprefix("slack:")
    workspace = requester.get("workspace") or row["workspace"]
    sender = {"kind": "slack"}
    if not requester.get("agent") and re.fullmatch(r"[UW][A-Z0-9]+", user):
        if name := _reference(requester.get("user_name")):
            sender["name"] = name
        if runtime := core.slack.runtimes.get(workspace):
            try:
                async with asyncio.timeout(4):
                    name, image = await runtime.user_profile(user)
                    if name := _reference(name):
                        sender["name"] = name
                    if avatar := await slack_profile_avatar(core, image):
                        sender["avatar_url"] = avatar
            except TimeoutError:
                pass
    elif agent := _reference(requester.get("agent") or agents.get(key) or agents.get(key.removeprefix("dots:"))):
        name = _reference(core.agents.label(agent)) or agent
        sender = {"kind": "agent", "agent": agent, "name": name}
        if avatar := agent_avatar_url(agent) or agent_avatar_url(name):
            sender["avatar_url"] = avatar
    return {"platform": "slack", **_fields(row, "id", "workspace", "identity", "action", "key_id"),
            **_scalars(row["params"], "channel", "thread_ts", "ts", "timestamp", "text", "name"),
            "persona": persona if isinstance(persona, str) else None,
            "sender": sender,
            "created_at": iso(row["created_at"]), "expires_at": iso(row["expires_at"])}


async def inbox(request: Request) -> JSONResponse:
    params = request.query_params
    project = params.get("project")
    if project and not re.fullmatch(_PROJECT, project):
        return _error("invalid project")
    before = None
    try:
        limit = _number(request, "limit", 20, 1, 100)
        filters = InboxFilters.parse(params)
    except ValueError as exc:
        return _error(str(exc))
    if "cursor" in params:
        stamp, _, handle = params["cursor"].partition(":")
        try:
            before = (float(stamp), handle)
        except ValueError:
            return _error("invalid cursor")
        if not math.isfinite(before[0]) or not 0 < len(handle) <= 256:
            return _error("invalid cursor")
    core = request.app.state.core
    slack = core.slack
    pending, pauses = [], []
    if before is None and slack is not None:
        rows = [row for row in slack.store.list_requests(status="pending", limit=50) if row["expires_at"] > slack.store.clock()]
        if rows:
            agents = {agent: agent for agent in core.known_agents()}
            agents.update({key["id"]: key["agent"] for key in core.connections.list(include_revoked=True)})
            pending = await asyncio.gather(*(_pending_write(core, row, agents) for row in rows))
    if before is None:
        pauses = core.store.q("SELECT root, turns, paused_at FROM conversation_turns WHERE paused_at IS NOT NULL ORDER BY paused_at DESC")
    predicates = [("NOT (h.owner = 'owner' AND h.destination = 'mailbox')", []), *filters.clauses(core, project)]
    roots = core.inbox.handles(limit=limit + 1, roots_only=True, before=before, older_first=filters.older_first, predicates=predicates)
    next_cursor = None
    if len(roots) > limit:
        roots.pop()
        last = roots[-1]
        stamp = last["updated_at"] if last["updated_at"] is not None else last["created_at"]
        next_cursor = f"{stamp}:{last['handle']}"
    by_handle = {row["handle"]: row for row in roots}
    visible_pauses = []
    for pause in pauses:
        row = by_handle.get(pause["root"]) or core.inbox.handle(pause["root"])
        if row is not None and not _owner_mailbox_hidden(row):
            visible_pauses.append(pause)
            by_handle[pause["root"]] = row
    conversations, _ = await _conversations(core, by_handle)
    unread = unread_handles(core, list(by_handle))
    for handle, result in conversations.items():
        result["unread"] = handle in unread
    return JSONResponse({"pending_writes": pending, "turn_cap": core.cfg.turn_cap,
                         "pauses": [{**_fields(row, "root", "turns"), "paused_at": iso(row["paused_at"]),
                                     "conversation": conversations.get(row["root"])} for row in visible_pauses],
                         "conversations": [conversations[row["handle"]] for row in roots], "next_cursor": next_cursor})


async def conversation(request: Request) -> JSONResponse:
    try:
        cursor = _number(request, "cursor", 0, 0)
        limit = _number(request, "limit", 100, 1, 200)
    except ValueError as exc:
        return _error(str(exc))
    core = request.app.state.core
    handle = request.path_params["handle"]
    row = core.inbox.handle(handle)
    if row is None or _owner_mailbox_hidden(row):
        return _error("conversation not found", 404)
    row = core.inbox.thread_root(row)
    if _owner_mailbox_hidden(row):
        return _error("conversation not found", 404)
    handle = row["handle"]
    out = core.inbox.read(owner=None, handle=handle, since=cursor, limit=limit, advance=False)
    items = []
    conversations, captains = await _conversations(core, {handle: row}, tuple(item.get("from") for item in out["items"]))
    conversations[handle]["unread"] = bool(unread_handles(core, [handle]))
    for item in out["items"]:
        projected = _scalars(item, "cursor", "handle", "kind", "at", "text", "status", "final", "in_reply_to", "trust", "project",
                             "target", "target_kind", "event_id", "slack_kind", "count")
        sender = item.get("from")
        if item["kind"] == "watch_digest":
            sender = _watch_sender(item, _object(sender))
        if display_sender := _sender(core, sender, row["destination"], captains):
            projected["from"] = display_sender
        items.append(projected)
    return JSONResponse({"conversation": conversations[handle],
                         "items": items, "cursor": out["cursor"]})


async def memory(request: Request) -> JSONResponse:
    core = request.app.state.core
    params = request.query_params
    namespace = params.get("namespace")
    if namespace and not NAMESPACE_RE.fullmatch(namespace):
        return _error("invalid namespace")
    scope, key, ident = params.get("scope"), params.get("key"), params.get("id")
    scopes = [scope] if scope else None
    known_scopes = [GENERAL, *(p.scope for p in core.projects.list(include_archived=True)), *(
        row["scope"] for row in core.store.q("SELECT DISTINCT scope FROM memories WHERE deleted_at IS NULL AND scope LIKE 'agent:%' ORDER BY scope")
    )]
    if scope and scope not in known_scopes:
        return _error("scope not found", 404)
    if "id" in params or "key" in params:
        if not ident and not (scope and namespace and key):
            return _error("provide id or scope, namespace and key")
        try:
            entry = core.memory.read(id=ident, scope=scope, namespace=namespace, key=key, history_limit=200)
        except MemoryError_:
            return _error("memory not found", 404)
        if scope and entry["scope"] != scope:
            return _error("memory not found", 404)
        return JSONResponse({"entry": {**_fields(entry, *_MEMORY_FIELDS, "body"),
                                       "writer": entry["content_by"], "summary": memory_summary(entry["body"]), "history": entry["history"]}})
    try:
        limit = _number(request, "limit", 50, 1, 200)
        offset = _number(request, "offset", 0, 0)
    except ValueError as exc:
        return _error(str(exc))
    result = core.memory.list(scopes=scopes, namespace=namespace, limit=limit, offset=offset, summaries=True)
    summary = result["scopes"]
    for name in scopes or known_scopes:
        summary.setdefault(name, {})
    if query := params.get("q", "").strip():
        if len(query) > 500:
            return _error("invalid search query")
        try:
            result = core.memory.search(query=query, scopes=scopes, namespace=namespace, limit=limit, summaries=True)
        except MemoryError_:
            return _error("query has no searchable words")
    writers = core.memory.latest_writers([row["id"] for row in result["items"]])
    return JSONResponse({"scopes": summary, "items": [{**_fields(row, *_MEMORY_FIELDS, "snippet", "summary"),
                                                       "writer": writers.get(row["id"], row["source"])} for row in result["items"]],
                         "next_offset": result.get("next_offset"), "mirror": bool(core.memory.mirror_dir)})


async def prompts(request: Request) -> JSONResponse:
    try:
        names = sorted(
            path.name.removesuffix(".md") for path in files(_PROMPT_PACKAGE).iterdir()
            if path.name.endswith(".md") and _PROMPT_NAME.fullmatch(path.name.removesuffix(".md")) and path.is_file()
        )
    except OSError:
        return _error("prompts unavailable", 503)
    return JSONResponse({"prompts": names})


async def prompt(request: Request) -> Response:
    name = request.path_params["name"]
    if not _PROMPT_NAME.fullmatch(name):
        return _error("prompt not found", 404)
    try:
        content = files(_PROMPT_PACKAGE).joinpath(f"{name}.md").read_bytes()
    except (FileNotFoundError, IsADirectoryError):
        return _error("prompt not found", 404)
    except OSError:
        return _error("prompt unavailable", 503)
    return Response(content, media_type="text/markdown")


async def platforms(request: Request) -> JSONResponse:
    core = request.app.state.core
    configured = [(name, block.manifest, block) for name, block in core.visible_blocks().items()]
    active_types = {manifest.id for _, manifest, _ in configured}
    available = [(use, load(use).manifest, None) for use in BLOCKS if use not in active_types and use not in ON_DEMAND_BLOCKS]
    items = []
    for name, manifest, block in sorted([*configured, *available], key=lambda item: item[0]):
        online = block is not None and block.online
        status = ("unconfigured" if block is None else "offline" if not online else
                  "degraded" if block.problems() else "online")
        config_keys = set(manifest.config_schema.get("properties", {}))
        if block is not None:
            config_keys.update(core.cfg.blocks.get(name, {}).keys() - {"use"})
        items.append({"name": name, "use": manifest.id, "title": manifest.title, "kind": manifest.kind,
                      "config_keys": sorted(config_keys), "status": status, "online": online})
    return JSONResponse({"platforms": items})


READ_ROUTES = [
    Route("/health", health, methods=["GET"]),
    Route("/routes", routes, methods=["GET"]),
    Route("/visibility", visibility, methods=["GET"]),
    Route("/audit", audit, methods=["GET"]),
    Route("/agents", agents, methods=["GET"]),
    Route("/projects", projects, methods=["GET"]),
    Route("/inbox", inbox, methods=["GET"]),
    Route("/conversations/{handle}", conversation, methods=["GET"]),
    Route("/memory", memory, methods=["GET"]),
    Route("/prompts", prompts, methods=["GET"]),
    Route("/prompts/{name}", prompt, methods=["GET"]),
    Route("/platforms", platforms, methods=["GET"]),
]
