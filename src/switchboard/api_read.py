"""Owner console reads, mounted behind the app's Cloudflare Access check."""

from __future__ import annotations

import json
import re
from importlib.resources import files
from ipaddress import ip_address
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .audit import iso
from .blocks import BLOCKS, load
from .memory import GENERAL, NAMESPACE_RE, MemoryError_
from .targets import SLUG_RE
from .visibility import show

_SQLITE_MAX = 2**63 - 1
_ROUTE_DEFAULTS = ("model", "mode", "delivery")
_ROUTE_KINDS = ("captain", "project", "thread", "routine")
_REPO = re.compile(r"[\w.-]+/[\w.-]+")
_AUTH_PATHS = ("/mcp", "/inbox/poll", "/inbox/events", "/register", "/authorize", "/token", "/connect/redeem")
_MEMORY_FIELDS = ("id", "scope", "namespace", "key", "title", "tags", "source", "created_at", "updated_at")
_PROMPTS = {"coordinator": "v4", "routine": "v1", "dispatcher": "v1"}


def _fields(row: Any, *names: str) -> dict[str, Any]:
    return {key: row[key] for key in names if key in row.keys()}


def _scalars(row: dict[str, Any], *names: str) -> dict[str, Any]:
    return {k: v for k, v in _fields(row, *names).items() if v is None or isinstance(v, (str, int, float))}


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
    # Provider diagnostics can include upstream errors and URLs. Show which block needs attention, not its raw error.
    problems += [f"{name}: check block status" for name, block in core.blocks.items() if block.problems()]
    revision = core.cfg.env.get("SWITCHBOARD_REVISION", "")
    return JSONResponse({
        **_fields(report, "status", "mirror", "static_keys", "turn_cap"),
        **({"commit": revision.lower()} if re.fullmatch(r"[0-9a-fA-F]{40}", revision) else {}),
        "blocks": {name: _fields(row, "use", "kind", "level", "online") for name, row in report["blocks"].items()},
        "problems": problems,
    })


async def routes(request: Request) -> JSONResponse:
    core = request.app.state.core
    items = []
    for route in core.routes.list():
        block = core.blocks.get(route.destination)
        kinds = tuple(t.kind for t in block.manifest.targets) if block else _ROUTE_KINDS
        kind, ident = next(((k, ident) for k in kinds if (ident := _reference(route.target.get(k)))), (None, None))
        name = _reference(route.target.get("name")) or ident if ident else None
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
    resources = [f"target:{slug}" for target in await core.targets() for slug in target["slugs"] if SLUG_RE.fullmatch(slug)]
    resources += [f"route:{route.id}" for route in core.routes.list()]
    resources += [f"memory:{GENERAL}", *(f"memory:{p.scope}" for p in core.projects.list(include_archived=True))]
    if core.slack is not None:
        for label, workspace in core.slack.config.workspaces.items():
            resources += [f"channel:{label}/{channel}" for channel in (*workspace.watched, *workspace.listen_channels)
                          if re.fullmatch(r"#?[\w.-]+", channel)]
    return JSONResponse({"resources": core.visibility.rows(resources), "defaults": {k: show(v) for k, v in core.visibility.defaults.items()}})


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
    items = []
    for row in rows:
        fields = _fields(row, "id", "origin", "source", "client", "destination", "action", "outcome", "target", "ref")
        if fields["action"] in ("auth_failure", "auth_missing", "oauth_refused", "rate_limited"):
            if fields["target"] not in _AUTH_PATHS:
                fields["target"] = None
            fields["ref"] = None
            try:
                fields["client"] = str(ip_address(fields["client"])) if "%" not in fields["client"] else None
            except ValueError:
                fields["client"] = None
        items.append({**fields, "at": iso(row["at"])})
    return JSONResponse({"items": items, "next_cursor": next_cursor})


