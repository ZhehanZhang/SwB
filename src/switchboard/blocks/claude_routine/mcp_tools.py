"""The claude-routine block's MCP tools: routine_list, routine_add and routine_remove. Annotations are evaluated eagerly
(no __future__ import) because the argument types come from the MCP block's kit at registration time."""


from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

if TYPE_CHECKING:
    from . import ClaudeRoutineBlock


def register(block: "ClaudeRoutineBlock", mcp: MCPServer, kit: Any) -> None:
    bname = block.name

    @mcp.tool(title="Claude routines", description="Claude Code routines switchboard can wake: general or for one switchboard project (a Claude Project's task), with status (untested until the first fire) and visibility. Tokens are never shown.", annotations=kit.READ)
    async def routine_list(ctx: Context) -> dict[str, Any]:
        return {"routines": await kit.invoke(ctx, bname, "list")}

    @mcp.tool(title="Add a Claude routine", description="Owner only. Register a Claude Code routine from its fire URL (…/routines/trig_…/fire) or trig_ id and its token, for a switchboard project (a Claude Project's API-triggered task) or general. The token is encrypted at rest and never shown again. Prefer the root page's form so the token stays out of chats. Audited.", annotations=kit.WRITE_IDEMPOTENT)
    async def routine_add(ctx: Context, fire_url: str, token: str, project: Annotated[str | None, Field(description="switchboard project slug; omit for the general routine")] = None,
                          name: Annotated[str | None, Field(description="optional; default claude-<project> or claude")] = None) -> dict[str, Any]:
        kit.owner(ctx, "add routines")
        return await kit.invoke(ctx, bname, "add", fire_url=fire_url, token=token, project=project, name=name)

    @mcp.tool(title="Remove a Claude routine", description="Owner only. Forget a routine added with routine_add or the root page (config ones are removed in the config file). Audited.", annotations=kit.DELETE)
    async def routine_remove(ctx: Context, name: str) -> dict[str, Any]:
        kit.owner(ctx, "remove routines")
        return await kit.invoke(ctx, bname, "remove", name=name)
