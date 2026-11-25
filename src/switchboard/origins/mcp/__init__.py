"""The MCP origin: Claude, ChatGPT and other MCP clients, signed in with OAuth or a bearer key, calling the core through tools."""

from __future__ import annotations

import contextvars
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.routing import BaseRoute, Mount

from ...auth import Principal
from ...memory import MemoryError_
from ...projects import ProjectError
from ...routes import RouteError
from .. import Origin
from .tasks import TasksExtension

if TYPE_CHECKING:
    from ...core import Switchboard

PRINCIPAL_SCOPE = "switchboard.principal"
TASK_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar("switchboard_task_mode", default=False)

INSTRUCTIONS = """Switchboard is the owner's shared workspace across Claude, ChatGPT and Capy.
Memory lives in scopes: 'general' (the owner's profile, preferences, cross-project decisions) or 'project:<slug>'.
At the start of a session call context_pack with the project you are working in (project_list shows them).
Search before asking the owner something he may already have said. Every memory_write names its scope.
Capy is a coding agent with cloud machines, git and PRs. Use capy_delegate for work that needs a real machine,
a repo, a PR, or more than a few minutes. It goes where the owner's route for you and the project points
(usually his Captain), and returns a handle. Then call capy_wait(handle) to wait for Capy's reply and answer
with it. If capy_wait times out, tell the owner Capy is still working and check again with capy_wait or capy_inbox."""

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_REMOTE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

Scope = Annotated[str, Field(description="Required: 'general' for the owner's cross-project memory, or 'project:<slug>'")]
ProjectArg = Annotated[str | None, Field(description="Project slug (project_list). Reads cover that project first, then general")]
Namespace = Annotated[str, Field(description="Inside the scope: profile, decisions, notes, or e.g. notes/meetings; lowercase, up to 3 levels")]


class FileIn(BaseModel):
    name: str = Field(description="File name, e.g. spec.md")
    text: str | None = Field(default=None, description="File content as text")
    base64: str | None = Field(default=None, description="File content as base64, for binary files")
    media_type: str | None = Field(default=None, description="MIME type, e.g. text/markdown")


class Links(BaseModel):
    capy_project: str | None = Field(default=None, description="Capy project id, e.g. project_01...")
    repos: list[str] | None = Field(default=None, description="owner/repo names that belong to the project")
    claude_project: str | None = Field(default=None, description="Name of the matching Claude Project")
    chatgpt_project: str | None = Field(default=None, description="Name of the matching ChatGPT project")


class RouteDefaults(BaseModel):
    model: str | None = Field(default=None, description="Capy model id for new work, e.g. anthropic/claude-opus-5-5")
    repos: list[str] | None = Field(default=None, description="Repos every task on this route concerns")
    mode: Literal["captain_thread", "new_thread"] | None = Field(default=None, description="For a Captain: message its thread (default) or start a crew thread")
    delivery: Literal["queue", "steer", "interrupt"] | None = Field(default=None, description="How a message reaches a busy thread")


def principal_of(request: Any) -> Principal | None:
    p = request.scope.get(PRINCIPAL_SCOPE) if request is not None else None
    return p if isinstance(p, Principal) else None


class WaitCaps:
    """capy_wait limits per agent. Hosted Claude allows 240 s per call; Claude Code by default fails a request with no response byte in 60 s."""

    def __init__(self, raw: dict[str, Any] | None):
        raw = raw or {}
        self.default_s = float(raw.get("default_s", 60))
        self.max_s = float(raw.get("max_s", 200))
        self.agents = {str(k).lower(): dict(v or {}) for k, v in (raw.get("agents") or {"claude-code": {"default_s": 45, "max_s": 55}}).items()}

    def for_agent(self, agent: str) -> tuple[float, float]:
        a = self.agents.get(agent.lower(), {})
        mx = float(a.get("max_s", self.max_s))
        return min(float(a.get("default_s", self.default_s)), mx), mx

    def clamp(self, agent: str, asked: float | None) -> float:
        default, mx = self.for_agent(agent)
        return max(0.0, min(mx, default if asked is None else float(asked)))