async def agents(request: Request) -> JSONResponse:
    core = request.app.state.core
    targets = await core.targets()
    routines = []
    for block in core.blocks.values():
        if block.manifest.id == "claude-routine":
            routines.extend({"block": block.name, **_fields(r, "name", "kind", "agent", "project", "routine_id", "claude_project",
                                                           "slugs", "source", "status", "primary", "visibility")} for r in block.rows())
        elif block.manifest.id == "grok-routine":
            slugs = {t["id"]: t["slugs"] for t in targets if t["block"] == block.name}
            routines.extend({"block": block.name, "kind": "project" if r["project"] else "general", "agent": "grok",
                             "routine_id": None, "slugs": slugs.get(r["name"], []),
                             "visibility": show(core.visibility.rule(f"target:{r['name']}")),
                             **_fields(r, "name", "project", "source", "status", "primary")} for r in block.status()["routines"])
    claims = []
    claude = next((block for block in core.blocks.values() if block.manifest.id == "claude-routine"), None)
    for row in reversed(claude.pending(details=True)) if claude else ():
        new_project = row["new_project"]
        claims.append({**_fields(row, "name", "routine_id", "project", "claude_project", "primary"), "created_by": row["requested_by"],
                       "created_at": iso(row["created_at"]), "expires_at": iso(row["expires_at"]),
                       "new_project": _scalars(new_project, "name", "description") if isinstance(new_project, dict) else None})
    keys = [{**_fields(k, "id", "agent", "project", "client"), "created_at": iso(k["created_at"]), "last_used_at": iso(k["last_used_at"])}
            for k in core.connections.list()]
    discovered: dict[str, list[dict[str, Any]]] = {"captain": [], "dot": []}
    for target in targets:
        if target["kind"] in discovered:
            slugs = [s for s in target["slugs"] if SLUG_RE.fullmatch(s)]
            discovered[target["kind"]].append({
                **_fields(target, "id", "name", "kind", "block", "address", "slugs", "level", "project"),
                "online": core.blocks[target["block"]].online,
                "visibility": show(core.visibility.rule(*(f"target:{s}" for s in slugs))) if slugs else show(core.visibility.defaults["target"]),
            })
    return JSONResponse({"routines": routines, "keys": keys, "pending_claims": claims, "captains": discovered["captain"], "dots": discovered["dot"],
                         "static_keys": [k.name for k in core.cfg.static_keys]})


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


def _conversation(row: dict[str, Any]) -> dict[str, Any]:
    return {**_fields(row, "handle", "owner", "destination", "ref", "project", "title", "target", "status", "open"),
            "created_at": iso(row["created_at"]), "updated_at": iso(row["updated_at"])}


def _pending_write(row: dict[str, Any]) -> dict[str, Any]:
    persona = (row.get("persona") or {}).get("name")
    return {**_fields(row, "id", "workspace", "identity", "action", "key_id"),
            **_scalars(row["params"], "channel", "thread_ts", "ts", "timestamp", "text", "name"),
            "persona": persona if isinstance(persona, str) else None,
            "created_at": iso(row["created_at"]), "expires_at": iso(row["expires_at"])}


async def inbox(request: Request) -> JSONResponse:
    core = request.app.state.core
    slack = core.slack
    pending = [_pending_write(row) for row in slack.store.list_requests(status="pending", limit=50)
               if row["expires_at"] > slack.store.clock()] if slack is not None else []
    pauses = core.store.q("SELECT root, turns, paused_at FROM conversation_turns WHERE paused_at IS NOT NULL ORDER BY paused_at DESC")
    return JSONResponse({"pending_writes": pending, "turn_cap": core.cfg.turn_cap,
                         "pauses": [{**_fields(row, "root", "turns"), "paused_at": iso(row["paused_at"])} for row in pauses],
                         "conversations": [_conversation(row) for row in core.inbox.handles(limit=100)]})


async def conversation(request: Request) -> JSONResponse:
    try:
        cursor = _number(request, "cursor", 0, 0)
        limit = _number(request, "limit", 100, 1, 200)
    except ValueError as exc:
        return _error(str(exc))
    core = request.app.state.core
    handle = request.path_params["handle"]
    row = core.inbox.handle(handle)
    if row is None:
        return _error("conversation not found", 404)
    out = core.inbox.read(owner=None, handle=handle, since=cursor, limit=limit, advance=False)
    items = []
    for item in out["items"]:
        projected = _scalars(item, "cursor", "kind", "at", "text", "status", "final", "in_reply_to", "trust", "project",
                             "target", "target_kind", "event_id", "slack_kind")
        sender = item.get("from")
        if isinstance(sender, dict):
            projected["from"] = _scalars(sender, "kind", "name", "agent", "trust", "workspace", "channel", "channel_name", "user",
                                         "user_name", "thread_id", "thread_ts", "ts", "message_id", "author", "title", "dot")
        items.append(projected)
    return JSONResponse({"conversation": _conversation(row), "items": items, "cursor": out["cursor"]})


