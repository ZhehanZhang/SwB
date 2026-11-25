from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .capy_api import CapyError
from .memory import MemoryError_
from .oauth import Principal

if TYPE_CHECKING:
    from .core import Bridge

PRINCIPAL_SCOPE = "capy_bridge.principal"

INSTRUCTIONS = """capy-bridge is the owner's shared workspace across Claude, ChatGPT and Capy.
Memory: one store every assistant reads and writes. Call context_pack at the start of a session.
Save durable facts, preferences and decisions with memory_write (namespaces: profile, decisions,
projects/<name>, notes); search before asking the owner something he may already have said.
Offload: Capy is a coding agent with cloud dev machines, git, and PRs. Use capy_delegate for work
that needs a real machine or repo (code changes, PRs, running tests, deploys, research that takes
more than a few minutes), then follow it with capy_status and capy_result. Don't delegate quick
questions you can answer yourself."""

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_REMOTE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

Namespace = Annotated[str, Field(description="profile, decisions, notes, or projects/<name>; lowercase, up to 3 levels")]


class FileIn(BaseModel):
    name: str = Field(description="File name, e.g. spec.md")
    text: str | None = Field(default=None, description="File content as text")
    base64: str | None = Field(default=None, description="File content as base64, for binary files")
    media_type: str | None = Field(default=None, description="MIME type, e.g. text/markdown")


