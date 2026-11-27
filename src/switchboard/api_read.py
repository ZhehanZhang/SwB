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

from .api_display import captain_avatar_url, captain_target, captain_targets, capy_label, local_avatar_url, slack_profile_avatar
from .slack.avatar_files import agent_avatar_url
from .audit import iso
from .blocks import BLOCKS, ON_DEMAND_BLOCKS, load
from .memory import GENERAL, NAMESPACE_RE, MemoryError_, summary as memory_summary
from .modes import CHAT_DROP_ACTION, ChatDrop
from .refusals import CSRF_AUDIT_PATHS, OWNER_NOTE_REQUIRED, SIGN_IN_FAILURES
from .routes import route_audit_name
from .targets import SLUG_RE
from .visibility import show

_SQLITE_MAX = 2**63 - 1
_ROUTE_DEFAULTS = ("model", "mode", "delivery")
_ROUTE_KINDS = ("captain", "project", "thread", "routine")
_REPO = re.compile(r"[\w.-]+/[\w.-]+")
_AUTH_PATHS = ("/mcp", "/inbox/poll", "/inbox/events", "/register", "/token", "/connect/redeem", *SIGN_IN_FAILURES, *CSRF_AUDIT_PATHS)
_MEMORY_FIELDS = ("id", "scope", "namespace", "key", "title", "tags", "source", "created_at", "updated_at")
_PROMPT_PACKAGE = "switchboard.prompts.setup"
_PROMPT_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PROJECT = r"[a-z0-9][a-z0-9-]{0,47}"
_SCOPE = rf"(?:general|project:{_PROJECT})"
_CAPY_SENDERS = ("capy_thread", "capy", "captain")
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
        **_fields(report, "status", "mirror", "static_keys", "turn_cap", "mcp_enabled"),
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
                   "thread_id", "thread_ts", "ts", "message_id", "author", "title", "dot")
    avatar = local_avatar_url(core, sender.get("avatar_url"))
    if out.get("kind") in _CAPY_SENDERS:
        upstream = sender.get("sender")
        ident = _reference(upstream.get("id") if isinstance(upstream, dict) else out.get("thread_id"))
        captain = captain_target(ident, destination, captains)
        if captain is None and ident:
            matches = [target for address, target in captains.items() if address.endswith(f":captain:{ident}")]
            captain = matches[0] if len(matches) == 1 else None
        if captain:
            avatar = avatar or captain_avatar_url(core, captain)
            if name := _reference(captain["name"]):
                out["author"] = name
    if not avatar:
        avatar = agent_avatar_url(_reference(out.get("agent")) or _reference(out.get("kind")) or "")
    if avatar:
        out["avatar_url"] = avatar
    return out or None


def _conversation(core: Any, row: dict[str, Any], endpoints: tuple[Any, str | None], captains: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sender, target = endpoints
    meta = row["meta"]
    first_sender = _sender(core, meta.get("from"), row["destination"], captains)
    if first_sender is None:
        first_sender = _sender(core, sender if row["destination"] in ("slack", "mailbox") else
                               {"kind": "agent", "agent": row["owner"]}, row["destination"], captains)
    first_target = row.get("target") or target
    if row.get("target"):
        parts = row["target"].split(":", 2)
        if len(parts) == 3 and isinstance(info := meta.get("target"), dict) and info.get(parts[1]) == parts[2]:
            first_target = _reference(info.get("name")) or first_target
        if len(parts) == 3 and parts[1] == "dot" and isinstance(dot := meta.get("dot"), str) and SLUG_RE.fullmatch(dot):
            first_target = dot
    reference = row.get("target") or first_target or row["ref"]
    first_target = capy_label(reference, row["destination"], captains, name=first_target) or first_target
    captain = captain_target(reference, row["destination"], captains)
    avatar_url = captain_avatar_url(core, captain) if captain else None
    block = core.blocks.get(row["destination"])
    destination_kind = block.manifest.id if block else None
    if not avatar_url:
        if row["target"] and row["target"].startswith("agent:"):
            avatar_url = agent_avatar_url(row["target"].removeprefix("agent:"))
        elif destination_kind:
            avatar_url = agent_avatar_url(destination_kind)
    return {**_fields(row, "handle", "owner", "destination", "ref", "project", "title", "target", "status", "open"),
            "created_at": iso(row["created_at"]), "updated_at": iso(row["updated_at"]),
            "first_sender": first_sender, "first_target": first_target,
            **({"target_avatar_url": avatar_url} if avatar_url else {}),
            "destination_kind": destination_kind}


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
    roots = core.inbox.handles(limit=limit + 1, roots_only=True, project=project, before=before)
    next_cursor = None
    if len(roots) > limit:
        roots.pop()
        last = roots[-1]
        stamp = last["updated_at"] if last["updated_at"] is not None else last["created_at"]
        next_cursor = f"{stamp}:{last['handle']}"
    by_handle = {row["handle"]: row for row in roots}
    for pause in pauses:
        if pause["root"] not in by_handle and (row := core.inbox.handle(pause["root"])) is not None:
            by_handle[pause["root"]] = row
    endpoints = core.inbox.first_endpoints(list(by_handle))
    captains = await captain_targets(core, core.blocks if any(
        isinstance(sender, dict) and sender.get("kind") in _CAPY_SENDERS
        for row in by_handle.values() if row["destination"] == "mailbox"
        for sender in (row["meta"].get("from"), endpoints.get(row["handle"], (None, None))[0])
    ) else (row["destination"] for row in by_handle.values()))
    conversations = {handle: _conversation(core, row, endpoints.get(handle, (None, None)), captains) for handle, row in by_handle.items()}
    return JSONResponse({"pending_writes": pending, "turn_cap": core.cfg.turn_cap,
                         "pauses": [{**_fields(row, "root", "turns"), "paused_at": iso(row["paused_at"]),
                                     "conversation": conversations.get(row["root"])} for row in pauses],
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
    if row is None:
        return _error("conversation not found", 404)
    row = core.inbox.thread_root(row)
    handle = row["handle"]
    out = core.inbox.read(owner=None, handle=handle, since=cursor, limit=limit, advance=False)
    items = []
    endpoints = core.inbox.first_endpoints([handle])
    first_sender = endpoints.get(handle, (None, None))[0]
    captains = await captain_targets(core, core.blocks if row["destination"] == "mailbox" and any(
        isinstance(sender, dict) and sender.get("kind") in _CAPY_SENDERS
        for sender in (row["meta"].get("from"), first_sender, *(item.get("from") for item in out["items"]))
    ) else (row["destination"],))
    for item in out["items"]:
        projected = _scalars(item, "cursor", "handle", "kind", "at", "text", "status", "final", "in_reply_to", "trust", "project",
                             "target", "target_kind", "event_id", "slack_kind", "count")
        if sender := _sender(core, item.get("from"), row["destination"], captains):
            projected["from"] = sender
        items.append(projected)
    return JSONResponse({"conversation": _conversation(core, row, endpoints.get(handle, (None, None)), captains),
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
            entry = core.memory.read(id=ident, scope=scope, namespace=namespace, key=key)
        except MemoryError_:
            return _error("memory not found", 404)
        if scope and entry["scope"] != scope:
            return _error("memory not found", 404)
        writer = core.memory.latest_writers([entry["id"]]).get(entry["id"], entry["source"])
        return JSONResponse({"entry": {**_fields(entry, *_MEMORY_FIELDS, "body"), "writer": writer, "summary": memory_summary(entry["body"]),
                                       "history": [_scalars(r, "at", "source", "action") for r in entry["history"]]}})
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
