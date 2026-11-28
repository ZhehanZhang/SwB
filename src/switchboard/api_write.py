"""Owner-console JSON writes. The API mount authenticates the owner and checks CSRF."""

from __future__ import annotations

from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import Principal
from .blocks import BlockError
from .blocks.mcp import RouteDefaults
from .config import SLUG_RE
from .connect import AGENT_RE
from .core import CoreError
from .guard import client_ip
from .routes import RouteError
from .slack.writes import Actor, WriteError
from .visibility import VisibilityError


class _Body(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class _RouteDefaults(RouteDefaults):
    model_config = _Body.model_config


class _RouteBody(_Body):
    destination: str
    target: dict[str, str]
    agent: str | None = None
    project: str | None = None
    defaults: _RouteDefaults | None = None
    allow_override: bool = False
    note: str = ""


class _VisibilityBody(_Body):
    resource: str
    visibility: str | list[str]


class _MemoryGrantBody(_Body):
    scope: str


class _ModeBody(_Body):
    block: str
    space: str
    channel: str = ""
    mode: str
    actions: list[str] | None = None
    project: str | None = None
    target: str | None = None


class _SendBody(_Body):
    task: str
    to: str | None = None
    project: str | None = None
    title: str | None = None
    dry_run: bool = False


class _InboxActionBody(_Body):
    action: Literal["read", "redeliver"]
    handles: list[str] = Field(min_length=1, max_length=50)
    cursor: int | None = Field(default=None, ge=1, le=2**63 - 1)
    count: int | None = Field(default=None, ge=0, le=2**63 - 1)


_Model = TypeVar("_Model", bound=_Body)


async def _body(request: Request, model: type[_Model]) -> _Model | None:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return None
    try:
        return model.model_validate_json(await request.body())
    except ValidationError:
        return None


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def _owner(request: Request) -> Principal:
    owner = request.state.owner
    return Principal(source=f"admin:{owner.label}", client_id=client_ip(request.scope), kind="owner_session", origin="http")


async def route_set(request: Request) -> JSONResponse:
    body = await _body(request, _RouteBody)
    if body is None:
        return _error("invalid_request")
    try:
        out = await request.app.state.core.set_route(
            _owner(request), destination=body.destination, agent=body.agent, project=body.project, target=body.target,
            defaults=body.defaults.model_dump(exclude_none=True) if body.defaults else None,
            allow_override=body.allow_override, note=body.note,
        )
    except (CoreError, BlockError, RouteError):
        return _error("route_not_saved")
    return JSONResponse({"id": out["id"]})


async def route_remove(request: Request) -> JSONResponse:
    core = request.app.state.core
    rid = request.path_params["id"]
    route = next((r for r in core.routes.list() if str(r.id) == rid), None)
    if route is None:
        return _error("route_not_found", 404)
    try:
        core.remove_route(_owner(request), destination=route.destination, agent=route.agent, project=route.project)
    except RouteError:
        return _error("route_not_found", 404)
    return JSONResponse({"id": route.id, "removed": True})


async def visibility_set(request: Request) -> JSONResponse:
    core = request.app.state.core
    body = await _body(request, _VisibilityBody)
    if body is None:
        return _error("invalid_request")
    owner = _owner(request)
    try:
        resource, rule = await core.set_visibility(body.resource, body.visibility, owner.source)
    except VisibilityError:
        return _error("visibility_not_saved")
    core.audit.record(origin=owner.origin, source=owner.source, client=owner.client_id, destination="core", action="visibility_set",
                      outcome="ok", target=resource, detail=str(rule))
    return JSONResponse({"resource": resource, "visibility": rule})


async def pairing_revoke(request: Request) -> JSONResponse:
    core = request.app.state.core
    owner = _owner(request)
    kid = request.path_params["kid"]
    hook = kid.startswith("hook:")
    revoked = core.hook_wake.revoke(kid[5:]) if hook else core.connections.revoke(key_id=kid)
    core.audit.record(origin=owner.origin, source=owner.source, client=owner.client_id, destination="core",
                      action="muse_hook_revoke" if hook else "connect_revoke",
                      outcome="ok" if revoked else "refused", target=kid if revoked else None)
    if not revoked:
        return _error("key_not_found", 404)
    return JSONResponse({"id": kid, "revoked": True})


async def pairing_memory_remove(request: Request) -> JSONResponse:
    core = request.app.state.core
    body = await _body(request, _MemoryGrantBody)
    if body is None:
        return _error("invalid_request")
    scope = body.scope
    if not (scope == "general" or (scope.startswith("project:") and SLUG_RE.fullmatch(scope[8:]))
            or (scope.startswith("agent:") and AGENT_RE.fullmatch(scope[6:]))):
        return _error("invalid_request")
    kid = request.path_params["kid"]
    removed = core.connections.remove_memory_scope(kid, scope)
    if removed is None:
        return _error("key_not_found", 404)
    if removed:
        owner = _owner(request)
        core.audit.record(origin=owner.origin, source=owner.source, client=owner.client_id, destination="core", action="memory_grant_removed",
                          outcome="ok", target=kid, ref=scope, detail="Removed a paired key memory scope.")
    return JSONResponse({"id": kid, "scopes": list(core.connections.memory_scopes(kid)), "removed": removed})


async def routine_remove(request: Request) -> JSONResponse:
    core = request.app.state.core
    name = request.path_params["name"]
    selected = request.query_params.get("block")
    block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"
                  and (selected is None or b.name == selected)), None)
    try:
        removed = block.remove(name) if block else False
    except BlockError:
        return _error("routine_not_removed")
    owner = _owner(request)
    core.audit.record(origin=owner.origin, source=owner.source, client=owner.client_id, destination="claude-routine",
                      action="routine_remove", outcome="ok" if removed else "refused", target=name if removed else None)
    if not removed:
        return _error("routine_not_found", 404)
    return JSONResponse({"name": name, "removed": True})