class MCPOrigin(Origin):
    name = "mcp"

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        self.wait_caps = WaitCaps(config.get("wait"))
        self.mcp = self.build()
        self.app = self.mcp.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    def routes(self) -> list[BaseRoute]:
        return [Mount("/", app=self.app)]

    def lifespan(self) -> AbstractAsyncContextManager[None]:
        return self.app.router.lifespan_context(self.app)

    def build(self) -> MCPServer:
        core = self.core
        has_capy = "capy" in core.destinations
        mcp = MCPServer(name="switchboard", instructions=INSTRUCTIONS, extensions=[TasksExtension(self)] if has_capy else [])

        def who(ctx: Context) -> Principal:
            p = principal_of(ctx.request_context.request)
            if p is None:
                raise ToolError("unauthenticated")
            return p

        def sync(fn: Any, *a: Any, **kw: Any) -> Any:
            try:
                return fn(*a, **kw)
            except (MemoryError_, ProjectError, RouteError) as e:
                raise ToolError(str(e)) from e

        async def run(coro: Any) -> Any:
            from ...core import CoreError
            from ...destinations import DestinationError

            try:
                return await coro
            except (CoreError, DestinationError, MemoryError_, ProjectError, RouteError) as e:
                raise ToolError(str(e)) from e

        async def capy(ctx: Context, action: str, **kw: Any) -> Any:
            from ...destinations import DestinationError

            dest = core.destination("capy")
            try:
                return await run(core.invoke(who(ctx), "capy", dest.action(action), kw))
            except DestinationError as e:
                raise ToolError(str(e)) from e

        # Memory and projects (core) --------------------------------------------------------

        @mcp.tool(
            title="Save to shared memory",
            description="Save or update a durable memory shared with every assistant and Capy. scope is required: 'general' for the owner's profile, preferences and cross-project decisions, 'project:<slug>' for one project. A key makes an entry you will update (e.g. profile/editor). mode=append adds to the existing body.",
            annotations=WRITE,
        )
        async def memory_write(
            ctx: Context,
            scope: Scope,
            namespace: Namespace,
            title: Annotated[str, Field(description="Short title")],
            body: Annotated[str, Field(description="Markdown content, max 20k chars")],
            key: Annotated[str | None, Field(description="Optional stable slug; the same scope+namespace+key updates that entry")] = None,
            tags: Annotated[list[str] | None, Field(description="Optional tags")] = None,
            id: Annotated[str | None, Field(description="Update the entry with this id instead")] = None,
            mode: Literal["replace", "append"] = "replace",
        ) -> dict[str, Any]:
            return sync(core.memory.write, source=who(ctx).source, scope=scope, namespace=namespace, title=title, body=body, key=key, tags=tags, id=id, mode=mode)

        @mcp.tool(title="Search shared memory", description="Full-text search. With project: that project's memory ranked first, then general (include_general=false for the project alone). Without: general only.", annotations=READ)
        async def memory_search(
            ctx: Context,
            query: str,
            project: ProjectArg = None,
            include_general: bool = True,
            namespace: Annotated[str | None, Field(description="Namespace prefix filter")] = None,
            limit: Annotated[int, Field(ge=1, le=50)] = 10,
        ) -> dict[str, Any]:
            who(ctx)
            return sync(lambda: core.memory.search(query=query, scopes=core.memory.read_scopes(project, include_general), namespace=namespace, limit=limit))

        @mcp.tool(title="Read a memory", description="Read one memory in full by id, or by scope, namespace and key, with its recent edit history.", annotations=READ)
        async def memory_read(ctx: Context, id: str | None = None, scope: str | None = None, namespace: str | None = None, key: str | None = None) -> dict[str, Any]:
            who(ctx)
            return sync(core.memory.read, id=id, scope=scope, namespace=namespace, key=key)

        @mcp.tool(title="List memories", description="List memories newest first with per-namespace counts. project works as in memory_search.", annotations=READ)
        async def memory_list(
            ctx: Context,
            project: ProjectArg = None,
            include_general: bool = True,
            namespace: str | None = None,
            limit: Annotated[int, Field(ge=1, le=200)] = 50,
            offset: Annotated[int, Field(ge=0)] = 0,
        ) -> dict[str, Any]:
            who(ctx)
            return sync(lambda: core.memory.list(scopes=core.memory.read_scopes(project, include_general), namespace=namespace, limit=limit, offset=offset))

        @mcp.tool(title="Delete a memory", description="Delete a memory by id when it is wrong or obsolete. History is kept server-side.", annotations=DELETE)
        async def memory_delete(ctx: Context, id: str) -> dict[str, Any]:
            return sync(core.memory.delete, source=who(ctx).source, id=id)

        @mcp.tool(
            title="Session briefing",
            description="Read at the start of a session: the project's memory and links, the owner's general profile and recent decisions, and, when Capy is routed, the routed Captain's digest, its relevant active work, and delegations in progress.",
            annotations=READ_REMOTE,
        )
        async def context_pack(ctx: Context, project: ProjectArg = None) -> str:
            return await run(core.context_pack(who(ctx), project))

        @mcp.tool(title="List projects", description="Projects that scope memory and routes, with their links (Capy project, repos, Claude and ChatGPT project names). hint finds the project for a repo or a Claude Project name.", annotations=READ)
        async def project_list(ctx: Context, include_archived: bool = False, hint: str | None = None) -> dict[str, Any]:
            who(ctx)
            if hint:
                return {"matches": sync(core.projects.resolve, hint)}
            return {"projects": [p.as_dict() for p in core.projects.list(include_archived)]}

        @mcp.tool(title="Create a project", description="Create a project, a memory scope 'project:<slug>'. Do this only when the owner starts a genuinely new project.", annotations=WRITE)
        async def project_create(ctx: Context, slug: Annotated[str, Field(description="lowercase-dashed, e.g. switchboard")], name: str,
                                 description: str = "", links: Links | None = None) -> dict[str, Any]:
            who(ctx)
            return sync(core.projects.create, slug=slug, name=name, description=description, links=links.model_dump(exclude_none=True) if links else None).as_dict()

        @mcp.tool(title="Update a project", description="Rename (new_slug moves its memories and routes), describe, relink, archive or restore a project. links merge: a field set to empty clears it.", annotations=WRITE_IDEMPOTENT)
        async def project_update(ctx: Context, slug: str, name: str | None = None, description: str | None = None, links: Links | None = None,
                                 new_slug: str | None = None, archived: bool | None = None) -> dict[str, Any]:
            who(ctx)
            return sync(core.projects.update, slug, name=name, description=description, links=links.model_dump(exclude_unset=True) if links else None,
                        new_slug=new_slug, archived=archived).as_dict()

        # Routes (core) ---------------------------------------------------------------------

        @mcp.tool(title="List routes", description="Where capy_delegate sends work, per agent and project. Resolution: agent+project, then project, then agent, then global.", annotations=READ)
        async def routes_list(ctx: Context) -> dict[str, Any]:
            who(ctx)
            return {"routes": [r.as_dict() for r in core.routes.list()]}

        @mcp.tool(
            title="Set a route",
            description="Point an agent (claude, chatgpt, claude-code, or a key name), a project, both, or neither (global) at a Capy target: 'captain:<code, id or name>', 'project:<Capy project id or code>', or 'thread:<jam_id>'. allow_override lets callers pass an explicit target. Audited.",
            annotations=WRITE_IDEMPOTENT,
        )
        async def routes_set(ctx: Context, target: str, project: str | None = None, agent: str | None = None, defaults: RouteDefaults | None = None,
                             allow_override: bool = False, note: str = "") -> dict[str, Any]:
            from ...destinations.capy import parse_target

            p = who(ctx)
            return await run(core.set_route(p, destination="capy", agent=agent, project=project, target=parse_target(target),
                                            defaults=defaults.model_dump(exclude_none=True) if defaults else {}, allow_override=allow_override, note=note))

        @mcp.tool(title="Remove a route", description="Remove the route for exactly this agent/project pair (both empty: the global route). Audited.", annotations=DELETE)
        async def routes_remove(ctx: Context, project: str | None = None, agent: str | None = None) -> dict[str, Any]:
            return sync(core.remove_route, who(ctx), destination="capy", agent=agent, project=project)

        @mcp.tool(title="Explain a route", description="Which route capy_delegate would use for an agent (default: you) and project, and the levels checked.", annotations=READ)
        async def routes_resolve(ctx: Context, project: str | None = None, agent: str | None = None) -> dict[str, Any]:
            p = who(ctx)
            r, tried = core.routes.resolve(destination="capy", agent=agent or p.source, project=project)
            return {"agent": agent or p.source, "project": project, "checked": tried, "route": r.as_dict() if r else None}

        @mcp.tool(title="Switchboard status", description="Who you are signed in as (your agent name for routes), your capy_wait limits, and what is configured or degraded.", annotations=READ)
        async def switchboard_status(ctx: Context) -> dict[str, Any]:
            p = who(ctx)
            default, mx = self.wait_caps.for_agent(p.source)
            return {"you": {"agent": p.source, "auth": p.kind, "origin": p.origin}, "capy_wait": {"default_s": default, "max_s": mx}, **core.health()}

        if not has_capy:
            return mcp

        # Capy destination ------------------------------------------------------------------

        @mcp.tool(
            title="Delegate to Capy",
            description=(
                "Hand a task to Capy, a coding agent with cloud machines, git and PRs, when it needs a real machine, a repo, a PR, or long-running work. "
                "It goes to the target your route names for you and project (see routes_resolve), usually the owner's Captain. "
                "Write the task self-contained: goal, context, repos, what done means. Returns a handle. Then call capy_wait(handle) "
                "and answer with Capy's reply; if it times out, say Capy is still working and check later. wait_s>0 waits right here. Limited per day."
            ),
            annotations=SEND,
        )
        async def capy_delegate(
            ctx: Context,
            task: Annotated[str, Field(description="Self-contained task description in markdown")],
            project: ProjectArg = None,
            title: Annotated[str | None, Field(description="Short title")] = None,
            target: Annotated[str | None, Field(description="Only when your route allows overriding: captain:<x>, project:<x> or thread:<jam_id>")] = None,
            repos: Annotated[list[str] | None, Field(description="owner/repo names the task concerns; must be linked to the project")] = None,
            files: Annotated[list[FileIn] | None, Field(description="Optional files to attach")] = None,
            new_thread: Annotated[bool | None, Field(description="For a Captain target: true starts a crew thread instead of messaging the Captain")] = None,
            wait_s: Annotated[float, Field(ge=0, description="Seconds to wait for a first reply before returning (capped per client)")] = 0,
        ) -> dict[str, Any]:
            p = who(ctx)
            out = await capy(ctx, "delegate", task=task, title=title, project=project, target=target, repos=repos,
                             files=[f.model_dump() for f in files] if files else None, new_thread=new_thread)
            if wait_s and not TASK_MODE.get():
                got = await core.inbox.wait(owner=p.source, handle=out["handle"], timeout=self.wait_caps.clamp(p.source, wait_s))
                out["replies"], out["cursor"], out["timed_out"] = got["items"], got["cursor"], got["timed_out"]
            return out

        @mcp.tool(
            title="Wait for Capy",
            description="Wait for Capy to report on delegated work: returns as soon as a reply, a question for the owner, or the final result arrives, or empty when timeout_s runs out (default and maximum depend on your client; see switchboard_status). Without handle it waits on all your delegations. Each call returns only what you have not seen.",
            annotations=READ,
        )
        async def capy_wait(ctx: Context, handle: str | None = None, timeout_s: Annotated[float | None, Field(ge=0)] = None) -> dict[str, Any]:
            p = who(ctx)
            got = await core.inbox.wait(owner=p.source, handle=handle, destination="capy", timeout=self.wait_caps.clamp(p.source, timeout_s))
            if handle:
                h = core.inbox.handle(handle)
                got["handle_status"] = (h["status"] if h else "unknown") if (h is None or not h["open"]) else "following"
            if got["timed_out"]:
                got["next"] = "Nothing new yet. Tell the owner Capy is still working; call capy_wait again or capy_inbox later."
            return got

        @mcp.tool(title="Capy inbox", description="What Capy reported on your delegations since since_cursor (default: since you last read). Never blocks.", annotations=READ)
        async def capy_inbox(ctx: Context, since_cursor: int | None = None, handle: str | None = None, limit: Annotated[int, Field(ge=1, le=100)] = 50) -> dict[str, Any]:
            p = who(ctx)
            out = core.inbox.read(owner=p.source, handle=handle, since=since_cursor, destination="capy", limit=limit)
            out["open"] = [{"handle": h["handle"], "thread_id": h["ref"], "status": h["status"], "title": h["title"] or h["meta"].get("task", "")[:80]}
                           for h in core.inbox.handles(owner=p.source, destination="capy", open_only=True, limit=20)]
            return out

        @mcp.tool(title="Capy thread status", description="Status of a Capy thread (by thread id or handle): working/waiting/idle/failed, whether it needs the owner, its PRs and latest messages.", annotations=READ_REMOTE)
        async def capy_status(ctx: Context, thread_id: Annotated[str, Field(description="jam_ thread id or h_ handle")], messages: Annotated[int, Field(ge=1, le=20)] = 5,
                              include_tools: bool = False) -> dict[str, Any]:
            return await capy(ctx, "status", thread_id=thread_id, messages=messages, include_tools=include_tools)

        @mcp.tool(title="List Capy work", description="scope 'delegated' (delegations through switchboard, with live status), 'captain' (a Captain's crew; default the routed one), or 'project' (threads in a project's linked Capy project).", annotations=READ_REMOTE)
        async def capy_list(ctx: Context, scope: Literal["delegated", "captain", "project"] = "delegated", project: str | None = None, captain: str | None = None,
                            limit: Annotated[int, Field(ge=1, le=30)] = 10) -> dict[str, Any]:
            return await capy(ctx, "list", scope=scope, project=project, captain=captain, limit=limit)

        @mcp.tool(title="Reply to a Capy thread", description="Send a follow-up into a Capy thread (thread id or handle): answer its question, add context, or redirect it. delivery: steer (default), queue, interrupt.", annotations=SEND)
        async def capy_reply(ctx: Context, thread_id: str, text: str, delivery: Literal["steer", "queue", "interrupt"] = "steer") -> dict[str, Any]:
            return await capy(ctx, "reply", thread_id=thread_id, text=text, delivery=delivery)

        @mcp.tool(title="Capy thread result", description="The latest final reply of a Capy thread (thread id or handle) in full, its PR links, and download links for delivered files (links expire).", annotations=READ_REMOTE)
        async def capy_result(ctx: Context, thread_id: str, tail: Annotated[int, Field(ge=0, le=30)] = 0) -> dict[str, Any]:
            return await capy(ctx, "result", thread_id=thread_id, tail=tail)

        @mcp.tool(title="Captain digest", description="A Captain's digest plus crew threads that need the owner or are working. Defaults to the Captain routed for you and project.", annotations=READ_REMOTE)
        async def capy_captain_digest(ctx: Context, captain: str | None = None, project: str | None = None) -> dict[str, Any]:
            return await capy(ctx, "captain_digest", captain=captain, project=project)

        @mcp.tool(title="List Captains", description="Captains the Capy key can see, for routes_set targets.", annotations=READ_REMOTE)
        async def capy_captains(ctx: Context) -> dict[str, Any]:
            return await capy(ctx, "captains")

        @mcp.tool(title="Stop following a delegation", description="Stop reporting on a handle. The Capy thread keeps running; use capy_reply to stop it.", annotations=WRITE_IDEMPOTENT)
        async def capy_cancel(ctx: Context, handle: str) -> dict[str, Any]:
            return await capy(ctx, "cancel", handle=handle)

        return mcp
