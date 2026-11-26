"""A chat block's MCP tools: <block>_post, <block>_status and <block>_provision."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ..blocks import BlockError

if TYPE_CHECKING:
    from . import ChatBlock


def register(block: "ChatBlock", mcp: Any, kit: Any) -> None:
    name, title = block.name, block.manifest.title

    async def run(ctx: Context, action: str, **kw: Any) -> Any:
        try:
            return await block.core.invoke(kit.who(ctx), name, block.action(action), kw)
        except BlockError as e:
            raise ToolError(str(e)) from e

    async def post(ctx: Context, space: Annotated[str, Field(description="Space label from the status tool")],
                   channel: Annotated[str, Field(description="Channel or chat id")], text: str,
                   thread: Annotated[str, Field(description="Thread to post in: an event's from.root")] = "",
                   persona: Annotated[str, Field(description="Target slug whose persona to post as, where the platform allows it")] = "") -> dict[str, Any]:
        return await run(ctx, "post", space=space, channel=channel, text=text, thread=thread, persona=persona)

    async def status(ctx: Context) -> dict[str, Any]:
        return await run(ctx, "spaces")

    async def provision(ctx: Context) -> dict[str, Any]:
        return await run(ctx, "provision")

    mcp.tool(name=f"{name}_post", title=f"Post to {title}",
             description=f"Post to a {title} channel or thread as the bot. Private to the owner's agents with a grant.", annotations=kit.SEND)(post)
    mcp.tool(name=f"{name}_status", title=f"{title} status",
             description=f"{title} connection, spaces and exactly what setup is missing.", annotations=kit.READ)(status)
    mcp.tool(name=f"{name}_provision", title=f"Provision {title}",
             description=f"Owner only. What {title} setup is missing, the invite or setup link, and a one-time link behind Access where the owner "
                         "enters the bot token (never through chat).", annotations=kit.READ)(provision)