async def slack_decide(request: Request) -> JSONResponse:
    slack = request.app.state.core.slack
    verb = request.path_params["verb"]
    if slack is None or verb not in ("approve", "reject"):
        return _error("not_found", 404)
    try:
        out = await slack.gate.decide(request.path_params["rid"], verb == "approve", Actor("admin", request.state.owner.label))
    except WriteError:
        return _error("decision_refused", 409)
    return JSONResponse({"id": out["id"], "status": out["status"]})


async def send(request: Request) -> JSONResponse:
    body = await _body(request, _SendBody)
    if body is None or not body.task.strip():
        return _error("invalid_request")
    try:
        out = await request.app.state.core.send(_owner(request), **body.model_dump())
    except (CoreError, BlockError):
        return _error("send_refused")
    fields = ("dry_run", "to", "block", "project", "route", "mode", "status", "handle", "conversation",
              "thread_id", "delivered", "mailbox", "fired")
    return JSONResponse({key: out[key] for key in fields if key in out})


async def modes(request: Request) -> JSONResponse:
    service = request.app.state.core.modes
    if request.method in ("GET", "HEAD"):
        await service.refresh()
        return JSONResponse(service.status())
    body = await _body(request, _ModeBody)
    if body is None:
        return _error("invalid_request")
    try:
        row = await service.set(_owner(request), **body.model_dump(),
                                preserve_actions="actions" not in body.model_fields_set,
                                preserve_project="project" not in body.model_fields_set)
    except ValueError:
        return _error("mode_not_saved")
    return JSONResponse({"space": row})


async def inbox_action(request: Request) -> JSONResponse:
    body = await _body(request, _InboxActionBody)
    if body is None or any(not handle or len(handle) > 256 for handle in body.handles):
        return _error("invalid_request")
    if (body.cursor is not None and len(body.handles) != 1) or (body.count is not None and body.cursor is None):
        return _error("invalid_request")
    core = request.app.state.core
    handles = list(dict.fromkeys(body.handles))
    if body.action == "redeliver":
        if body.cursor is not None:
            return _error("invalid_request")
        updated = [handle for handle in handles if core.inbox.redeliver_owner(handle)]
        if updated:
            await core.owner_notify.flush()
    elif body.cursor is not None:
        updated = handles if core.inbox.mark_owner_read(handles[0], body.cursor, body.count) else []
    else:
        marks = ", ".join("?" for _ in handles)
        latest = core.store.q(
            "SELECT i.handle, i.id, json_extract(i.data, '$.count') AS count FROM handles h "
            "JOIN inbox i ON i.id = (SELECT id FROM inbox WHERE handle = h.handle ORDER BY id DESC LIMIT 1) "
            f"WHERE h.handle IN ({marks})", handles,
        )
        updated = [row["handle"] for row in latest if core.inbox.mark_owner_read(row["handle"], row["id"], row["count"])]
    return JSONResponse({"updated": updated})


WRITE_ROUTES = [
    Route("/routes", route_set, methods=["POST"]),
    Route("/routes/{id}", route_remove, methods=["DELETE"]),
    Route("/visibility", visibility_set, methods=["POST"]),
    Route("/pairing/keys/{kid}/revoke", pairing_revoke, methods=["POST"]),
    Route("/pairing/keys/{kid}/memory/remove", pairing_memory_remove, methods=["POST"]),
    Route("/routines/{name}/remove", routine_remove, methods=["POST"]),
    Route("/slack/requests/{rid}/{verb}", slack_decide, methods=["POST"]),
    Route("/send", send, methods=["POST"]),
    Route("/inbox/actions", inbox_action, methods=["POST"]),
    Route("/modes", modes, methods=["GET", "POST"]),
]
