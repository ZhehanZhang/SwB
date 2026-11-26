"""slack_* tools on the MCP origin: the Slack destination's actions, plus slack_wait on the caller's inbox."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Callable, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

if TYPE_CHECKING:
    from ...auth import Principal
    from ...core import Switchboard

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
WAIT = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

Workspace = Annotated[str, Field(description="Workspace label from slack_workspaces")]
Channel = Annotated[str, Field(description="Channel id (C0123...) or #name")]
Identity = Annotated[Literal["", "bot", "user"], Field(description="bot (the app, with a persona) or user (the owner's own account); empty picks bot when allowed")]

SLACK_NOTE = ("Each workspace has identities: bot (the app; posts can use a persona) and/or user (the owner's own account). Writes follow "
              "a per-identity policy: free (sent at once), approval (queued until the owner approves out of band; you cannot approve), "
              "or never.")


def register(mcp: MCPServer, core: "Switchboard", who: Callable[[Context], "Principal"], wait_caps: Any) -> None:
    async def slack(ctx: Context, action: str, **kw: Any) -> Any:
        from ...destinations import DestinationError

        dest = core.destination("slack")
        try:
            return await core.invoke(who(ctx), "slack", dest.action(action), kw)
        except DestinationError as e:
            raise ToolError(str(e)) from e

    @mcp.tool(title="Slack workspaces", description="The Slack workspaces you may use, your identities and write policy in each, and connection status. " + SLACK_NOTE, annotations=READ)
    async def slack_workspaces(ctx: Context) -> dict[str, Any]:
        return {"you": await slack(ctx, "me"), "workspaces": await slack(ctx, "workspaces")}

    @mcp.tool(title="Slack channels", description="List channels an identity can see, optionally filtered by a name substring. Shows which are watched (their new messages reach your inbox).", annotations=READ)
    async def slack_channels(ctx: Context, workspace: Workspace, query: str = "", identity: Identity = "", include_private: bool = True,
                             limit: Annotated[int, Field(ge=1, le=500)] = 200) -> list[dict[str, Any]]:
        return await slack(ctx, "channels", workspace=workspace, query=query, identity=identity, include_private=include_private, limit=limit)

    @mcp.tool(title="Search Slack", description="Search messages with Slack search syntax (from:, in:, before:). Needs the user identity; bots cannot search.", annotations=READ)
    async def slack_search(ctx: Context, workspace: Workspace, query: str, count: Annotated[int, Field(ge=1, le=100)] = 20) -> list[dict[str, Any]]:
        return await slack(ctx, "search", workspace=workspace, query=query, count=count)

    @mcp.tool(title="Slack channel history", description="Recent messages in a channel, newest first. Page with oldest/latest (Slack ts) or cursor.", annotations=READ)
    async def slack_history(ctx: Context, workspace: Workspace, channel: Channel, limit: Annotated[int, Field(ge=1, le=200)] = 30, oldest: str = "",
                            latest: str = "", cursor: str = "", identity: Identity = "") -> dict[str, Any]:
        return await slack(ctx, "history", workspace=workspace, channel=channel, limit=limit, oldest=oldest, latest=latest, cursor=cursor, identity=identity)

    @mcp.tool(title="Slack thread", description="Read a thread: the parent message and its replies, oldest first.", annotations=READ)
    async def slack_thread(ctx: Context, workspace: Workspace, channel: Channel, thread_ts: str, limit: Annotated[int, Field(ge=1, le=200)] = 100,
                           identity: Identity = "") -> dict[str, Any]:
        return await slack(ctx, "thread", workspace=workspace, channel=channel, thread_ts=thread_ts, limit=limit, identity=identity)

    @mcp.tool(title="Watched Slack channels", description="Channels per workspace whose new messages are delivered to your inbox (slack_wait), with any that could not be resolved.", annotations=READ)
    async def slack_watched(ctx: Context, workspace: str = "") -> dict[str, Any]:
        return await slack(ctx, "watched", workspace=workspace)

    @mcp.tool(title="Recent Slack events", description="Recent events from watched channels, mentions of the bot and DMs, newest first (kind: watched, mention or dm; since: unix seconds). slack_wait is the live feed.", annotations=READ)
    async def slack_events(ctx: Context, workspace: str = "", kind: Literal["", "watched", "mention", "dm"] = "", since: float = 0,
                           limit: Annotated[int, Field(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
        return await slack(ctx, "events", workspace=workspace, kind=kind, since=since, limit=limit)

    @mcp.tool(title="Slack personas", description="Personas (display name and avatar for bot posts) and the one you post as by default. Personas never apply to user-identity posts.", annotations=READ)
    async def slack_personas(ctx: Context) -> dict[str, Any]:
        return await slack(ctx, "personas")

    @mcp.tool(title="Slack targets", description=("Slugs that route a Slack mention: '@bot <slug> text' goes to that Captain, agent or project, and its "
                                                  "thread stays with it. Found automatically (Captain > agent > project on a clash). refresh re-reads them now."), annotations=READ)
    async def slack_targets(ctx: Context, refresh: bool = False) -> dict[str, Any]:
        return await slack(ctx, "targets", refresh=refresh)

    @mcp.tool(title="Post to Slack", description=("Post a message, or a thread reply with thread_ts, to a channel id or #name. identity bot posts as the app with a persona "
                                                  "(persona only if you may choose one); identity user posts as the owner himself. If the identity's policy is approval, "
                                                  "the post is queued (status pending_approval) until the owner approves. Reply to a mention in its thread."), annotations=SEND)
    async def slack_post(ctx: Context, workspace: Workspace, channel: Channel, text: str, identity: Identity = "", thread_ts: str = "", persona: str = "",
                         reply_broadcast: bool = False) -> dict[str, Any]:
        return await slack(ctx, "post", workspace=workspace, channel=channel, text=text, identity=identity, thread_ts=thread_ts, persona=persona, reply_broadcast=reply_broadcast)

    @mcp.tool(title="React in Slack", description="Add an emoji reaction (name without colons) to a message. Follows the identity's write policy.", annotations=SEND)
    async def slack_react(ctx: Context, workspace: Workspace, channel: Channel, ts: str, name: str, identity: Identity = "") -> dict[str, Any]:
        return await slack(ctx, "react", workspace=workspace, channel=channel, ts=ts, name=name, identity=identity)

    @mcp.tool(title="Ask to post to Slack", description="Queue a post for the owner's approval even when the identity's policy is free. Same arguments as slack_post; returns a request_id. Only the owner approves, out of band.", annotations=SEND)
    async def slack_request_write(ctx: Context, workspace: Workspace, channel: Channel, text: str, identity: Identity = "", thread_ts: str = "", persona: str = "") -> dict[str, Any]:
        return await slack(ctx, "request_write", workspace=workspace, channel=channel, text=text, identity=identity, thread_ts=thread_ts, persona=persona)

    @mcp.tool(title="Slack write requests", description="Your write requests, newest first (status: pending, approved, rejected, expired, sent or failed), or one request in full by request_id.", annotations=READ)
    async def slack_write_requests(ctx: Context, request_id: str = "", status: Literal["", "pending", "approved", "rejected", "expired", "sent", "failed"] = "",
                                   limit: Annotated[int, Field(ge=1, le=100)] = 20) -> Any:
        if request_id:
            return await slack(ctx, "write_request", request_id=request_id)
        return await slack(ctx, "write_requests", status=status, limit=limit)

    @mcp.tool(title="Wait for Slack", description=("Wait for Slack events addressed to your inbox: messages in watched channels, mentions of the bot, DMs. Returns as soon as one arrives, "
                                                   "or empty when timeout_s runs out. Each item's 'from' names the workspace, channel and Slack user, from Slack's own data. "
                                                   "addressed_to_us marks mentions, DMs and replies in our threads. Each call returns only what you have not seen."), annotations=WAIT)
    async def slack_wait(ctx: Context, timeout_s: Annotated[float | None, Field(ge=0)] = None, project: str | None = None) -> dict[str, Any]:
        p = who(ctx)
        got = await core.inbox.wait(owner=p.source, destination="slack", timeout=wait_caps.clamp(p.source, timeout_s), project=project)
        if got["timed_out"]:
            got["next"] = "Nothing new in Slack yet; call slack_wait again or check later."
        return got
