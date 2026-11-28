"""Additional owner console detail and write routes."""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .api_read import _agents, _audit_item
from .api_write import _Body, _body, _error, _owner
from .blocks.mcp.coding import GUIDES, coding_snippet
from .blocks.mcp.setup import CHATGPT_PLUGINS_URL, hosted_client_instruction
from .memory import GENERAL, MemoryError_, MemoryNotFound

_INVENTORY = {"routine": "routines", "captain": "captains", "dot": "dots", "key": "keys"}


async def agent_detail(request: Request) -> JSONResponse:
    core = request.app.state.core
    path_ident = request.path_params.get("agent")
    kind = request.query_params.get("kind")
    ident = path_ident or request.query_params.get("id")
    block = request.query_params.get("block")
    if (kind not in _INVENTORY and kind != "mailbox" and (kind is not None or path_ident is None)) or not ident or len(ident) > 200:
        return _error("invalid_agent")
    inventory = await _agents(core)
    matches = [(group, row) for group, collection in _INVENTORY.items() if kind is None or group == kind
               for row in inventory[collection] if not block or row.get("block") == block
               if ident == row["name" if group == "routine" else "id"]
               or path_ident is not None and ident in (row.get("name"), row.get("routine_id"), *row.get("slugs", []))]
    if not matches and kind in (None, "mailbox") and not block and (
        ident in inventory["static_keys"] or any(row["agent"] == ident for row in inventory["keys"])
    ):
        matches = [("mailbox", {"id": ident, "name": ident, "agent": ident})]
    if not matches:
        return _error("agent_not_found", 404)
    if len(matches) > 1:
        return _error("ambiguous_agent", 409)
    kind, agent = matches[0]
    project = agent.get("project")
    identities = defaultdict(set)

    if kind == "mailbox":
        routines = [row for row in inventory["routines"] if row["agent"] == ident]
        keys = [row for row in inventory["keys"] if row["agent"] == ident]
    elif kind in ("routine", "key"):
        routines = [row for row in inventory["routines"] if row["agent"] == agent["agent"]
                    and (row["project"] == project or kind == "key" and project is None)]
        keys = [row for row in inventory["keys"] if row["agent"] == agent["agent"] and row["project"] in (None, project)]
    else:
        names = {agent["id"], *agent["slugs"]}
        projects = set(agent["projects"])
        routines = [row for row in inventory["routines"] if row["project"] in projects]
        keys = [row for row in inventory["keys"] if row["project"] in projects]
        identities[agent["block"]].update(names | {agent["address"], *(f"{kind}:{name}" for name in names)})
        if kind == "captain":
            identities[agent["block"]].update((f"captain {agent['id']}", f"captain {agent['name']} ({agent['id']})"))

    for row in routines:
        names = {row["name"], *row["slugs"]}
        if row["routine_id"]:
            names.add(row["routine_id"])
        identities[row["block"]].update(names | {f"routine:{name}" for name in names})
    entries = core.audit.related(targets={name: sorted(values) for name, values in identities.items()},
                                 keys=[key["id"] for key in keys])
    captains = {row["address"]: row for row in inventory["captains"]}
    return JSONResponse({"kind": kind, "agent": agent, "routines": routines, "keys": keys,
                         "audit": [_audit_item(core, row, captains) for row in entries]})


def _mcp_url(value: str | None) -> str | None:
    if not value or any(ch.isspace() or ord(ch) < 32 or ch in "\\?#" for ch in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme in ("https", "http") and url.hostname and url.username is None and url.port != 0:
            return value
    except ValueError:
        pass
    return None


async def agent_setup(request: Request) -> JSONResponse:
    core = request.app.state.core
    connector = _mcp_url(core.cfg.resource)
    url = connector or _mcp_url(f"{core.base_url}/mcp") or "<public_url>/mcp"
    oauth = bool(connector and core.cfg.owner_sign_in)
    prompt = (
        f"Connect to SwB over Streamable HTTP at {url}. Add this URL as an MCP server in your client if needed.\n\n"
        'Once connected, call connect(what="mcp-client") for the next setup step. If you know your platform, use its matching connect kind instead. '
        'Follow the steps the tool returns. Keep credentials out of chat and tool arguments; if a token is needed, use the owner link the tool provides.'
    )
    public = urlsplit(_mcp_url(core.cfg.public_url) or "")
    return JSONResponse({
        "public_origin": f"{public.scheme}://{public.netloc}" if public.netloc else None,
        "chatgpt": {"connector_url": connector, "plugins_url": CHATGPT_PLUGINS_URL,
                    "instruction": hosted_client_instruction("chatgpt", connector)},
        "any": {"prompt": prompt},
        "coding_agents": [coding_snippet(agent, url, oauth) for agent in GUIDES],
    })


async def resume_conversation(request: Request) -> JSONResponse:
    root = request.path_params["root"]
    if not request.app.state.core.resume_conversation(_owner(request), root):
        return _error("pause_not_found", 404)
    return JSONResponse({"root": root, "resumed": True})


class _MoveBody(_Body):
    to_scope: str


class _EditMemoryBody(_Body):
    title: str
    body: str
    tags: list[str] | None = None


class _RestoreMemoryBody(_Body):
    rev: int


class _RenameMemoryBody(_Body):
    path: str


class _MemorySettingsBody(_Body):
    sync: bool


async def memory_settings(request: Request) -> JSONResponse:
    core = request.app.state.core
    if request.method == "GET":
        return JSONResponse(core.memory_settings())
    body = await _body(request, _MemorySettingsBody)
    if body is None:
        return _error("invalid_request")
    return JSONResponse(core.set_memory_setting(_owner(request), body.sync))


async def mutate_memory(request: Request) -> JSONResponse:
    ident = request.path_params["id"]
    core, owner = request.app.state.core, _owner(request)
    verb = request.path_params.get("verb")
    try:
        result: dict[str, Any]
        if request.method == "DELETE":
            result = core.delete_memory(owner, ident)
        elif verb == "move":
            body = await _body(request, _MoveBody)
            if body is None:
                return _error("invalid_request")
            result = core.move_memory(owner, ident, body.to_scope)
        elif verb == "promote":
            result = core.move_memory(owner, ident, GENERAL, promote=True)
        elif verb == "edit":
            body = await _body(request, _EditMemoryBody)
            if body is None:
                return _error("invalid_request")
            result = core.edit_memory(owner, ident, body.title, body.body, body.tags)
        elif verb == "restore":
            body = await _body(request, _RestoreMemoryBody)
            if body is None:
                return _error("invalid_request")
            result = core.restore_memory(owner, body.rev, id=ident)
        elif verb == "rename":
            body = await _body(request, _RenameMemoryBody)
            if body is None:
                return _error("invalid_request")
            result = {"renamed": ident, "message": core.rename_memory(owner, body.path, id=ident)}
        else:
            return _error("not_found", 404)
    except MemoryNotFound:
        return _error("memory_not_found", 404)
    except MemoryError_:
        return _error("memory_not_changed")
    return JSONResponse(result)


PAGE_ROUTES = [
    Route("/agents/detail", agent_detail, methods=["GET"]),
    Route("/agents/setup", agent_setup, methods=["GET"]),
    Route("/agents/{agent}", agent_detail, methods=["GET"]),
    Route("/inbox/pauses/{root}/resume", resume_conversation, methods=["POST"]),
    Route("/memory/settings", memory_settings, methods=["GET", "POST"]),
    Route("/memory/{id}/{verb}", mutate_memory, methods=["POST"]),
    Route("/memory/{id}", mutate_memory, methods=["DELETE"]),
]