def build_mcp(bridge: "Bridge") -> MCPServer:
    mcp = MCPServer(name="capy-bridge", instructions=INSTRUCTIONS)

    def who(ctx: Context) -> Principal:
        request = ctx.request_context.request
        p = request.scope.get(PRINCIPAL_SCOPE) if request is not None else None
        if not isinstance(p, Principal):
            raise ToolError("unauthenticated")
        return p

    async def run(coro: Any) -> Any:
        from .core import BridgeError

        try:
            return await coro
        except (BridgeError, MemoryError_) as e:
            raise ToolError(str(e)) from e
        except CapyError as e:
            raise ToolError(f"Capy API error: {e}") from e

    def sync(fn: Any, *a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except MemoryError_ as e:
            raise ToolError(str(e)) from e

    # Memory ---------------------------------------------------------------------------

    @mcp.tool(
        title="Save to shared memory",
        description="Save or update a durable memory shared with every assistant and Capy. Use a key for entries you will update (e.g. profile/editor). mode=append adds to the existing body. The writing assistant is recorded automatically.",
        annotations=WRITE,
    )
    async def memory_write(
        ctx: Context,
        namespace: Namespace,
        title: Annotated[str, Field(description="Short title")],
        body: Annotated[str, Field(description="Markdown content, max 20k chars")],
        key: Annotated[str | None, Field(description="Optional stable slug; writing the same namespace+key updates that entry")] = None,
        tags: Annotated[list[str] | None, Field(description="Optional tags")] = None,
        id: Annotated[str | None, Field(description="Update the entry with this id instead")] = None,
        mode: Literal["replace", "append"] = "replace",
    ) -> dict[str, Any]:
        return sync(bridge.memory.write, source=who(ctx).source, namespace=namespace, title=title, body=body, key=key, tags=tags, id=id, mode=mode)

    @mcp.tool(title="Search shared memory", description="Full-text search over shared memory. Optionally limit to a namespace prefix such as projects/capy-bridge.", annotations=READ)
    async def memory_search(
        ctx: Context,
        query: str,
        namespace: Annotated[str | None, Field(description="Namespace prefix filter")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        who(ctx)
        return sync(bridge.memory.search, query=query, namespace=namespace, limit=limit)

    @mcp.tool(title="Read a memory", description="Read one memory in full by id, or by namespace and key, with its recent edit history.", annotations=READ)
    async def memory_read(ctx: Context, id: str | None = None, namespace: str | None = None, key: str | None = None) -> dict[str, Any]:
        who(ctx)
        return sync(bridge.memory.read, id=id, namespace=namespace, key=key)

    @mcp.tool(title="List memories", description="List memories newest first, with per-namespace counts. Optionally filter by a namespace prefix.", annotations=READ)
    async def memory_list(
        ctx: Context,
        namespace: str | None = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        who(ctx)
        return sync(bridge.memory.list, namespace=namespace, limit=limit, offset=offset)

    @mcp.tool(title="Delete a memory", description="Delete a memory by id when it is wrong or obsolete. History is kept server-side.", annotations=DELETE)
    async def memory_delete(ctx: Context, id: str) -> dict[str, Any]:
        return sync(bridge.memory.delete, source=who(ctx).source, id=id)

    @mcp.tool(
        title="Session briefing",
        description="Compact briefing to read at the start of a session: the owner's profile, recent decisions, project notes, active Capy work and open asks. Pass project to include projects/<project>.",
        annotations=READ_REMOTE,
    )
    async def context_pack(ctx: Context, project: str | None = None) -> str:
        who(ctx)
        return await run(bridge.context_pack(project))

    # Capy offload ------------------------------------------------------------------------

    @mcp.tool(
        title="Delegate to Capy",
        description=(
            "Hand a task to Capy, a coding agent with cloud machines, git and PRs, when it needs a real machine, a repo, a PR, or "
            "long-running work. target='captain' (default) sends it to the owner's Captain, who plans and staffs it; a project alias "
            "starts a new thread there (crew=true puts it under the Captain). Write the task self-contained: goal, context, repos, what done means. "
            "Returns a thread_id; follow up with capy_status and capy_result. Limited per day."
        ),
        annotations=SEND,
    )
    async def capy_delegate(
        ctx: Context,
        task: Annotated[str, Field(description="Self-contained task description in markdown")],
        title: Annotated[str | None, Field(description="Short thread title (project targets)")] = None,
        target: Annotated[str | None, Field(description="'captain' or a project alias from bridge_status")] = None,
        repos: Annotated[list[str] | None, Field(description="owner/repo names the task concerns; must be allowlisted")] = None,
        files: Annotated[list[FileIn] | None, Field(description="Optional files to attach")] = None,
        crew: Annotated[bool, Field(description="For a project target: create the thread in the Captain's crew")] = False,
    ) -> dict[str, Any]:
        return await run(
            bridge.delegate(who(ctx), task=task, title=title, target=target, repos=repos, files=[f.model_dump() for f in files] if files else None, crew=crew)
        )

    @mcp.tool(title="Capy thread status", description="Status of a Capy thread: working/waiting/idle/failed, whether it needs the owner, its PR links, and its latest messages.", annotations=READ_REMOTE)
    async def capy_status(
        ctx: Context,
        thread_id: str,
        messages: Annotated[int, Field(ge=1, le=20)] = 5,
        include_tools: Annotated[bool, Field(description="Include one-line tool activity entries")] = False,
    ) -> dict[str, Any]:
        who(ctx)
        return await run(bridge.status(thread_id, messages, include_tools))

    @mcp.tool(
        title="List Capy work",
        description="List Capy threads: scope 'delegated' (tasks sent through this bridge, with live status), 'captain' (the Captain's crew), or 'project' (recent threads in a project alias).",
        annotations=READ_REMOTE,
    )
    async def capy_list(
        ctx: Context,
        scope: Literal["delegated", "captain", "project"] = "delegated",
        project: str | None = None,
        limit: Annotated[int, Field(ge=1, le=30)] = 10,
    ) -> dict[str, Any]:
        who(ctx)
        return await run(bridge.list(scope, project, limit))

    @mcp.tool(
        title="Reply to a Capy thread",
        description="Send a follow-up into a Capy thread: answer its question, add context, or redirect it. delivery: steer (default, lands at its next step), queue (after current work), interrupt (stop and read now).",
        annotations=SEND,
    )
    async def capy_reply(ctx: Context, thread_id: str, text: str, delivery: Literal["steer", "queue", "interrupt"] = "steer") -> dict[str, Any]:
        return await run(bridge.reply(who(ctx), thread_id, text, delivery))

    @mcp.tool(
        title="Capy thread result",
        description="The latest final reply of a Capy thread in full, its PR links, and download links for files it delivered (links expire). tail>0 adds that many recent transcript messages.",
        annotations=READ_REMOTE,
    )
    async def capy_result(ctx: Context, thread_id: str, tail: Annotated[int, Field(ge=0, le=30)] = 0) -> dict[str, Any]:
        who(ctx)
        return await run(bridge.result(thread_id, tail))

    @mcp.tool(
        title="Captain digest",
        description="Read a Captain's digest (its DIGEST.md, ONGOING.md or NOTES.md) plus crew threads that need the owner. Defaults to the configured Captain; pass a Captain name or jam_ id for another.",
        annotations=READ_REMOTE,
    )
    async def capy_captain_digest(ctx: Context, captain: str | None = None) -> dict[str, Any]:
        who(ctx)
        return await run(bridge.captain_digest(captain))

    @mcp.tool(title="Bridge status", description="Who you are signed in as, what this bridge allows (targets, repos, daily cap), and anything misconfigured.", annotations=READ)
    async def bridge_status(ctx: Context) -> dict[str, Any]:
        p = who(ctx)
        cfg = bridge.cfg
        return {
            "you": {"source": p.source, "auth": p.kind},
            **bridge.health(),
            "targets": ["captain", *cfg.projects],
            "default_target": cfg.default_target,
            "repos": {a: list(pr.repos) for a, pr in cfg.projects.items()},
            "delegations_today": f"{bridge.used_today()}/{cfg.daily_cap}",
        }

    return mcp
