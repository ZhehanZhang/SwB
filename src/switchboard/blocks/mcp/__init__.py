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
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, InputRequiredResult, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator
from starlette.routing import BaseRoute, Mount

from ... import __version__
from ...auth import Principal
from ...memory import GENERAL, MemoryError_
from ...modes import ChannelModes
from ...projects import Project, ProjectError
from ...refusals import PROJECT_LINK_REQUIRED, ROUTE_TARGET_KIND, PolicyRefusal
from ...routes import RouteError
from ...targets import LEGACY_KINDS
from .. import Block, Manifest
from .events import EventsExtension, advertise_events
from .policy import (BLOCK_ROLES, CONVERSATION_CONFLICT, CONVERSATION_REQUIRED, MEMORY_NOT_FOUND, OWNER_REQUIRED, READ_ROLES,
                     ROUTE_REQUIRED, SCOPE_REQUIRED, TARGET_REQUIRED, TOOL_REQUIRED, Role, can_read_project, catalog_refusal,
                     default_memory_scopes, granted_memory_scopes, mutation_roles, read_refusal)
from .tasks import TasksExtension

if TYPE_CHECKING:
    from ...core import Switchboard

PRINCIPAL_SCOPE = "switchboard.principal"
TASK_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar("switchboard_task_mode", default=False)

CORE_INSTRUCTIONS = """SwB is a shared workspace for your assistants and agents.
Use project_list to find your project by name or repository. Call context_pack with the project at the start of a session.
Write project facts to project:<slug> and pass project=<slug> to check the scope.
Use agent:<name> for memory in your agent's scope. New paired keys get their own agent scope and any project named in the pairing.
Use general for facts that apply to all projects, such as the owner's preferences.
For general writes, get the owner's agreement and pass confirm_general=true.
Read within your grants. Pass project=<slug> for a project or scopes=[...] to select exact scopes. Reads include general only when granted.
Use the same credential that wrote an entry to update, move or delete it. The owner can manage any entry and promote it to general.
Call connect for setup and follow its steps.
Use send to hand off work or message an agent. The owner's route for you and the project is the default.
For to, use a slug from targets, an agent mailbox such as agent:claude, or a block address <block>:<kind>:<id>.
SwB keeps messages in the agent's mailbox until the agent reads them.
Replies reach your inbox with in_reply_to set to the conversation from send.
Call wait for the reply. If wait times out, check again with wait or inbox.
Answer a message with reply(text, conversation)."""


READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_REMOTE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

Scope = Annotated[str, Field(description="A granted scope: 'project:<slug>', 'agent:<name>' or 'general'")]
ProjectArg = Annotated[str | None, Field(description="Project slug (project_list). Memory reads add general only when granted")]
Namespace = Annotated[str, Field(description="Inside the scope: profile, decisions, notes, or e.g. notes/meetings; lowercase, up to 3 levels")]
ScopesArg = Annotated[list[str] | None, Field(description="Exact granted scopes, e.g. ['agent:notes-bot'] or ['project:example', 'general']")]


class FileIn(BaseModel):
    name: str = Field(description="File name, e.g. spec.md")
    text: str | None = Field(default=None, description="File content as text")
    base64: str | None = Field(default=None, description="File content as base64, for binary files")
    media_type: str | None = Field(default=None, description="MIME type, e.g. text/markdown")


class ProjectLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def supported_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.keys() - cls.model_fields.keys():
            raise PolicyRefusal(PROJECT_LINK_REQUIRED)
        return data


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
    """Apply the same tool and role policy to discovery and dispatch."""

    modes: ChannelModes
    roles: dict[str, Role]
    github_actions: dict[str, str]

    def _audit_refusal(self, p: Principal | None, tool: str | None, reason: str) -> None:
        self.modes.core.audit.record(origin=p.origin if p else "mcp", source=p.source if p else "-",
                                     client=p.client_id if p else "-", destination="mcp", action="tool_refused",
                                     outcome="refused", target=tool, detail=reason)

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Context | None = None) -> CallToolResult | InputRequiredResult:
        request_context = getattr(context, "request_context", None)
        p = principal_of(getattr(request_context, "request", None))
        tool = next((t for t in await self.list_tools() if t.name == name), None)
        role = self.roles.get(name)
        reason = catalog_refusal(p, name, tool, role)
        if reason is None and p is not None:
            if name in self.github_actions and (conversation := arguments.get("conversation")) is not None:
                handle = arguments.get("handle")
                if handle is not None and handle != conversation:
                    reason = CONVERSATION_CONFLICT
                elif isinstance(conversation, str):
                    arguments = {**arguments, "handle": conversation}
                    del arguments["conversation"]
            if reason is None:
                reason = await self._argument_refusal(p, name, role, arguments)
        if reason:
            message = reason
            if reason == OWNER_REQUIRED and p is not None and p.kind == "connect":
                message = f"This key belongs to a connected agent. {reason}"
            elif reason == TOOL_REQUIRED and p is not None:
                message = f"the {p.source} key may call only {', '.join(p.tools or ()) or 'no tools'}"
            refusal = PolicyRefusal(reason, message)
            self._audit_refusal(p, tool.name if tool else None, refusal.reason)
            refusal.audited = True
            raise ToolError(str(refusal)) from refusal
        if role and p is not None and name not in ("wait", "inbox", "capy_wait", "capy_inbox", "slack_wait"):
            self._check_mode(p, name, arguments)
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as exc:
            cause: BaseException | None = exc
            while cause is not None:
                if isinstance(cause, PolicyRefusal):
                    if not cause.audited:
                        self._audit_refusal(p, name, cause.reason)
                        cause.audited = True
                    raise ToolError(str(cause)) from cause
                cause = cause.__cause__
            if isinstance(exc, UnexpectedToolError) or not isinstance(error := exc.__cause__, ValidationError):
                raise
            properties = (self._tool_input_schema(name) or {}).get("properties", {})
            parts, missing, invalid = [], [], []
            for issue in error.errors(include_input=False, include_context=False, include_url=False):
                loc = issue["loc"]
                if name == "inbox" and loc == ("limit",) and issue["type"] in ("greater_than_equal", "less_than_equal"):
                    limits = properties["limit"]
                    parts.append(f"Use a limit from {limits['minimum']} to {limits['maximum']}.")
                elif loc and loc[0] in properties:
                    (missing if issue["type"] == "missing" else invalid).append(loc[0])
            if missing:
                parts.append(f"Provide {', '.join(dict.fromkeys(missing))}.")
            if invalid:
                parts.append(f"Check {', '.join(dict.fromkeys(invalid))}.")
            raise ToolError(" ".join(parts) or "Check the tool arguments.") from error

    async def _argument_refusal(self, p: Principal, name: str, role: Role | None, arguments: dict[str, Any]) -> str | None:
        if name in READ_ROLES:
            return read_refusal(self.modes.core, p, name, arguments)
        owner = p.is_unrestricted_owner
        if role == "route" and not owner:
            agent = arguments.get("agent")
            if not isinstance(agent, str) or agent.strip().lower() != p.source:
                return ROUTE_REQUIRED
            project = arguments.get("project")
            scope = "project:" + project.strip().lower().removeprefix("project:") if isinstance(project, str) and project.strip() else None
            if not p.allows_routing_scope(scope or "general"):
                return SCOPE_REQUIRED
            if name == "routes_set" and p.targets is not None and isinstance(target := arguments.get("target"), str):
                from ...core import CoreError

                try:
                    await self.modes.core.check_target(p, target, scope.removeprefix("project:") if scope else None)
                except CoreError:
                    return TARGET_REQUIRED
        if role == "self" and name in ("reply", "cancel", "capy_reply", "capy_cancel"):
            ref = arguments.get("conversation" if name in ("reply", "cancel") else "thread_id" if name == "capy_reply" else "handle")
            if isinstance(ref, str):
                ref = ref.strip()
                core = self.modes.core
                if ref.startswith("h_"):
                    handle = core.inbox.handle(ref)
                    if handle and handle["destination"] == "mailbox":
                        if name == "reply":
                            return None if core.inbox.participant(handle, p.source) else CONVERSATION_REQUIRED
                        if name == "cancel":
                            return None if p.source == handle["ref"] else CONVERSATION_REQUIRED
                    own = bool(handle and handle["owner"] == p.source)
                else:
                    own = bool(core.store.one("SELECT 1 FROM handles WHERE owner = ? AND ref = ?", (p.source, ref)))
                if not owner and not own:
                    return CONVERSATION_REQUIRED
        return None

    async def _handle_list_tools(self, ctx: Any, params: Any) -> Any:
        out = await super()._handle_list_tools(ctx, params)
        p = principal_of(getattr(ctx, "request", None))
        out.tools = [t for t in out.tools if catalog_refusal(p, t.name, t, self.roles.get(t.name)) is None]
        return out

    def _check_mode(self, p: Principal, name: str, arguments: dict[str, Any]) -> None:
        repo = None
        if name in self.github_actions:
            action = self.github_actions[name]
            ref, repo = arguments.get("handle"), arguments.get("repo")
        else:
            ref = next((arguments[k] for k in ("conversation", "handle", "forward", "in_reply_to", "ref")
                        if isinstance(arguments.get(k), str)), None)
            action = name
            if (action == "reply" and arguments.get("to_owner") is True) or (action == "agent_send" and ref):
                action = "notify_owner"
            elif action == "send" and arguments.get("forward"):
                action = "forward"
        if action == "notify_owner":
            return
        try:
            self.modes.check(p, ref if isinstance(ref, str) else None, action, repo if isinstance(repo, str) else None)
        except PolicyRefusal as refusal:
            self._audit_refusal(p, name, refusal.reason)
            refusal.audited = True
            raise ToolError(str(refusal)) from refusal


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

    def status(self) -> dict[str, Any]:
        if not (self.core.cfg.public_url or "").startswith("https://"):
            return {"manual_steps": ["Give SwB a public HTTPS address for hosted MCP clients."]}
        return {}

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
        task_destinations = {b.name for b in core.blocks.values() if b.manifest.id == "capy"}
        extensions = [self.events, TasksExtension(self, task_destinations)] if task_destinations else [self.events]
        mcp = LimitedServer(name="swb", title="SwB", version=__version__, instructions=self.instructions(), extensions=extensions)
        mcp.modes = core.modes
        mcp.roles = mutation_roles(core)
        mcp.github_actions = {f"{b.name}_{action}": action for b in core.blocks.values() if b.manifest.id == "github"
                              for action in BLOCK_ROLES["github"]}
        mcp.middleware.append(advertise_events)
        global Links
        link_keys = tuple(dict.fromkeys(core.projects.link_keys))
        Links = create_model("Links", __base__=ProjectLinks,
                             repos=(list[str] | None, Field(default=None, description="owner/repo names that belong to the project")),
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

        def need_routing(p: Principal, scope: str) -> None:
            if not p.allows_routing_scope(scope):
                raise PolicyRefusal(SCOPE_REQUIRED, f"the {p.source} key is limited to {', '.join(p.scopes or ())}; {scope} is outside it")

        def pscope(slug: str) -> str:
            return "project:" + slug.strip().lower().removeprefix("project:")

        def reading(p: Principal, project: str | None, include_general: bool, scopes: list[str] | None, what: str) -> tuple[list[str], str | None]:
            note = None
            if scopes:
                granted = granted_memory_scopes(core, p, scopes)
                if not granted:
                    raise PolicyRefusal(MEMORY_NOT_FOUND)
                ss = sync(core.memory.explicit_scopes, granted, project)
                if len(granted) < len(scopes):
                    note = "Skipped scopes outside this key's grants."
            elif not project:
                ss = default_memory_scopes(core, p)
            else:
                ss = granted_memory_scopes(core, p, sync(core.memory.read_scopes, project, include_general))
            if not ss:
                raise PolicyRefusal(MEMORY_NOT_FOUND)
            cross = [s for s in ss if s != GENERAL and (not project or s != pscope(project))]
            if cross:
                core.audit_memory(p, "cross_read", ",".join(cross), f"{what} while working in {project or 'general'}")
            return ss, note

        async def run(coro: Any) -> Any:
            from ...blocks import BlockError as DestinationError
            from ...core import CoreError

            try:
                return await coro
            except (CoreError, DestinationError, MemoryError_, ProjectError, RouteError, PermissionError) as e:
                raise ToolError(str(e)) from e

        @mcp.tool(title="Channel modes", description="Read the modes and targets for channels and repositories you can see. See which agent handles triage.", annotations=READ)
        async def modes_status(ctx: Context) -> dict[str, Any]:
            p = who(ctx)
            await core.modes.refresh(p)
            return core.modes.status(p, slugs=await core.modes.status_slugs(p))

        @mcp.tool(title="Set a channel mode", description="Owner only. Set a space or channel override. Choose a target listed by targets, such as agent:beta. Omit target to keep the binding. Pass an empty target to clear it. Use inherit to clear the override. The triage agent can reply by default.", annotations=WRITE)
        async def space_mode(ctx: Context, block: str, space: str, mode: str, channel: str = "",
                             actions: list[str] | None = None, project: str | None = None, target: str | None = None) -> dict[str, Any]:
            try:
                return await core.modes.set(who(ctx), block, space, mode, channel, actions, project, target)
            except (ValueError, PermissionError) as e:
                raise ToolError(str(e)) from e

        # Memory and projects ------------------------------------------------------------------

        @mcp.tool(
            title="Save to shared memory",
            description="Save memory in a granted scope. Use 'project:<slug>' for project facts or 'agent:<name>' for agent memory. With a general grant, pass confirm_general=true after the owner agrees. Update an entry you wrote by ID or by scope, namespace and key. The owner can update any entry.",
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
            return sync(core.write_memory, who(ctx), scope=scope, namespace=namespace, title=title, body=body, key=key, tags=tags, id=id,
                        mode=mode, confirm_general=confirm_general, project=project)

        @mcp.tool(title="Search shared memory", description="Search granted scopes. Pass project for that project, or scopes for exact scopes such as agent:<name>. General is included only when granted. For a private project, use its project name.", annotations=READ)
        async def memory_search(
            ctx: Context,
            query: str,
            project: ProjectArg = None,
            include_general: bool = True,
            namespace: Annotated[str | None, Field(description="Namespace prefix filter")] = None,
            limit: Annotated[int, Field(ge=1, le=50)] = 10,
            scopes: ScopesArg = None,
        ) -> dict[str, Any]:
            ss, note = reading(who(ctx), project, include_general, scopes, f"search {query!r}")
            out = sync(core.memory.search, query=query, scopes=ss, namespace=namespace, limit=limit)
            return {**out, "note": note} if note else out

        @mcp.tool(title="Read a memory", description="Read one memory in full by id, or by scope, namespace and key, with its recent edit history.", annotations=READ)
        async def memory_read(ctx: Context, id: str | None = None, scope: str | None = None, namespace: str | None = None, key: str | None = None) -> dict[str, Any]:
            p = who(ctx)
            out = sync(core.memory.read, id=id, scope=scope, namespace=namespace, key=key)
            sync(core.check_memory_scope, p, out["scope"])
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
            ss, note = reading(who(ctx), project, include_general, scopes, "list")
            out = sync(core.memory.list, scopes=ss, namespace=namespace, limit=limit, offset=offset)
            return {**out, "note": note} if note else out

        @mcp.tool(title="Delete a memory", description="Delete an entry you wrote within your grants. The owner can delete any entry. SwB keeps the history.", annotations=DELETE)
        async def memory_delete(ctx: Context, id: str) -> dict[str, Any]:
            return sync(core.delete_memory, who(ctx), id)

        @mcp.tool(title="Move a memory to another scope", description="Move an entry you wrote between granted project or agent scopes. The owner can move any entry. SwB keeps its ID and history.", annotations=WRITE_IDEMPOTENT)
        async def memory_move(ctx: Context, id: str, to_scope: Annotated[str, Field(description="'project:<slug>' or 'agent:<name>'")]) -> dict[str, Any]:
            return sync(core.move_memory, who(ctx), id, to_scope)

        @mcp.tool(title="Promote a memory to general", description="Use the owner's sign-in to move an entry to general. SwB keeps its ID and history.", annotations=WRITE_IDEMPOTENT)
        async def memory_promote(ctx: Context, id: str, to: Literal["general"] = "general") -> dict[str, Any]:
            return sync(core.move_memory, who(ctx), id, to, promote=True)

        @mcp.tool(
            title="Session briefing",
            description="Read granted memory and your conversations in progress. Pass a project for its links or scopes to select memory. Paired keys default to their grants. The owner's sign-in includes the routed Captain's digest and crew activity.",
            annotations=READ_REMOTE,
        )
        async def context_pack(ctx: Context, project: ProjectArg = None, scopes: ScopesArg = None) -> str:
            p = who(ctx)
            ss, note = reading(p, project, True, scopes, "briefing")
            out = await run(core.context_pack(p, project, ss))
            return f"{out}\n\n{note}" if note else out

        @mcp.tool(title="List projects", description="List public projects and private projects you have a named grant to see. The owner sees every project. Pass hint to find a project by repository or name. pending_projects lists new slugs waiting for a routine registration.", annotations=READ)
        async def project_list(ctx: Context, include_archived: bool = False, hint: str | None = None) -> dict[str, Any]:
            p = who(ctx)
            block = next((b for b in core.blocks.values() if b.manifest.id == "claude-routine"), None)
            pending = block.pending_projects() if block is not None else []

            def visible(project: Project) -> bool:
                return can_read_project(p, project)

            if hint:
                return {"matches": sync(core.projects.resolve, hint, visible=visible), "pending_projects": pending}
            return {"projects": [project.as_dict() for project in core.projects.list(include_archived) if visible(project)],
                    "pending_projects": pending}

        @mcp.tool(title="Create a project", description="Create a project, a memory scope 'project:<slug>'. Do this only when the owner starts a genuinely new project. Pass its repos: switchboard then links it to its counterpart in each block (a Capy project, say) and routes it (result.auto); ambiguous matches come back as candidates to ask the owner about.", annotations=WRITE)
        async def project_create(ctx: Context, slug: Annotated[str, Field(description="lowercase-dashed, e.g. switchboard")], name: str,
                                 description: str = "", links: Links | None = None,
                                 private: Annotated[bool, Field(description="Require a named project grant for non-owner reads")] = False) -> dict[str, Any]:
            p = who(ctx)
            out = sync(core.projects.create, slug=slug, name=name, description=description, links=links.model_dump(exclude_none=True) if links else None,
                       private=private).as_dict()
            core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination="core",
                              action="project_create", outcome="ok", target=out["slug"], detail="Created project.")
            out["auto"] = await run(core.project_sync(p, out["slug"]))
            return out

        @mcp.tool(title="Update a project", description="Rename (new_slug moves its memories and routes), describe, relink, archive or restore a project, or mark it private. links merge: a field set to empty clears it. While a block's link is empty, it tries to link and route the project (result.auto).", annotations=WRITE_IDEMPOTENT)
        async def project_update(ctx: Context, slug: str, name: str | None = None, description: str | None = None, links: Links | None = None,
                                 new_slug: str | None = None, archived: bool | None = None, private: bool | None = None,
                                 force: Annotated[bool, Field(description="Let the automatic link and route replace existing ones")] = False) -> dict[str, Any]:
            p = who(ctx)
            out = sync(core.projects.update, slug, name=name, description=description, links=links.model_dump(exclude_unset=True) if links else None,
                       new_slug=new_slug, archived=archived, private=private).as_dict()
            changes = [
                "Renamed project." if name is not None or new_slug is not None else None,
                "Updated description." if description is not None else None,
                "Updated links." if links is not None else None,
                ("Archived project." if archived else "Restored project.") if archived is not None else None,
                ("Set project to private." if private else "Set project privacy to standard.") if private is not None else None,
            ]
            core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination="core",
                              action="project_update", outcome="ok", target=out["slug"],
                              detail=" ".join(change for change in changes if change) or "Updated project.")
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
            return await run(core.project_sync(p, slug, force=force))

        # Routes ---------------------------------------------------------------------------

        def route_destinations(block: str | None) -> str | tuple[str, ...]:
            names = core.route_destinations()
            if not block:
                return names
            if block not in names:
                raise ToolError(f"Choose a routing destination from {', '.join(names)}.")
            return block

        @mcp.tool(title="List routes", description="Where send goes without to, per agent and project. Resolution: agent+project, then project, then agent, then global.", annotations=READ)
        async def routes_list(ctx: Context) -> dict[str, Any]:
            who(ctx)
            return {"routes": [r.as_dict() for r in core.routes.list()]}

        @mcp.tool(
            title="Set a route",
            description="Route sends from an agent, a project, both, or all callers. Use agent:<name> for a mailbox or a block address such as capy:captain:<name>. For Capy, allow_override lets callers pass an explicit target. Audited.",
            annotations=WRITE_IDEMPOTENT,
        )
        async def routes_set(ctx: Context, target: str, project: str | None = None, agent: str | None = None, defaults: RouteDefaults | None = None,
                             allow_override: bool = False, note: str = "") -> dict[str, Any]:
            p = who(ctx)
            name, parsed = route_target(target)
            return await run(core.set_route(p, destination=name, agent=agent, project=project, target=parsed,
                                            defaults=defaults.model_dump(exclude_none=True) if defaults else {}, allow_override=allow_override, note=note))

        def route_target(text: str) -> tuple[str, dict[str, Any]]:
            text = text.strip()
            prefix, sep, rest = text.partition(":")
            head = prefix.lower()
            if head == "agent" and sep:
                return "agent", {"agent": rest}
            b = core.blocks.get(head)
            if b is not None:
                route_destinations(b.name)
                kind, kind_sep, _ = rest.partition(":")
                if kind_sep and not any(k.kind == kind.strip().lower() for k in b.manifest.targets):
                    raise PolicyRefusal(ROUTE_TARGET_KIND, f"Choose a supported route target kind for {text[:120]!r}.")
                return b.name, b.parse_route_target(rest)
            owner = core._kind_owner(head) if head in LEGACY_KINDS else None
            if owner is not None:
                b = core.blocks[owner]
                return b.name, b.parse_route_target(text)
            if sep:
                raise PolicyRefusal(ROUTE_TARGET_KIND, f"Choose a supported route target kind for {text[:120]!r}.")
            name = core.route_destinations()[0]
            if name == "agent":
                raise ToolError("Use agent:<name> for a mailbox or enable a block that takes routes.")
            b = core.blocks[name]
            return b.name, b.parse_route_target(text)

        @mcp.tool(title="Remove a route", description="Remove the route for this exact agent and project. Omit both for the global route. Pass block to select a destination when several match. Audited.", annotations=DELETE)
        async def routes_remove(ctx: Context, project: str | None = None, agent: str | None = None, block: str | None = None) -> dict[str, Any]:
            p = who(ctx)
            return sync(core.remove_route, p, destination=route_destinations(block), agent=agent, project=project)

        @mcp.tool(title="Explain a route", description="Show the route that send would use for an agent and project. The agent defaults to you. Pass block to check one destination.", annotations=READ)
        async def routes_resolve(ctx: Context, project: str | None = None, agent: str | None = None, block: str | None = None) -> dict[str, Any]:
            p = who(ctx)
            r, tried = core.routes.resolve(destination=route_destinations(block), agent=agent or p.source, project=project)
            addr = core.address_for_route(r) if r else None
            return {"agent": agent or p.source, "project": project, "checked": tried, "route": r.as_dict() if r else None, "to": str(addr) if addr else None}

        @mcp.tool(title="SwB status", description="Who you are signed in as (your agent name for routes), your wait limits, the blocks and their levels, and what is configured or degraded.", annotations=READ)
        async def switchboard_status(ctx: Context) -> dict[str, Any]:
            p = who(ctx)
            await core.modes.refresh(p)
            default, mx = self.wait_caps.for_agent(p.source)
            return {"you": {"agent": p.source, "auth": p.kind, "origin": p.origin, **({"scopes": list(p.scopes)} if p.scopes is not None else {})},
                    "wait": {"default_s": default, "max_s": mx}, "capy_wait": {"default_s": default, "max_s": mx},
                    **core.health(p, mode_slugs=await core.modes.status_slugs(p)),
                    "agents": core.known_agents(),
                    "connected": (sorted(set(core.connections.agents())) if p.is_unrestricted_owner else [p.source] if p.kind == "connect" else [])}

        kit = Kit(self, who, run, need_routing, pscope)
        kit.register_generic(mcp)
        from .setup import register

        register(mcp, kit)
        for b in core.blocks.values():
            b.mcp_tools(mcp, kit)
        return mcp



from .kit import Kit  # noqa: E402
