"""Owner-console JSON writes. The API mount authenticates the owner and checks CSRF."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import Principal
from .blocks import BlockError
from .blocks.mcp import RouteDefaults
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
    revoked = core.connections.revoke(key_id=kid)
    core.audit.record(origin=owner.origin, source=owner.source, client=owner.client_id, destination="core", action="connect_revoke",
                      outcome="ok" if revoked else "refused", target=kid if revoked else None)
    if not revoked:
        return _error("key_not_found", 404)
    return JSONResponse({"id": kid, "revoked": True})


async def routine_remove(request: Request) -> JSONResponse:
    core = request.app.state.core
    name = request.path_params["name"]
    block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
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
        row = await service.set(_owner(request), **body.model_dump())
    except ValueError:
        return _error("mode_not_saved")
    return JSONResponse({"space": row})


WRITE_ROUTES = [
    Route("/routes", route_set, methods=["POST"]),
    Route("/routes/{id}", route_remove, methods=["DELETE"]),
    Route("/visibility", visibility_set, methods=["POST"]),
    Route("/pairing/keys/{kid}/revoke", pairing_revoke, methods=["POST"]),
    Route("/routines/{name}/remove", routine_remove, methods=["POST"]),
    Route("/slack/requests/{rid}/{verb}", slack_decide, methods=["POST"]),
    Route("/send", send, methods=["POST"]),
    Route("/modes", modes, methods=["GET", "POST"]),
]
