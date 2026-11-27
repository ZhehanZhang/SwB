"""Project placements and their provenance for the owner console."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .api_display import captain_avatar_url, captain_target, capy_label
from .slack.avatar_files import agent_avatar_url
from .api_read import _REPO, _error, _reference
from .api_runtime import runtime_info
from .audit import iso
from .projects import Project, ProjectError
from .targets import SLUG_RE

if TYPE_CHECKING:
    from .core import Switchboard


def _item(id: str, label: str, kind: str, *, short_label: str | None = None, detail: str | None = None,
          avatar_url: str | None = None) -> dict[str, Any]:
    avatar_url = avatar_url or agent_avatar_url(kind)
    return {"id": id, "label": label, "kind": kind, "short_label": short_label, "detail": detail,
            **({"avatar_url": avatar_url} if avatar_url else {})}


def _agent(core: Switchboard, name: str) -> dict[str, Any]:
    return _item(f"agent:{name}", core.agents.label(name, "Slack" if name == "slack" else None), name)


def _actor(core: Switchboard, source: str | None) -> dict[str, Any] | None:
    if not source or source == "-":
        return None
    if source == "config":
        return _item("config", "Config", "config")
    if source == "owner" or source.startswith(("admin:", "owner:")):
        return _item("owner", "Owner", "owner")
    _, sep, name = source.partition(":")
    source = name if sep and SLUG_RE.fullmatch(name) else source if not sep else source.split(":", 1)[0]
    return _agent(core, source) if SLUG_RE.fullmatch(source) else None


def _channel(block: str, workspace: str, channel: str, name: str | None = None) -> dict[str, Any]:
    ident = channel.removeprefix("#")
    label = f"#{name}" if name else ident if re.fullmatch(r"[CGD][A-Z0-9]+", ident) else f"#{ident}"
    return _item(f"{block}:{workspace}:{ident}", label, "slack", detail=workspace)


def _slack_placement(block: Any, workspace: str, channel: str) -> tuple[str | None, dict[str, Any]]:
    rt = block.engine.runtimes.get(workspace)
    ref = channel.removeprefix("#")
    ident = rt.channel_ids.get(ref, ref) if rt else ref
    name = rt.channel_names.get(ident) if rt else None
    mapped = block.channel_projects.get(workspace, {})
    scope = mapped.get(ident, mapped.get(name or ref, block.projects.get(workspace)))
    slug = scope.strip().lower().removeprefix("project:") if scope else None
    return slug, _channel(block.name, workspace, ident, name)


def _sources(core: Switchboard, projects: dict[str, Project]) -> dict[str | None, list[dict[str, Any]]]:
    out: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    repos = {slug: {r for r in p.links.get("repos", []) if _REPO.fullmatch(r)} for slug, p in projects.items()}
    for block in core.blocks.values():
        if block.manifest.id == "slack" and block.engine is not None:
            for workspace, cfg in block.engine.config.workspaces.items():
                rt = block.engine.runtimes.get(workspace)
                mapped = block.channel_projects.get(workspace, {})
                default = block.projects.get(workspace)
                refs = {*mapped, *cfg.watched, *cfg.listen_channels}
                if rt:
                    refs.update(rt.watched_ids | rt.listen_ids)
                channels: dict[str, tuple[str | None, dict[str, Any]]] = {}
                for ref in sorted(refs):
                    if not re.fullmatch(r"#?[\w.-]{1,80}", ref):
                        continue
                    slug, item = _slack_placement(block, workspace, ref)
                    channels[item["id"]] = (slug, item)
                for slug, item in channels.values():
                    out[slug if slug in projects else None].append(item)
                if cfg.has_bot and cfg.intake == "socket":
                    slug = default.strip().lower().removeprefix("project:") if default else None
                    out[slug if slug in projects else None].append(_item(f"{block.name}:{workspace}:dm", "direct messages", "slack",
                                                                        short_label="DMs", detail=workspace))
        elif block.manifest.id == "github":
            used = set()
            matched_patterns = set()
            for slug, links in repos.items():
                found = sorted(r for r in links if block.repo_routes.get(r) or block.repo_routes.get(f"{r.split('/')[0]}/*"))
                if found:
                    used.update(found)
                    matched_patterns.update(f"{r.split('/')[0]}/*" for r in found)
                    out[slug].append(_item(block.name, f"{len(found)} {'repo' if len(found) == 1 else 'repos'}", "github",
                                           detail=", ".join(found)))
            for repo, sinks in sorted(block.repo_routes.items()):
                if sinks and repo not in used and repo not in matched_patterns and (
                    _REPO.fullmatch(repo) or re.fullmatch(r"[\w.-]+/\*", repo)
                ):
                    out[None].append(_item(f"{block.name}:{repo}", repo, "github"))
    return {slug: sorted(items, key=lambda item: item["kind"] == "github") for slug, items in out.items()}


def _routine_state(status: str) -> str:
    if status.startswith("failed"):
        return "fault"
    return "on" if status.startswith("tested") else "off"


def _placements(core: Switchboard, targets: dict[str, dict[str, Any]]) -> tuple[dict, dict, dict]:
    agents: dict[str | None, dict[str, dict[str, Any]]] = defaultdict(dict)
    routes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    routines = {}
    connected = set()
    for block in core.blocks.values():
        use = block.manifest.id
        if use not in ("claude-routine", "grok-routine"):
            continue
        if use == "claude-routine":
            rows = block.rows()
            stored = {r["name"]: r for r in core.store.q("SELECT name, created_at, created_by FROM routines")}
        else:
            rows = block.status()["routines"]
            stored = {r["name"]: r for r in core.store.q("SELECT name, created_at FROM grok_routines WHERE block = ?", (block.name,))}
        wake_targets = {
            (agent, project): block.wake_target(agent, project)
            for agent, project in {(row.get("agent", "grok"), row["project"]) for row in rows if row["project"]}
        }
        for row in rows:
            name = row["name"]
            ident = f"{block.name}:routine:{name}"
            meta = dict(stored.get(name, {})) if row["source"] == "stored" else {}
            primary = wake_targets.get((row.get("agent", "grok"), row["project"]))
            role = "primary routine" if primary and str(primary) == ident else "routine"
            item = {**_item(ident, name, use if use == "grok-routine" else row.get("agent", "claude")),
                    "role": role, "state": _routine_state(row["status"]),
                    "by": _actor(core, meta.get("created_by") or ("config" if row["source"] == "config" else None)),
                    "at": iso(meta.get("created_at"))}
            agents[row["project"]][ident] = item
            routines[ident] = (item, row)
    for route in core.routes.list():
        block = core.blocks.get(route.destination)
        addr = core.address_for_route(route)
        if addr:
            connected.add(str(addr))
        if not route.project:
            continue
        ident = str(addr) if addr else route.destination
        target = targets.get(ident, {})
        use = block.manifest.id if block else route.destination
        by = _actor(core, route.updated_by)
        state = ("on" if target else "off") if route.destination == "agent" else "off" if not block or not block.online else "on" if target else None
        if ident in routines:
            item = routines[ident][0]
            state = item["state"]
        else:
            label = _reference(target.get("name")) or _reference(route.target.get("name")) or (_reference(addr.id) if addr else None)
            label = capy_label(ident, route.destination, targets, name=label) or label
            item = {**_item(ident, label or (block.manifest.title if block else route.destination),
                            use, avatar_url=captain_avatar_url(core, target)),
                    "role": addr.kind if addr else "destination", "state": state, "by": by, "at": route.updated_at}
        agents[route.project].setdefault(ident, item)
        routes[route.project].append({"id": str(route.id), "from": _agent(core, route.agent) if route.agent else _item("any-agent", "Any agent", "agent"),
                                      "to": item, "direction": "route", "by": by, "at": route.updated_at, "state": state,
                                      "delivery": _reference(route.defaults.get("delivery"))})
    for key in core.connections.list():
        item = _agent(core, key["agent"])
        agents[key["project"]].setdefault(item["id"], {
            **item, "role": "MCP client", "state": None, "by": _actor(core, key["created_by"]), "at": iso(key["created_at"]),
        })
    for address, target in targets.items():
        kind = target.get("kind")
        if address in connected or kind not in ("captain", "dot"):
            continue
        if label := capy_label(address, target["block"], targets) or _reference(target.get("name")) or _reference(target.get("id")):
            agents[None][address] = _item(address, label, "capy" if kind == "captain" else "chatgpt",
                                         avatar_url=captain_avatar_url(core, target))
    return agents, routes, routines


def _slack_routes(core: Switchboard, slug: str, placements: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    routes = {}
    routine = next((a for b in core.blocks.values() if b.wakes_agents and (a := b.wake_target("claude", slug))), None)
    handles = None
    for block in core.blocks.values():
        if block.manifest.id != "slack" or block.engine is None:
            continue
        if routine and (destination := placements.get(str(routine))):
            for workspace, settings in block.engine.config.workspaces.items():
                for channel in settings.listen_channels:
                    mapped, source = _slack_placement(block, workspace, channel)
                    if mapped != slug:
                        continue
                    ident = f"listen:{source['id']}:{destination['id']}"
                    routes[ident] = {"id": ident, "from": source, "to": destination, "direction": "route",
                                     "by": _actor(core, "config"), "at": None, "state": destination["state"],
                                     "delivery": "Slack listen mode"}
        if handles is None:
            handles = core.inbox.handles(project=slug, open_only=True, limit=20)
        for handle in handles:
            relay = handle["meta"].get("slack_thread") or block.engine.store.relay(handle["handle"])
            if not isinstance(relay, dict) or not (destination := placements.get(handle["target"])):
                continue
            workspace, channel = relay.get("workspace"), relay.get("channel")
            if (not isinstance(workspace, str) or workspace not in block.engine.config.workspaces
                    or not isinstance(channel, str) or not re.fullmatch(r"[CGD][A-Z0-9]+", channel)):
                continue
            _, source = _slack_placement(block, workspace, channel)
            ident = f"reply:{destination['id']}:{source['id']}"
            routes.setdefault(ident, {"id": ident, "from": destination, "to": source, "direction": "reply",
                                      "by": None, "at": iso(handle["created_at"]), "state": None,
                                      "delivery": "Slack thread reply"})
    return list(routes.values())


def _project_facts(core: Switchboard, project: Project, *, memory_count: int, open_count: int) -> dict[str, Any]:
    return {"slug": project.slug, "name": project.name, "scope": project.scope, "archived": project.archived,
            "created_at": project.created_at, "turn_cap": core.cfg.turn_cap, "memory_count": memory_count,
            "open_count": open_count}


async def board(request: Request) -> JSONResponse:
    core = request.app.state.core
    targets = {t["address"]: t for t in await core.targets()}
    projects = {p.slug: p for p in core.projects.list()}
    sources = _sources(core, projects)
    agents, _, _ = _placements(core, targets)
    memory = {r["scope"]: r["n"] for r in core.store.q("SELECT scope, COUNT(*) AS n FROM memories WHERE deleted_at IS NULL GROUP BY scope")}
    opened = {r["project"]: r["n"] for r in core.store.q("SELECT project, COUNT(*) AS n FROM handles WHERE open = 1 GROUP BY project")}
    return JSONResponse({"runtime": runtime_info(core), "projects": [
        {**_project_facts(core, p, memory_count=memory.get(p.scope, 0), open_count=opened.get(slug, 0)),
         "sources": sources.get(slug, []), "agents": list(agents[slug].values())} for slug, p in projects.items()
    ], "unassigned": [*sources.get(None, []), *agents[None].values()]})


def _conversations(core: Switchboard, slug: str, targets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = core.store.q(
        "SELECT handle, owner, destination, ref, title, target, COALESCE(updated_at, created_at) AS at "
        "FROM handles WHERE project = ? AND open = 1 ORDER BY at DESC, handle LIMIT 5", (slug,),
    )
    out = []
    for row in rows:
        block = core.blocks.get(row["destination"])
        ref = row["target"] or (row["ref"] if block and block.manifest.id == "capy" else None)
        target = targets.get(ref, {})
        if ref and (label := capy_label(ref, row["destination"], targets) or _reference(target.get("name"))):
            captain = captain_target(ref, row["destination"], targets)
            endpoint = _item(ref, label, block.manifest.id if block else target.get("block", row["destination"]),
                             avatar_url=captain_avatar_url(core, captain) if captain else None)
        else:
            endpoint = _agent(core, row["owner"])
        out.append({"handle": row["handle"], "endpoint": endpoint, "title": row["title"], "at": iso(row["at"])})
    return out


def _history(core: Switchboard, slug: str) -> list[dict[str, Any]]:
    rows = core.store.q(
        "SELECT a.id, a.at, a.action, a.source, a.destination, a.outcome FROM audit a "
        "WHERE (a.action IN ('route_set','route_remove') AND instr(a.target, '/') > 0 AND substr(a.target, instr(a.target, '/') + 1) = ?) "
        "OR EXISTS (SELECT 1 FROM handles h WHERE h.handle = a.ref AND h.project = ?) "
        "OR (a.action = 'connect_redeem' AND EXISTS (SELECT 1 FROM agent_keys k WHERE k.id = a.target AND k.project = ?)) "
        "ORDER BY a.at DESC, a.id DESC LIMIT 5", (slug, slug, slug),
    )
    return [{"id": r["id"], "at": iso(r["at"]), "action": r["action"], "by": _actor(core, r["source"]),
             "destination": block.manifest.title if (block := core.blocks.get(r["destination"])) else r["destination"],
             "outcome": r["outcome"]} for r in rows]


async def project_board(request: Request) -> JSONResponse:
    core = request.app.state.core
    slug = request.path_params["slug"]
    try:
        if not SLUG_RE.fullmatch(slug):
            raise ProjectError()
        project = core.projects.get(slug)
    except ProjectError:
        return _error("project not found", 404)
    targets = {t["address"]: t for t in await core.targets()}
    agents, routes, routines = _placements(core, targets)
    memory = core.memory.list(scopes=[project.scope], limit=4)
    count = core.store.one("SELECT COUNT(*) AS n FROM handles WHERE project = ? AND open = 1", (slug,))["n"]
    claude_project = _reference(project.links.get("claude_project"))
    if not claude_project:
        claude_project = next((name for item, row in routines.values() if row["project"] == slug and item["role"] == "primary routine"
                               and (name := _reference(row.get("claude_project")))), None)
    # The coordinator endpoint accepts only printable titles up to 120 characters.
    if claude_project and (len(claude_project) > 120 or any(ord(c) < 32 or ord(c) == 127 for c in claude_project)):
        claude_project = None
    return JSONResponse({
        "project": _project_facts(core, project, memory_count=sum(memory["scopes"].get(project.scope, {}).values()), open_count=count),
        "claude_project": claude_project, "routes": [*routes[slug], *_slack_routes(core, slug, agents[slug])],
        "conversations": _conversations(core, slug, targets), "history": _history(core, slug),
        "memory": [{**{k: row[k] for k in ("id", "key", "title", "updated_at")}, "by": _actor(core, row["source"])} for row in memory["items"]],
    })


BOARD_ROUTES = [
    Route("/board", board, methods=["GET"]),
    Route("/projects/{slug}", project_board, methods=["GET"]),
    Route("/board/projects/{slug}", project_board, methods=["GET"]),
]
