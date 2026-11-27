from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ...refusals import CONVERSATION_REQUIRED, PolicyRefusal

if TYPE_CHECKING:
    from . import GitHubBlock

Conversation = Annotated[str | None, Field(description="The conversation handle from the GitHub wake. Alias for handle.")]


def register(block: GitHubBlock, mcp: MCPServer, kit: Any) -> None:
    name = block.name

    @mcp.tool(name=f"{name}_status", title="GitHub status", annotations=kit.READ_REMOTE)
    async def github_status(ctx: Context) -> dict[str, Any]:
        """Check the GitHub webhook, repository modes and GitHub App installation permissions."""
        return await kit.invoke(ctx, name, "status")

    @mcp.tool(name=f"{name}_label", title="Label an issue or pull request", annotations=kit.SEND)
    async def github_label(ctx: Context, repo: str, number: int, labels: list[str], handle: str | None = None,
                           conversation: Conversation = None) -> dict[str, Any]:
        """Add existing labels in owner/repo. Pass conversation or handle from a triage wake."""
        return await kit.invoke(ctx, name, "label", repo=repo, number=number, labels=labels, handle=handle)

    @mcp.tool(name=f"{name}_comment", title="Comment on an issue or pull request", annotations=kit.SEND)
    async def github_comment(ctx: Context, repo: str, number: int, body: str, handle: str | None = None,
                             conversation: Conversation = None) -> dict[str, Any]:
        """Comment in owner/repo. Pass conversation or handle from a triage wake."""
        return await kit.invoke(ctx, name, "comment", repo=repo, number=number, body=body, handle=handle)

    @mcp.tool(name=f"{name}_open_issue", title="Open an issue", annotations=kit.SEND)
    async def github_open_issue(ctx: Context, repo: str, title: str, body: str = "", labels: list[str] | None = None,
                                handle: str | None = None, conversation: Conversation = None) -> dict[str, Any]:
        """Open an issue in owner/repo. Pass conversation or handle from a triage wake."""
        return await kit.invoke(ctx, name, "open_issue", repo=repo, title=title, body=body, labels=labels, handle=handle)

    @mcp.tool(name=f"{name}_link_duplicate", title="Resolve a duplicate issue", annotations=kit.DELETE)
    async def github_link_duplicate(ctx: Context, repo: str, number: int, duplicate_of: int, handle: str | None = None,
                                    conversation: Conversation = None) -> dict[str, Any]:
        """Link a duplicate to its canonical issue in the same repository, label it duplicate, and close as not planned."""
        return await kit.invoke(ctx, name, "link_duplicate", repo=repo, number=number, duplicate_of=duplicate_of, handle=handle)

    @mcp.tool(name=f"{name}_notify_owner", title="Notify the owner about triage", annotations=kit.WRITE)
    async def github_notify_owner(ctx: Context, text: str, handle: str | None = None,
                                  conversation: Conversation = None) -> dict[str, Any]:
        """Put a triage note with the triggering permalink in the owner's inbox. Pass conversation or handle."""
        if handle is None:
            raise PolicyRefusal(CONVERSATION_REQUIRED, "Provide conversation or handle.")
        return await kit.invoke(ctx, name, "notify_owner", handle=handle, text=text)

    @mcp.tool(name=f"{name}_reply", title="Reply in the originating GitHub thread", annotations=kit.SEND)
    async def github_reply(ctx: Context, text: str, handle: str | None = None, conversation: Conversation = None) -> dict[str, Any]:
        """Reply in the originating GitHub thread. Pass conversation or handle from the assist or triage wake."""
        if handle is None:
            raise PolicyRefusal(CONVERSATION_REQUIRED, "Provide conversation or handle.")
        return await kit.invoke(ctx, name, "reply", handle=handle, text=text)
