"""The MCP block: MCP clients (Claude, ChatGPT, Gemini, Claude Code, Capy, ...), signed in with OAuth, a bearer key or a
pairing-code key, calling the core through tools.

Generic tools come first: send, reply, wait, inbox, status, result, cancel and targets go to whichever block owns the
target or conversation. Each block adds its own tools, named <block>_<tool>. The old capy_* names stay listed as
deprecated aliases while mcp.legacy_aliases is on (the default this release); every call to one is counted.
The server instructions are a neutral core paragraph plus each enabled block's instructions fragment.
"""

from __future__ import annotations

import contextvars
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, create_model
from starlette.routing import BaseRoute, Mount

from ...auth import Principal
from ...memory import GENERAL, MemoryError_
from ...projects import ProjectError
from ...routes import RouteError
from ...targets import LEGACY_KINDS
from .. import Block, Manifest
from .events import EventsExtension, advertise_events
from .tasks import TasksExtension

if TYPE_CHECKING:
    from ...core import Switchboard

PRINCIPAL_SCOPE = "switchboard.principal"
TASK_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar("switchboard_task_mode", default=False)

CORE_INSTRUCTIONS = """Switchboard is a shared workspace for your assistants and agents.
Memory lives in scopes: 'general' (the owner's profile, preferences, cross-project decisions) or 'project:<slug>'.
Switchboard can't see which project you are in, so you decide the project: find it with project_list
(hint: your project's name or a repo), then call context_pack with it at the start of a session.
Write project facts to project:<slug> and pass project=<slug> too, so a slip lands nowhere. Write to general only for
things that apply everywhere, after the owner agrees, with confirm_general=true. Reads cover the named project plus
general; another project's memory needs scopes=[...] and is logged. memory_move and memory_promote fix a wrong scope.
Search before asking the owner something they may already have said.
When someone asks how to connect, set up or link something to Switchboard, call connect and follow its next steps.
To hand off work or message someone, use send: without to, the owner's route for you and the project decides; to can
be a slug from targets(), an agent's mailbox (agent:claude) or an address <block>:<kind>:<id>. Every agent (claude,
claude-code, chatgpt, gemini, capy, and each persona's agent) has a switchboard mailbox, so you can always message it,
even when it isn't running: it reads the mailbox on its own schedule, in a live chat, or when switchboard wakes it. Its
reply comes back to your inbox with in_reply_to set to the conversation send returned. Never say you can't reach an
agent; send to it. Then call wait() for
the reply and answer with it. If wait times out, tell the owner it is still working and check again with wait or
inbox. Answer a message with reply(text, conversation). Older names (capy_delegate, capy_wait, capy_inbox,
agent_send, ...) still work this release; prefer the new ones."""


READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_REMOTE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

Scope = Annotated[str, Field(description="Required: 'general' for the owner's cross-project memory, or 'project:<slug>'")]
ProjectArg = Annotated[str | None, Field(description="Project slug (project_list). Reads cover that project first, then general")]
Namespace = Annotated[str, Field(description="Inside the scope: profile, decisions, notes, or e.g. notes/meetings; lowercase, up to 3 levels")]
ScopesArg = Annotated[list[str] | None, Field(description="Only to read other projects on purpose: exact scopes, e.g. ['project:example', 'general']. Logged")]


class FileIn(BaseModel):
    name: str = Field(description="File name, e.g. spec.md")
    text: str | None = Field(default=None, description="File content as text")
    base64: str | None = Field(default=None, description="File content as base64, for binary files")
    media_type: str | None = Field(default=None, description="MIME type, e.g. text/markdown")


class RouteDefaults(BaseModel):
    model: str | None = Field(default=None, description="Model id for new work on the target, e.g. anthropic/claude-opus-5-5")
    repos: list[str] | None = Field(default=None, description="Repos every task on this route concerns")
    mode: Literal["captain_thread", "new_thread"] | None = Field(default=None, description="For a Captain: message its thread (default) or start a crew thread")
    delivery: Literal["queue", "steer", "interrupt"] | None = Field(default=None, description="How a message reaches a busy thread")


Links: Any = None  # the project links model, built per server from the link keys its blocks and agents provide


def principal_of(request: Any) -> Principal | None:
    p = request.scope.get(PRINCIPAL_SCOPE) if request is not None else None
    return p if isinstance(p, Principal) else None