async def memory(request: Request) -> JSONResponse:
    core = request.app.state.core
    params = request.query_params
    namespace = params.get("namespace")
    if namespace and not NAMESPACE_RE.fullmatch(namespace):
        return _error("invalid namespace")
    scope, key, ident = params.get("scope"), params.get("key"), params.get("id")
    scopes = [scope] if scope else None
    known_scopes = [GENERAL, *(p.scope for p in core.projects.list(include_archived=True))]
    if scope and scope not in known_scopes:
        return _error("scope not found", 404)
    if "id" in params or "key" in params:
        if not ident and not (scope and namespace and key):
            return _error("provide id or scope, namespace and key")
        try:
            entry = core.memory.read(id=ident, scope=scope, namespace=namespace, key=key)
        except MemoryError_:
            return _error("memory not found", 404)
        if scope and entry["scope"] != scope:
            return _error("memory not found", 404)
        return JSONResponse({"entry": {**_fields(entry, *_MEMORY_FIELDS, "body"),
                                       "history": [_scalars(r, "at", "source", "action") for r in entry["history"]]}})
    try:
        limit = _number(request, "limit", 50, 1, 200)
        offset = _number(request, "offset", 0, 0)
    except ValueError as exc:
        return _error(str(exc))
    result = core.memory.list(scopes=scopes, namespace=namespace, limit=limit, offset=offset)
    summary = result["scopes"]
    for name in scopes or known_scopes:
        summary.setdefault(name, {})
    if query := params.get("q", "").strip():
        if len(query) > 500:
            return _error("invalid search query")
        try:
            result = core.memory.search(query=query, scopes=scopes, namespace=namespace, limit=limit)
        except MemoryError_:
            return _error("query has no searchable words")
    return JSONResponse({"scopes": summary, "items": [_fields(row, *_MEMORY_FIELDS, "snippet") for row in result["items"]],
                         "next_offset": result.get("next_offset"), "mirror": bool(core.memory.mirror_dir)})


async def prompts(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    template = "coordinator" if name == "universal-coordinator" else name
    version = _PROMPTS.get(template)
    if version is None:
        return _error("prompt not found", 404)
    project = request.query_params.get("claude_project", "").strip()
    if len(project) > 120 or any(ord(c) < 32 or ord(c) == 127 for c in project):
        return _error("invalid Claude Project name")
    title = json.dumps(project or "<exact Claude Project name>", ensure_ascii=False)
    try:
        text = files("switchboard").joinpath("prompts", f"{template}-{version}.txt").read_text(encoding="utf-8")
    except OSError:
        return _error("prompt unavailable", 503)
    text = text.rstrip().replace("{claude_project}", title)
    return JSONResponse({"name": name, "version": version, "text": text})


async def platforms(request: Request) -> JSONResponse:
    core = request.app.state.core
    configured = [(name, block.manifest, block) for name, block in core.blocks.items()]
    active_types = {manifest.id for _, manifest, _ in configured}
    available = [(use, load(use).manifest, None) for use in BLOCKS if use not in active_types]
    items = []
    for name, manifest, block in sorted([*configured, *available], key=lambda item: item[0]):
        online = block is not None and block.online
        status = ("unconfigured" if block is None else "offline" if not online else
                  "degraded" if block.problems() else "online")
        config_keys = set(manifest.config_schema.get("properties", {}))
        if block is not None:
            config_keys.update(core.cfg.blocks[name].keys() - {"use"})
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
    Route("/prompts/{name}", prompts, methods=["GET"]),
    Route("/platforms", platforms, methods=["GET"]),
]
