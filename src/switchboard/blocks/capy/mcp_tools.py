"""The capy block's own MCP tools (capy_captain_digest, capy_captains, capy_list) and the deprecated capy_* aliases of
the generic verbs, which this release still lists. Annotations are evaluated eagerly (no __future__ import) because
the argument types come from the MCP block's kit at registration time."""


from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

if TYPE_CHECKING:
    from . import CapyBlock


def register(block: "CapyBlock", mcp: MCPServer, kit: Any) -> None:
    name = block.name
    ProjectArg, FileIn = kit.ProjectArg, kit.FileIn

    @mcp.tool(title="List Capy work", description="scope 'delegated' (work sent to Capy through switchboard, with live status), 'captain' (a Captain's crew; default the routed one), or 'project' (threads in a project's linked Capy project).", annotations=kit.READ_REMOTE)
    async def capy_list(ctx: Context, scope: Literal["delegated", "captain", "project"] = "delegated", project: str | None = None, captain: str | None = None,
                        limit: Annotated[int, Field(ge=1, le=30)] = 10) -> dict[str, Any]:
        return await kit.invoke(ctx, name, "list", scope=scope, project=project, captain=captain, limit=limit)

    @mcp.tool(title="Captain digest", description="Read a Captain digest and crew activity with the owner's sign-in. The Captain defaults to your project route.", annotations=kit.READ_REMOTE)
    async def capy_captain_digest(ctx: Context, captain: str | None = None, project: str | None = None) -> dict[str, Any]:
        return await kit.invoke(ctx, name, "captain_digest", captain=captain, project=project)

    @mcp.tool(title="List Captains", description="Captains the Capy key can see, with whether each is routed; use them in routes_set or send(to='capy:captain:<name>').", annotations=kit.READ_REMOTE)
    async def capy_captains(ctx: Context) -> dict[str, Any]:
        return await kit.invoke(ctx, name, "captains")

    if not kit.aliases:
        return

    @mcp.tool(
        title="Delegate to Capy",
        description=kit.deprecated("send", "Hand work to the project's Capy Captain; cheap, so delegate freely and follow up with capy_reply. It goes where "
                                   "your route points (routes_resolve). Write the task self-contained, then call capy_wait() and answer with the reply."),
        annotations=kit.SEND,
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
        from . import parse_target

        p = kit.alias(ctx, "capy_delegate")
        kit.scoped(p, project)
        to = None
        if target:
            kind, value = next(iter(parse_target(target).items()))
            to = f"{name}:{kind}:{value}"
        return await kit.send(p, wait_s=wait_s, task=task, to=to or name, project=project, title=title, repos=repos,
                              files=[f.model_dump() for f in files] if files else None, new_conversation=new_thread)

    @mcp.tool(title="Wait for Capy", description=kit.deprecated("wait", "Wait for replies on your delegations, or messages another agent left you; returns as soon as something arrives, or empty when timeout_s runs out. Call it with no arguments; handle only narrows it."), annotations=kit.READ)
    async def capy_wait(ctx: Context, handle: str | None = None, timeout_s: Annotated[float | None, Field(ge=0)] = None, project: ProjectArg = None) -> dict[str, Any]:
        return await kit.wait(kit.alias(ctx, "capy_wait"), handle=handle, timeout_s=timeout_s, project=project)

    @mcp.tool(title="Capy inbox", description=kit.deprecated("inbox", "What Capy reported on your delegations, and messages other agents left you, since since_cursor (default: since you last read). Never blocks."), annotations=kit.READ)
    async def capy_inbox(ctx: Context, since_cursor: int | None = None, handle: str | None = None, limit: Annotated[int, Field(ge=1, le=100)] = 50,
                         project: ProjectArg = None) -> dict[str, Any]:
        out = kit.inbox(kit.alias(ctx, "capy_inbox"), since_cursor=since_cursor, handle=handle, limit=limit, project=project, block=name)
        out["open"] = [{k: o[k] for k in ("handle", "thread_id", "status", "title")} for o in out["open"]]
        return out

    @mcp.tool(title="Capy thread status", description=kit.deprecated("status", "Status of a Capy thread (by thread id or handle): working/waiting/idle/failed, whether it needs the owner, its PRs and latest messages."), annotations=kit.READ_REMOTE)
    async def capy_status(ctx: Context, thread_id: Annotated[str, Field(description="jam_ thread id or h_ handle")], messages: Annotated[int, Field(ge=1, le=20)] = 5,
                          include_tools: bool = False) -> dict[str, Any]:
        kit.alias(ctx, "capy_status")
        return await kit.invoke(ctx, name, "status", thread_id=thread_id, messages=messages, include_tools=include_tools)

    @mcp.tool(title="Reply to a Capy thread", description=kit.deprecated("reply", "Send a follow-up into a Capy thread (thread id or handle): answer its question, add context, or redirect it. delivery: steer (default), queue, interrupt."), annotations=kit.SEND)
    async def capy_reply(ctx: Context, thread_id: str, text: str, delivery: Literal["steer", "queue", "interrupt"] = "steer") -> dict[str, Any]:
        kit.alias(ctx, "capy_reply")
        return await kit.invoke(ctx, name, "reply", thread_id=thread_id, text=text, delivery=delivery)

    @mcp.tool(title="Capy thread result", description=kit.deprecated("result", "The latest final reply of a Capy thread (thread id or handle) in full, its PR links, and download links for delivered files (links expire)."), annotations=kit.READ_REMOTE)
    async def capy_result(ctx: Context, thread_id: str, tail: Annotated[int, Field(ge=0, le=30)] = 0) -> dict[str, Any]:
        kit.alias(ctx, "capy_result")
        return await kit.invoke(ctx, name, "result", thread_id=thread_id, tail=tail)

    @mcp.tool(title="Stop following a delegation", description=kit.deprecated("cancel", "Stop reporting on a handle. The Capy thread keeps running; use capy_reply to stop it."), annotations=kit.WRITE_IDEMPOTENT)
    async def capy_cancel(ctx: Context, handle: str) -> dict[str, Any]:
        kit.alias(ctx, "capy_cancel")
        return await kit.invoke(ctx, name, "cancel", handle=handle)