class LimitedServer(MCPServer):
    """An MCP server that holds a key to its tool allowlist (auth.key_tools): tools/list shows only those tools and
    tools/call refuses any other."""

    async def _handle_list_tools(self, ctx: Any, params: Any) -> Any:
        out = await super()._handle_list_tools(ctx, params)
        p = principal_of(getattr(ctx, "request", None))
        if p is not None and p.tools is not None:
            out.tools = [t for t in out.tools if t.name in p.tools]
        return out

    async def _handle_call_tool(self, ctx: Any, params: Any) -> Any:
        p = principal_of(getattr(ctx, "request", None))
        if p is not None and p.tools is not None and params.name not in p.tools:
            return CallToolResult(content=[TextContent(type="text", text=f"the {p.source} key may call only {', '.join(p.tools) or 'no tools'}")],
                                  is_error=True)
        return await super()._handle_call_tool(ctx, params)


class WaitCaps:
    """wait limits per agent. Hosted Claude allows 240 s per call; Claude Code by default fails a request with no response byte in 60 s."""

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


class MCPBlock(Block):
    manifest = Manifest(id="mcp", title="MCP", kind="source", level_in="mcp", speaks=("message",),
                        config_schema={"type": "object", "properties": {"legacy_aliases": {"type": "boolean"}, "wait": {"type": "object"}}})

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        self.wait_caps = WaitCaps(config.get("wait"))
        self.legacy_aliases = bool(config.get("legacy_aliases", True))
        self.mcp: MCPServer | None = None
        self.app: Any = None
        self.events = EventsExtension(self, **(config.get("_events") or {}))

    def bind(self) -> None:
        self.mcp = self.build()
        self.app = self.mcp.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    def routes(self) -> list[BaseRoute]:
        return [Mount("/", app=self.app)]

    def next_step(self) -> str | None:
        if not self.core.cfg.public_url:
            return "Set public_url to let hosted MCP clients reach Switchboard."
        if not self.core.cfg.owner_sign_in:
            return "Configure owner sign-in to enable OAuth for MCP clients."
        return None

    def lifespan(self) -> AbstractAsyncContextManager[None]:
        return self.app.router.lifespan_context(self.app)

    async def start(self) -> None:
        self.events.start()

    async def aclose(self) -> None:
        await self.events.close()

    def instructions(self) -> str:
        parts = [CORE_INSTRUCTIONS] + [b.manifest.instructions.strip() for b in self.core.blocks.values() if b.manifest.instructions]
        return "\n".join(parts)

    def build(self) -> MCPServer:
        core = self.core
        senders = [b for b in core.blocks.values() if b.verb("send") is not None]
        extensions = [self.events, TasksExtension(self)] if senders else [self.events]
        mcp = LimitedServer(name="switchboard", instructions=self.instructions(), extensions=extensions)
        mcp.middleware.append(advertise_events)
        global Links
        link_keys = tuple(dict.fromkeys(core.projects.link_keys))
        Links = create_model("Links", repos=(list[str] | None, Field(default=None, description="owner/repo names that belong to the project")),
                             **{k: (str | None, Field(default=None, description=f"The matching {k.removesuffix('_project').replace('_', ' ')} project (id or name)"))
                                for k in link_keys})

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

        def need(p: Principal, scope: str) -> None:
            if not p.allows(scope):
                raise ToolError(f"the {p.source} key is limited to {', '.join(p.scopes or ())}; {scope} is outside it")

        def unrestricted(p: Principal, what: str) -> None:
            if p.scopes is not None and "*" not in p.scopes:
                raise ToolError(f"the {p.source} key is limited to {', '.join(p.scopes)}, so it can't {what}")

        def pscope(slug: str) -> str:
            return "project:" + slug.strip().lower().removeprefix("project:")

        def reading(p: Principal, project: str | None, include_general: bool, scopes: list[str] | None, what: str) -> list[str]:
            ss = sync(core.memory.explicit_scopes, scopes, project) if scopes else sync(core.memory.read_scopes, project, include_general)
            if project:
                need(p, pscope(project))
            for s in ss:
                if s != GENERAL:
                    need(p, s)
            ss = [s for s in ss if s != GENERAL or p.allows(GENERAL)]
            if not ss:
                raise ToolError(f"the {p.source} key can't read general; pass the project you work in")
            cross = [s for s in ss if s != GENERAL and (not project or s != pscope(project))]
            if cross:
                core.audit_memory(p, "cross_read", ",".join(cross), f"{what} while working in {project or 'general'}")
            return ss

        async def run(coro: Any) -> Any:
            from ...blocks import BlockError as DestinationError
            from ...core import CoreError

            try:
                return await coro
            except (CoreError, DestinationError, MemoryError_, ProjectError, RouteError) as e:
                raise ToolError(str(e)) from e

        # Memory and projects ------------------------------------------------------------------

        @mcp.tool(
            title="Save to shared memory",
            description="Save or update a durable memory shared with every assistant and agent. scope is required: 'project:<slug>' for anything about one project (pass project=<slug> as well), or 'general' for the owner's profile, preferences and decisions that apply to every project, which also needs confirm_general=true after the owner agrees. The result echoes the scope it landed in. A key makes an entry you will update (e.g. profile/editor). mode=append adds to the existing body.",
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
            project: Annotated[str | None, Field(description="The project you are working in. A project scope that differs from it is rejected")] = None,
            confirm_general: Annotated[bool, Field(description="Required for scope 'general': the owner agreed this applies to every project")] = False,
        ) -> dict[str, Any]:
            p = who(ctx)
            need(p, (scope or "").strip().lower())
            if id:
                need(p, sync(core.memory.scope_of, id))
            return sync(core.memory.write, source=p.source, scope=scope, namespace=namespace, title=title, body=body, key=key, tags=tags, id=id,
                        mode=mode, confirm_general=confirm_general, project=project)

        @mcp.tool(title="Search shared memory", description="Full-text search. With project: that project's memory ranked first, then general (include_general=false for the project alone). Without: general only. Another project's memory only with an explicit scopes list, which is logged; private projects never show up there.", annotations=READ)
        async def memory_search(
            ctx: Context,
            query: str,
            project: ProjectArg = None,
            include_general: bool = True,
            namespace: Annotated[str | None, Field(description="Namespace prefix filter")] = None,
            limit: Annotated[int, Field(ge=1, le=50)] = 10,
            scopes: ScopesArg = None,
        ) -> dict[str, Any]:
            ss = reading(who(ctx), project, include_general, scopes, f"search {query!r}")
            return sync(lambda: core.memory.search(query=query, scopes=ss, namespace=namespace, limit=limit))

        @mcp.tool(title="Read a memory", description="Read one memory in full by id, or by scope, namespace and key, with its recent edit history.", annotations=READ)
        async def memory_read(ctx: Context, id: str | None = None, scope: str | None = None, namespace: str | None = None, key: str | None = None) -> dict[str, Any]:
            p = who(ctx)
            if scope:
                need(p, scope.strip().lower())
            out = sync(core.memory.read, id=id, scope=scope, namespace=namespace, key=key)
            need(p, out["scope"])
            return out

        @mcp.tool(title="List memories", description="List memories newest first with per-namespace counts. project works as in memory_search.", annotations=READ)
        async def memory_list(
            ctx: Context,
            project: ProjectArg = None,
            include_general: bool = True,
            namespace: str | None = None,
            limit: Annotated[int, Field(ge=1, le=200)] = 50,
            offset: Annotated[int, Field(ge=0)] = 0,
            scopes: ScopesArg = None,
        ) -> dict[str, Any]:
            ss = reading(who(ctx), project, include_general, scopes, "list")
            return sync(lambda: core.memory.list(scopes=ss, namespace=namespace, limit=limit, offset=offset))

        @mcp.tool(title="Delete a memory", description="Delete a memory by id when it is wrong or obsolete. History is kept server-side.", annotations=DELETE)
        async def memory_delete(ctx: Context, id: str) -> dict[str, Any]:
            p = who(ctx)
            need(p, sync(core.memory.scope_of, id))
            return sync(core.memory.delete, source=p.source, id=id)

        @mcp.tool(title="Move a memory to another project", description="Move an entry that landed in the wrong project to project:<slug>. Keeps its id and history. Audited. Use memory_promote to move it to general.", annotations=WRITE_IDEMPOTENT)
        async def memory_move(ctx: Context, id: str, to_scope: Annotated[str, Field(description="'project:<slug>'")]) -> dict[str, Any]:
            p = who(ctx)
            if (to_scope or "").strip().lower() == GENERAL:
                raise ToolError("moving to general is memory_promote, after the owner agrees it applies to every project")
            frm = sync(core.memory.scope_of, id)
            need(p, frm)
            need(p, (to_scope or "").strip().lower())
            out = sync(core.memory.move, source=p.source, id=id, to_scope=to_scope)
            core.audit_memory(p, "memory_move", f"{out['from']} -> {out['to']}", id)
            return out

        @mcp.tool(title="Promote a memory to general", description="Move a project entry to general once the owner agrees it applies to every project. Keeps its id and history. Audited.", annotations=WRITE_IDEMPOTENT)
        async def memory_promote(ctx: Context, id: str, to: Literal["general"] = "general") -> dict[str, Any]:
            p = who(ctx)
            need(p, sync(core.memory.scope_of, id))
            need(p, GENERAL)
            out = sync(core.memory.move, source=p.source, id=id, to_scope=to)
            core.audit_memory(p, "memory_promote", f"{out['from']} -> {out['to']}", id)
            return out

        @mcp.tool(
            title="Session briefing",
            description="Read at the start of a session: the project's memory and links, the owner's general profile and recent decisions, and each block's section (for a routed Captain: its digest, relevant active work and conversations in progress).",
            annotations=READ_REMOTE,
        )
        async def context_pack(ctx: Context, project: ProjectArg = None) -> str:
            return await run(core.context_pack(who(ctx), project))

        @mcp.tool(title="List projects", description="Projects that scope memory and routes, with their links (repos, and the matching project in each block or assistant). hint finds the project for a repo or a project name. pending_projects lists new slugs waiting for an owner to save a routine registration.", annotations=READ)
        async def project_list(ctx: Context, include_archived: bool = False, hint: str | None = None) -> dict[str, Any]:
            who(ctx)
            block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
            pending = block.pending_projects() if block is not None else []
            if hint:
                return {"matches": sync(core.projects.resolve, hint), "pending_projects": pending}
            return {"projects": [p.as_dict() for p in core.projects.list(include_archived)], "pending_projects": pending}

        @mcp.tool(title="Create a project", description="Create a project, a memory scope 'project:<slug>'. Do this only when the owner starts a genuinely new project. Pass its repos: switchboard then links it to its counterpart in each block (a Capy project, say) and routes it (result.auto); ambiguous matches come back as candidates to ask the owner about.", annotations=WRITE)
        async def project_create(ctx: Context, slug: Annotated[str, Field(description="lowercase-dashed, e.g. switchboard")], name: str,
                                 description: str = "", links: Links | None = None,
                                 private: Annotated[bool, Field(description="Keep its memory out of other projects' reads, even explicit ones")] = False) -> dict[str, Any]:
            p = who(ctx)
            unrestricted(p, "create projects")
            out = sync(core.projects.create, slug=slug, name=name, description=description, links=links.model_dump(exclude_none=True) if links else None,
                       private=private).as_dict()
            out["auto"] = await run(core.project_sync(p, out["slug"]))
            return out

        @mcp.tool(title="Update a project", description="Rename (new_slug moves its memories and routes), describe, relink, archive or restore a project, or mark it private. links merge: a field set to empty clears it. While a block's link is empty, it tries to link and route the project (result.auto).", annotations=WRITE_IDEMPOTENT)
        async def project_update(ctx: Context, slug: str, name: str | None = None, description: str | None = None, links: Links | None = None,
                                 new_slug: str | None = None, archived: bool | None = None, private: bool | None = None,
                                 force: Annotated[bool, Field(description="Let the automatic link and route replace existing ones")] = False) -> dict[str, Any]:
            p = who(ctx)
            need(p, pscope(slug))
            if new_slug:
                need(p, pscope(new_slug))
            out = sync(core.projects.update, slug, name=name, description=description, links=links.model_dump(exclude_unset=True) if links else None,
                       new_slug=new_slug, archived=archived, private=private).as_dict()
            if new_slug:
                core.memory.schedule_mirror()
            given = links.model_dump(exclude_unset=True) if links else {}
            linked_now = any(v for k, v in given.items() if k != "repos")
            unlinked = any(not out["links"].get(k) for b in core.blocks.values() for k in b.manifest.link_keys)
            if not out["archived"] and (force or linked_now or unlinked):
                out["auto"] = await run(core.project_sync(p, out["slug"], force=force))
            return out

        @mcp.tool(title="Sync projects with blocks", description="Link projects to their counterparts in each block (an exact repo match beats a name match) and route each where that block suggests (for Capy: its project's only Captain). One project or all. Never replaces an existing link or route unless force. Reports what changed and the candidates when a match is ambiguous.", annotations=WRITE_IDEMPOTENT)
        async def project_sync(ctx: Context, slug: str | None = None, force: bool = False) -> dict[str, Any]:
            p = who(ctx)
            if slug:
                need(p, pscope(slug))
            else:
                unrestricted(p, "sync every project")
            return await run(core.project_sync(p, slug, force=force))

        # Routes ---------------------------------------------------------------------------

        def routing_block(name: str | None) -> Block:
            if name:
                b = core.blocks.get(name)
                if b is None or not b.supports_routes:
                    raise ToolError(f"{name!r} is not a block that takes routes; those are {', '.join(n for n, x in core.blocks.items() if x.supports_routes) or 'none'}")
                return b
            routable = [b for b in core.blocks.values() if b.supports_routes]
            if not routable:
                raise ToolError("no configured block takes routes")
            return routable[0]

        @mcp.tool(title="List routes", description="Where send goes without to, per agent and project. Resolution: agent+project, then project, then agent, then global.", annotations=READ)
        async def routes_list(ctx: Context) -> dict[str, Any]:
            who(ctx)
            return {"routes": [r.as_dict() for r in core.routes.list()]}

        @mcp.tool(
            title="Set a route",
            description="Point an agent (claude, chatgpt, claude-code, or a key name), a project, both, or neither (global) at a target: an address like 'capy:captain:<code, id or name>', or the short forms 'captain:<x>', 'project:<Capy project id or code>' and 'thread:<jam_id>'. allow_override lets callers pass an explicit to. Audited.",
            annotations=WRITE_IDEMPOTENT,
        )
        async def routes_set(ctx: Context, target: str, project: str | None = None, agent: str | None = None, defaults: RouteDefaults | None = None,
                             allow_override: bool = False, note: str = "") -> dict[str, Any]:
            p = who(ctx)
            unrestricted(p, "change routes")
            name, parsed = route_target(target)
            return await run(core.set_route(p, destination=name, agent=agent, project=project, target=parsed,
                                            defaults=defaults.model_dump(exclude_none=True) if defaults else {}, allow_override=allow_override, note=note))

        def route_target(text: str) -> tuple[str, dict[str, Any]]:
            head = (text or "").strip().partition(":")[0].lower()
            b = core.blocks.get(head) if head in core.blocks else None
            if b is not None:
                return b.name, b.parse_route_target(text.strip()[len(head) + 1:])
            owner = core._kind_owner(head) if head in LEGACY_KINDS else None
            if owner is not None:
                b = core.blocks[owner]
                return b.name, b.parse_route_target(text.strip())
            b = routing_block(None)
            return b.name, b.parse_route_target(text)

        @mcp.tool(title="Remove a route", description="Remove the route for exactly this agent/project pair (both empty: the global route). Audited.", annotations=DELETE)
        async def routes_remove(ctx: Context, project: str | None = None, agent: str | None = None, block: str | None = None) -> dict[str, Any]:
            p = who(ctx)
            unrestricted(p, "change routes")
            return sync(core.remove_route, p, destination=routing_block(block).name, agent=agent, project=project)

        @mcp.tool(title="Explain a route", description="Which route send would use for an agent (default: you) and project, and the levels checked.", annotations=READ)
        async def routes_resolve(ctx: Context, project: str | None = None, agent: str | None = None, block: str | None = None) -> dict[str, Any]:
            p = who(ctx)
            b = routing_block(block)
            r, tried = core.routes.resolve(destination=b.name, agent=agent or p.source, project=project)
            addr = b.route_address(r.target) if r else None
            return {"agent": agent or p.source, "project": project, "checked": tried, "route": r.as_dict() if r else None, "to": str(addr) if addr else None}

        @mcp.tool(title="Switchboard status", description="Who you are signed in as (your agent name for routes), your wait limits, the blocks and their levels, and what is configured or degraded.", annotations=READ)
        async def switchboard_status(ctx: Context) -> dict[str, Any]:
            p = who(ctx)
            default, mx = self.wait_caps.for_agent(p.source)
            return {"you": {"agent": p.source, "auth": p.kind, "origin": p.origin, **({"scopes": list(p.scopes)} if p.scopes is not None else {})},
                    "wait": {"default_s": default, "max_s": mx}, "capy_wait": {"default_s": default, "max_s": mx}, **core.health(),
                    "agents": core.known_agents(), "connected": sorted(set(core.connections.agents()))}

        kit = Kit(self, who, run, need, pscope, unrestricted)
        kit.register_generic(mcp)
        from .setup import register

        register(mcp, kit)
        for b in core.blocks.values():
            b.mcp_tools(mcp, kit)
        return mcp



from .kit import Kit  # noqa: E402
