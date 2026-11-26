"""MCP Tasks (io.modelcontextprotocol/tasks, protocol 2026-07-28) for capy_delegate.

When a tools/call request advertises the extension in its _meta client capabilities, capy_delegate answers with a task
instead of a plain result. The task id is the delegation's switchboard handle, which lives in SQLite, so tasks/get keeps
working across restarts. The task completes when the Capy thread reports its final result. Clients without the
extension get the ordinary result (handle plus capy_wait).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.extension import Extension, MethodBinding
from mcp_types import RequestParams

from ...audit import iso

if TYPE_CHECKING:
    from . import MCPOrigin

EXTENSION = "io.modelcontextprotocol/tasks"
CAPS_KEY = "io.modelcontextprotocol/clientCapabilities"
POLL_MS = 10_000
INVALID_PARAMS = -32602
TASK_FAILED = -32000


class TaskParams(RequestParams):
    taskId: str


class UpdateParams(RequestParams):
    taskId: str
    inputResponses: dict[str, Any] = {}


def wants_tasks(meta: Any) -> bool:
    caps = (meta or {}).get(CAPS_KEY) or {}
    extensions = caps.get("extensions") if isinstance(caps, dict) else None
    return isinstance(extensions, dict) and EXTENSION in extensions


def _structured(result: Any) -> tuple[bool, dict[str, Any] | None]:
    if isinstance(result, dict):
        return bool(result.get("isError")), result.get("structuredContent")
    return bool(getattr(result, "is_error", False)), getattr(result, "structured_content", None)


class TasksExtension(Extension):
    identifier = EXTENSION

    def __init__(self, origin: "MCPOrigin"):
        self.origin = origin

    @property
    def core(self) -> Any:
        return self.origin.core

    def ttl_ms(self) -> int:
        dest = self.core.destinations.get("capy")
        return int(getattr(dest, "handle_ttl", 7 * 86400) * 1000)

    async def intercept_tool_call(self, params: Any, ctx: Any, call_next: Any) -> Any:
        from . import TASK_MODE

        if params.name != "capy_delegate" or not wants_tasks(ctx.meta):
            return await call_next(ctx)
        token = TASK_MODE.set(True)
        try:
            result = await call_next(ctx)
        finally:
            TASK_MODE.reset(token)
        is_error, data = _structured(result)
        if is_error or not data or not data.get("handle"):
            return result
        h = self.core.inbox.handle(data["handle"])
        return {"resultType": "task", **self._task(h, detailed=False)}

    def _principal(self, ctx: Any) -> Any:
        from mcp.shared.exceptions import MCPError

        from . import principal_of

        p = principal_of(ctx.request)
        if p is None:
            raise MCPError(code=INVALID_PARAMS, message="unauthenticated")
        return p

    def _handle(self, ctx: Any, task_id: str) -> dict[str, Any]:
        from mcp.shared.exceptions import MCPError

        p = self._principal(ctx)
        h = self.core.inbox.handle(task_id)
        if h is None or h["owner"] != p.source or h["destination"] != "capy":
            raise MCPError(code=INVALID_PARAMS, message=f"unknown task {task_id!r}")
        return h

    def _task(self, h: dict[str, Any], detailed: bool = True) -> dict[str, Any]:
        state = h["state"]
        base: dict[str, Any] = {
            "taskId": h["handle"],
            "createdAt": iso(h["created_at"]),
            "lastUpdatedAt": iso(h.get("updated_at") or h["created_at"]),
            "ttlMs": self.ttl_ms(),
            "pollIntervalMs": POLL_MS,
        }
        target = h["meta"].get("target") or {}
        where = f"thread {h['ref']}" + (f" ({target.get('name')})" if target.get("name") else "")
        if h["open"] or not detailed:
            msg = f"Capy is working on it in {where}"
            if state.get("ask"):
                msg += f"; it needs the owner: {state['ask']}"
            elif state.get("last_reply"):
                msg += f"; latest reply: {state['last_reply'][:300]}"
            return {**base, "status": "working", "statusMessage": msg}
        status = h["status"]
        text = state.get("final") or state.get("last_reply") or ""
        if status == "cancelled":
            return {**base, "status": "cancelled", "statusMessage": text or "cancelled"}
        if status in ("failed", "expired"):
            return {**base, "status": "failed", "statusMessage": text, "error": {"code": TASK_FAILED, "message": text or status}}
        prs = state.get("pull_requests") or []
        body = text + ("\n\nPull requests:\n" + "\n".join(f"- {u}" for u in prs) if prs else "") + f"\n\n(Capy {where}, handle {h['handle']})"
        result = {
            "content": [{"type": "text", "text": body}],
            "structuredContent": {"handle": h["handle"], "thread_id": h["ref"], "status": status, "reply": text, "pull_requests": prs},
            "isError": False,
        }
        return {**base, "status": "completed", "statusMessage": "Capy reported its result", "result": result}

    def methods(self) -> list[MethodBinding]:
        async def get(ctx: Any, params: TaskParams) -> dict[str, Any]:
            return {"resultType": "complete", **self._task(self._handle(ctx, params.taskId))}

        async def update(ctx: Any, params: UpdateParams) -> dict[str, Any]:
            self._handle(ctx, params.taskId)
            return {"resultType": "complete"}

        async def cancel(ctx: Any, params: TaskParams) -> dict[str, Any]:
            h = self._handle(ctx, params.taskId)
            dest = self.core.destination("capy")
            await self.core.invoke(self._principal(ctx), "capy", dest.action("cancel"), {"handle": h["handle"]})
            return {"resultType": "complete"}

        return [
            MethodBinding(method="tasks/get", params_type=TaskParams, handler=get),
            MethodBinding(method="tasks/update", params_type=UpdateParams, handler=update),
            MethodBinding(method="tasks/cancel", params_type=TaskParams, handler=cancel),
        ]
