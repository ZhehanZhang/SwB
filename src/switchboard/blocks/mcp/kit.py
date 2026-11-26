"""The generic MCP tools (send, reply, wait, inbox, status, result, cancel, targets, connect_*) and the helpers block
tools use. A block registers its own tools through Block.mcp_tools(mcp, kit) and never imports the MCP block."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Callable, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ...auth import Principal
    from . import MCPBlock

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_REMOTE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

ProjectArg = Annotated[str | None, Field(description="Project slug (project_list). Reads cover that project first, then general")]
Conversation = Annotated[str, Field(description="The conversation: its handle (h_...) from send or an inbox item, or the block's own id (a jam_ thread id)")]
DEPRECATED = "Deprecated alias of {new}, kept for this release only; use {new}. "


class FileIn(BaseModel):
    name: str = Field(description="File name, e.g. spec.md")
    text: str | None = Field(default=None, description="File content as text")
    base64: str | None = Field(default=None, description="File content as base64, for binary files")
    media_type: str | None = Field(default=None, description="MIME type, e.g. text/markdown")


class Kit:
    def __init__(self, block: "MCPBlock", who: Callable[[Context], "Principal"], run: Callable[[Any], Any], need: Callable[["Principal", str], None],
                 pscope: Callable[[str], str], unrestricted: Callable[["Principal", str], None]):
        self.block = block
        self.core = block.core
        self.who, self.run, self.need, self.pscope, self.unrestricted = who, run, need, pscope, unrestricted
        self.wait_caps = block.wait_caps
        self.aliases = block.legacy_aliases
        self.FileIn = FileIn
        self.ProjectArg = ProjectArg
        self.Conversation = Conversation
        self.READ, self.READ_REMOTE, self.WRITE, self.WRITE_IDEMPOTENT, self.SEND, self.DELETE = READ, READ_REMOTE, WRITE, WRITE_IDEMPOTENT, SEND, DELETE

    def deprecated(self, new: str, text: str) -> str:
        return DEPRECATED.format(new=new) + text

    def owner(self, ctx: Context, what: str) -> "Principal":
        """Pairing codes are the owner's: an unrestricted OAuth or bearer-key caller, never an agent connected by a code."""
        p = self.who(ctx)
        if p.kind == "connect":
            raise ToolError(f"a connected agent can't {what}; the owner does that from the root page or his own sign-in")
        self.unrestricted(p, what)
        return p

    def alias(self, ctx: Context, name: str) -> "Principal":
        p = self.who(ctx)
        self.core.alias_used(name, p.source)
        return p

    async def invoke(self, ctx: Context, block: str, action: str, **kw: Any) -> Any:
        from ...blocks import BlockError

        try:
            b = self.core.destination(block)
            return await self.run(self.core.invoke(self.who(ctx), block, b.action(action), kw))
        except BlockError as e:
            raise ToolError(str(e)) from e

    def scoped(self, p: "Principal", project: str | None) -> None:
        if project:
            self.need(p, self.pscope(project))
        elif p.scopes is not None and "*" not in p.scopes:
            raise ToolError(f"the {p.source} key is limited to {', '.join(p.scopes)}; pass project")

    async def send(self, p: "Principal", *, wait_s: float = 0, **kw: Any) -> dict[str, Any]:
        from . import TASK_MODE

        out = await self.run(self.core.send(p, **kw))
        if wait_s and not TASK_MODE.get() and out.get("block") and out.get("handle") and not out.get("mailbox"):
            got = await self.core.inbox.wait(owner=p.source, handle=out["handle"], timeout=self.wait_caps.clamp(p.source, wait_s))
            out["replies"], out["cursor"], out["timed_out"] = got["items"], got["cursor"], got["timed_out"]
        return out

    async def wait(self, p: "Principal", *, handle: str | None, timeout_s: float | None, project: str | None, block: str | None = None) -> dict[str, Any]:
        got = await self.core.inbox.wait(owner=p.source, handle=handle, timeout=self.wait_caps.clamp(p.source, timeout_s), project=project, destination=block)
        if handle:
            h = self.core.inbox.handle(handle)
            h = h if h and h["owner"] == p.source else None
            got["handle_status"] = (h["status"] if h else "unknown") if (h is None or not h["open"]) else "following"
        if got["timed_out"]:
            got["next"] = "Nothing new yet. Tell the owner it is still working; call wait again or inbox later."
        return got

    def inbox(self, p: "Principal", *, since_cursor: int | None, handle: str | None, limit: int, project: str | None, block: str | None = None) -> dict[str, Any]:
        out = self.core.inbox.read(owner=p.source, handle=handle, since=since_cursor, limit=limit, project=project, destination=block)
        senders = [block] if block else [n for n, b in self.core.blocks.items() if b.verb("send") is not None]
        open_ = [h for name in senders for h in self.core.inbox.handles(owner=p.source, destination=name, open_only=True, project=project, limit=20)]
        out["open"] = [{"handle": h["handle"], "conversation": h["handle"], "block": h["destination"], "target": h.get("target"), "thread_id": h["ref"],
                        "status": h["status"], "title": h["title"] or h["meta"].get("task", "")[:80]} for h in open_]
        return out

    async def verb(self, ctx: Context, verb: str, ref: str, **kw: Any) -> Any:
        return await self.run(self.core.verb(self.who(ctx), verb, ref, **kw))

    # The generic tools ---------------------------------------------------------------------

    def register_generic(self, mcp: MCPServer) -> None:
        core = self.core
        kit = self

        @mcp.tool(
            title="Send work or a message",
            description=("Hand work to someone or leave a message; cheap, so send freely. Every agent (claude, claude-code, chatgpt, gemini, "
                         "capy, ...) has a switchboard mailbox, so a message always goes through even when it isn't running: it reads on its own "
                         "schedule, in a live chat, or when switchboard wakes it. Its reply reaches your inbox with in_reply_to set to the "
                         "conversation this returns. Without to, the owner's route decides. to: a slug from targets, agent:<name>, or "
                         "<block>:<kind>:<id>. Make the task self-contained (goal, context, repos, done). Then wait() and answer with the reply. "
                         "Use forward to hand off a conversation you received and route the final answer back to its sender."),
            annotations=SEND,
        )
        async def send(
            ctx: Context,
            task: Annotated[str, Field(description="Self-contained task or message in markdown")],
            to: Annotated[str | None, Field(description="Optional target: a slug, agent:<name>, or <block>:<kind>:<id>. Without it, your route decides")] = None,
            project: ProjectArg = None,
            title: Annotated[str | None, Field(description="Short title")] = None,
            repos: Annotated[list[str] | None, Field(description="owner/repo names the task concerns; must be linked to the project")] = None,
            files: Annotated[list[FileIn] | None, Field(description="Optional files to attach")] = None,
            new_conversation: Annotated[bool | None, Field(description="For a target that is a long-lived thread (a Captain): true starts a new one instead")] = None,
            wait_s: Annotated[float, Field(ge=0, description="Seconds to wait for a first reply before returning (capped per client)")] = 0,
            forward: Annotated[str | None, Field(description="Conversation handle you received and are handing off")] = None,
        ) -> dict[str, Any]:
            p = kit.who(ctx)
            mailbox = bool(to) and (to.strip().lower().startswith("agent:") or to.strip().lower() in core.known_agents())
            if project or not mailbox:
                kit.scoped(p, project)
            return await kit.send(p, wait_s=wait_s, task=task, to=to, project=project, title=title, repos=repos,
                                  files=[f.model_dump() for f in files] if files else None, new_conversation=new_conversation, forward=forward)

        @mcp.tool(title="Reply in a conversation", description="Send a follow-up into a conversation (its handle from send or an inbox item, or a block's id such as a thread id): answer its question, add context, redirect it, or answer a mailbox message. delivery (steer, queue, interrupt) applies where the block supports it.", annotations=SEND)
        async def reply(ctx: Context, text: str, conversation: Conversation, delivery: Literal["steer", "queue", "interrupt"] | None = None,
                        final: Annotated[bool, Field(description="false for an interim note (handed off, working on it); true for the answer")] = True) -> dict[str, Any]:
            return await kit.verb(ctx, "reply", conversation, text=text, delivery=delivery, final=final)

        @mcp.tool(
            title="Wait for replies",
            description="Wait for replies on your conversations, or messages other agents left you: returns as soon as a reply, a question for the owner, a final result or a message arrives, or empty when timeout_s runs out (default and maximum depend on your client; see switchboard_status). Call it with no arguments. conversation, project and block only narrow it. Each item's 'from' says who it is from. Each call returns only what you have not seen.",
            annotations=READ,
        )
        async def wait(ctx: Context, conversation: str | None = None, timeout_s: Annotated[float | None, Field(ge=0)] = None, project: ProjectArg = None,
                       block: str | None = None) -> dict[str, Any]:
            return await kit.wait(kit.who(ctx), handle=conversation, timeout_s=timeout_s, project=project, block=block)

        @mcp.tool(title="Your inbox", description="Your switchboard mailbox: replies on your conversations and messages other agents left you since since_cursor (default: since you last read), plus your open conversations. A reply to a message you sent carries in_reply_to with that conversation. Each item's from and trust say who sent it, from switchboard's own data. Answer a message with reply(conversation=<its handle>, text). No arguments needed; conversation, project and block only narrow it. Never blocks.", annotations=READ)
        async def inbox(ctx: Context, since_cursor: int | None = None, conversation: str | None = None, limit: Annotated[int, Field(ge=1, le=100)] = 50,
                        project: ProjectArg = None, block: str | None = None) -> dict[str, Any]:
            return kit.inbox(kit.who(ctx), since_cursor=since_cursor, handle=conversation, limit=limit, project=project, block=block)

        @mcp.tool(title="Conversation status", description="Status of a conversation (handle or the block's id): working, waiting, idle or failed, whether it needs the owner, its PRs and latest messages, as the block that owns it reports them.", annotations=READ_REMOTE)
        async def status(ctx: Context, conversation: Conversation, messages: Annotated[int, Field(ge=1, le=20)] = 5, include_tools: bool = False) -> dict[str, Any]:
            return await kit.verb(ctx, "status", conversation, messages=messages, include_tools=include_tools)

        @mcp.tool(title="Conversation result", description="The latest final reply of a conversation (handle or the block's id) in full, its PR links, and download links for delivered files (links expire).", annotations=READ_REMOTE)
        async def result(ctx: Context, conversation: Conversation, tail: Annotated[int, Field(ge=0, le=30)] = 0) -> dict[str, Any]:
            return await kit.verb(ctx, "result", conversation, tail=tail)

        @mcp.tool(title="Stop following a conversation", description="Stop reporting on a conversation (its handle). The work on the other side keeps running; use reply to stop it.", annotations=WRITE_IDEMPOTENT)
        async def cancel(ctx: Context, conversation: Conversation) -> dict[str, Any]:
            return await kit.verb(ctx, "cancel", conversation)

        @mcp.tool(title="List targets", description="Everything send can reach: each block's targets (Captains, channels, ...), an agent:<name> mailbox per agent, and project routes, with address, slugs, level and persona. A slug clash goes to the higher priority. block, kind and query narrow it; refresh re-reads the blocks now.", annotations=READ_REMOTE)
        async def targets(ctx: Context, block: str | None = None, kind: str | None = None, query: str | None = None, refresh: bool = False) -> dict[str, Any]:
            kit.who(ctx)
            got = await core.targets(block=block, kind=kind, query=query, refresh=refresh)
            return {"targets": got, "errors": dict(core.target_errors), "usage": "send(task, to=<slug or address>); without to, your route decides."}

        @mcp.tool(title="Connect a new agent", description="Mint a one-time pairing code (10 minutes) for an agent you are setting up, such as claude-code, codex or a laptop script. The client redeems it at POST /connect for its own bearer key; the owner can revoke it from the root page or connect_revoke. Only an unrestricted caller may mint codes.", annotations=WRITE)
        async def connect_code(ctx: Context, agent: Annotated[str, Field(description="Agent name it signs in as, lowercase-dashed, e.g. codex")], project: ProjectArg = None,
                               client: Annotated[str | None, Field(description="What it is, e.g. codex-cli on the laptop")] = None) -> dict[str, Any]:
            from ...connect import ConnectError

            p = kit.owner(ctx, "mint pairing codes")
            if project:
                core.project(project)
            try:
                out = core.connections.mint(agent=agent, project=project, client=client, by=p.source)
            except ConnectError as e:
                raise ToolError(str(e)) from e
            core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination="core", action="connect_code", outcome="ok", target=out["agent"])
            base = core.cfg.public_url or ""
            return {**out, "redeem": f"POST {base}/connect with {{\"code\": \"{out['code']}\"}}",
                    "curl": f"curl -s -X POST {base}/connect -H 'content-type: application/json' -d '{{\"code\": \"{out['code']}\"}}'"}

        @mcp.tool(title="Who can reach what", description="Every resource's visibility: private (only the owner and his own agents) or shared (anyone in that workspace, or a narrowed list). Resources are target:<slug>, channel:<workspace>/<id or #name>, memory:<scope>, route:<id> and tool:<name>; defaults are per kind.", annotations=READ)
        async def visibility_list(ctx: Context) -> dict[str, Any]:
            kit.owner(ctx, "read visibility")
            slugs = [f"target:{s}" for t in await core.targets() for s in t.get("slugs") or [] if t.get("block") != "agent"][:500]
            return {"defaults": {k: v if isinstance(v, str) else list(v) for k, v in core.visibility.defaults.items()}, "resources": core.visibility.rows(slugs)}

        @mcp.tool(title="Make a resource private or shared", description="Owner only. Set one resource (e.g. target:teamcaptain, channel:acme/#dev) to private, shared, or a list of Slack user ids and workspace:<label> entries. Tools stay private. Audited.", annotations=WRITE_IDEMPOTENT)
        async def visibility_set(ctx: Context, resource: str, visibility: Annotated[str | list[str], Field(description="private, shared, or a list such as ['U0123', 'workspace:acme']")]) -> dict[str, Any]:
            from ...visibility import VisibilityError

            p = kit.owner(ctx, "change visibility")
            try:
                rule = core.visibility.set(resource, visibility, f"{p.kind}:{p.source}", core.clock())
            except VisibilityError as e:
                raise ToolError(str(e)) from e
            core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination="core", action="visibility_set", outcome="ok",
                              target=resource, detail=str(rule))
            return {"resource": resource.lower(), "visibility": rule}

        @mcp.tool(title="Connected agents", description="Agents connected with a pairing code: their key ids, project, client, when they last called, and who minted the code. Keys themselves are never shown again.", annotations=READ)
        async def connect_list(ctx: Context, include_revoked: bool = False) -> dict[str, Any]:
            kit.owner(ctx, "list connected agents")
            return {"keys": core.connections.list(include_revoked)}

        @mcp.tool(title="Revoke a connected agent", description="Revoke one connected key (key_id) or every key of an agent. The agent's next call is refused; its memory and conversations stay. Audited.", annotations=DELETE)
        async def connect_revoke(ctx: Context, key_id: str | None = None, agent: str | None = None) -> dict[str, Any]:
            from ...connect import ConnectError

            p = kit.owner(ctx, "revoke connected agents")
            try:
                n = core.connections.revoke(key_id=key_id, agent=agent)
            except ConnectError as e:
                raise ToolError(str(e)) from e
            core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination="core", action="connect_revoke", outcome="ok", target=key_id or agent)
            return {"revoked": n}

        if not self.aliases:
            return

        @mcp.tool(
            title="Message another agent",
            description=kit.deprecated("send(to='agent:<name>')", "Leave a message in another agent's mailbox (to='claude', 'chatgpt', 'gemini', 'claude-code'). It waits until that agent next calls wait or inbox. You are named as the sender from your sign-in."),
            annotations=SEND,
        )
        async def agent_send(ctx: Context, to: Annotated[str, Field(description="The agent's name, as switchboard_status shows it")], text: str,
                             project: ProjectArg = None) -> dict[str, Any]:
            p = kit.alias(ctx, "agent_send")
            if project:
                kit.need(p, kit.pscope(project))
            return await kit.run(core.agent_send(p, to=to, text=text, project=project))
