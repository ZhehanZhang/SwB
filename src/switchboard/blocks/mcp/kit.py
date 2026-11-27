"""The generic MCP tools (send, reply, wait, inbox, status, result, cancel, targets, connect_*) and the helpers block
tools use. A block registers its own tools through Block.mcp_tools(mcp, kit) and never imports the MCP block."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any, Callable, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.tool_name_validation import TOOL_NAME_REGEX
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field
from pydantic_core import to_json

from ...audit import iso
from ...companion.caps.base import CapabilityError
from ...companion.caps.drop import decode_file_content, valid_name
from ...companion.mcp_config import NAME as LOCAL_MCP_NAME
from ...companions import COMPANION_ID, SLUG
from ...refusals import OWNER_REQUIRED, SCOPE_REQUIRED, PolicyRefusal
from ...trust import neutralize, wrap

if TYPE_CHECKING:
    from ...auth import Principal
    from . import MCPBlock

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_REMOTE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

ProjectArg = Annotated[str | None, Field(description="Project slug (project_list)")]
Conversation = Annotated[str, Field(description="The conversation: its handle (h_...) from send or an inbox item, or the block's own id (a jam_ thread id)")]
DEPRECATED = "Deprecated alias of {new}, kept for this release only; use {new}. "


DEVICE_VALUES = frozenset({"type", "mimeType", "uri", "size"})
DEVICE_SCHEMAS = frozenset({"inputSchema", "outputSchema"})
SCHEMA_TEXT = frozenset({"title", "description", "$comment", "examples"})
DEVICE_TOKEN = re.compile(r"[A-Za-z0-9_.+/:#@=-]{1,256}")
DEVICE_BASE64 = re.compile(r"[A-Za-z0-9+/_-]*={0,2}")
DEVICE_OUTPUT_LIMIT = 1024 * 1024
DEVICE_OUTPUT_ERROR = "Companion output exceeds 1 MiB. Request less data."
DEVICE_LIST_IDS = {
    ("id",): COMPANION_ID.fullmatch,
    ("last_seen",): DEVICE_TOKEN.fullmatch,
    ("capabilities", None): SLUG.fullmatch,
    ("capabilities", None, "id"): SLUG.fullmatch,
    ("capabilities", None, "actions", None, "name"): SLUG.fullmatch,
}
DEVICE_LIST_SCHEMA = ("capabilities", None, "actions", None, "schema")
DEVICE_ACTION_IDS = {
    ("mcp", "servers"): {("servers", None, "name"): LOCAL_MCP_NAME.fullmatch},
    ("mcp", "tools"): {("tools", None, "name"): TOOL_NAME_REGEX.fullmatch},
    ("drop", "list"): {("files", None, "name"): valid_name},
    ("drop", "get"): {("name",): valid_name},
    ("drop", "put"): {("name",): valid_name},
}


def frame_device(value: Any, where: str, *, sender: str | None = None,
                 exact: dict[tuple[str | None, ...], Callable[[str], Any]] | None = None,
                 schema_path: tuple[str | None, ...] | None = None, limit: int = DEVICE_OUTPUT_LIMIT) -> Any:
    """Frame device strings and bound the fields before building the full response."""
    remaining = limit

    def charge(size: int) -> None:
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            raise ToolError(DEVICE_OUTPUT_ERROR)

    def frame(item: Any, path: tuple[str | None, ...], key: str = "", schema: bool = False) -> Any:
        if len(path) > 64:
            raise ToolError("Companion output contains too many nested fields.")
        if isinstance(item, dict):
            charge(2)
            out = {}
            binary = item.get("type") in ("image", "audio")
            for name, child in item.items():
                display = neutralize(str(name))[:128]
                charge(len(to_json(display)) + 1 + bool(out))
                next_path = (*path, name)
                out[display] = frame(child, next_path, "text" if name == "data" and not binary else name,
                                     schema or name in DEVICE_SCHEMAS or next_path == schema_path)
            return out
        if isinstance(item, list):
            charge(2 + max(0, len(item) - 1))
            return [frame(child, (*path, None), key, schema) for child in item]
        if isinstance(item, str) and item:
            if len(item) > remaining:
                raise ToolError(DEVICE_OUTPUT_ERROR)
            check = exact.get(path) if exact else None
            machine = key in DEVICE_VALUES or (schema and key not in SCHEMA_TEXT)
            binary = not schema and (key == "data" or key.lower().endswith(("base64", "b64")))
            if not ((check and check(item)) or (machine and DEVICE_TOKEN.fullmatch(item))
                    or (binary and DEVICE_BASE64.fullmatch(item))):
                item = wrap(item, "companion" if sender else None, sender, "external", where)
        charge(len(to_json(item)))
        return item

    try:
        return frame(value, ())
    except ValueError:
        raise ToolError("Companion output contains invalid text.") from None


def device_output(out: dict[str, Any], file_data: str | None = None) -> CallToolResult:
    text = to_json(out if file_data is None else {**out, "file_data": "Read result.content_b64 in structuredContent."})
    if len(text) > DEVICE_OUTPUT_LIMIT:
        raise ToolError(DEVICE_OUTPUT_ERROR)
    structured = out if file_data is None else {**out, "result": {**out["result"], "content_b64": file_data}}
    return CallToolResult(content=[TextContent(type="text", text=text.decode("utf-8"))], structured_content=structured)


def frame_companion(row: dict[str, Any], limit: int = DEVICE_OUTPUT_LIMIT) -> dict[str, Any]:
    return frame_device(row, "", sender=row["id"], exact=DEVICE_LIST_IDS, schema_path=DEVICE_LIST_SCHEMA, limit=limit)


class FileIn(BaseModel):
    name: str = Field(description="File name, e.g. spec.md")
    text: str | None = Field(default=None, description="File content as text")
    base64: str | None = Field(default=None, description="File content as base64, for binary files")
    media_type: str | None = Field(default=None, description="MIME type, e.g. text/markdown")


class Kit:
    def __init__(self, block: "MCPBlock", who: Callable[[Context], "Principal"], run: Callable[[Any], Any], need: Callable[["Principal", str], None],
                 pscope: Callable[[str], str]):
        self.block = block
        self.core = block.core
        self.who, self.run, self.need, self.pscope = who, run, need, pscope
        self.wait_caps = block.wait_caps
        self.aliases = block.legacy_aliases
        self.FileIn = FileIn
        self.ProjectArg = ProjectArg
        self.Conversation = Conversation
        self.READ, self.READ_REMOTE, self.WRITE, self.WRITE_IDEMPOTENT, self.SEND, self.DELETE = READ, READ_REMOTE, WRITE, WRITE_IDEMPOTENT, SEND, DELETE

    def deprecated(self, new: str, text: str) -> str:
        return DEPRECATED.format(new=new) + text

    def mint_pairing(self, p: "Principal", agent: str, project: str | None, client: str | None) -> dict[str, Any]:
        from ...connect import ConnectError

        if not p.is_unrestricted_owner:
            raise PolicyRefusal(OWNER_REQUIRED)
        if project:
            self.core.project(project)
        try:
            out = self.core.connections.mint(agent=agent, project=project, client=client, by=p.source)
        except ConnectError as e:
            raise ToolError(str(e)) from e
        self.core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination="core", action="connect_code", outcome="ok", target=out["agent"])
        return out

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
            raise PolicyRefusal(SCOPE_REQUIRED, f"the {p.source} key is limited to {', '.join(p.scopes)}; pass project")

    async def send(self, p: "Principal", *, wait_s: float = 0, **kw: Any) -> dict[str, Any]:
        from . import TASK_MODE

        out = await self.run(self.core.send(p, **kw))
        if wait_s and not TASK_MODE.get() and out.get("block") and out.get("handle") and not out.get("mailbox"):
            got = await self.core.inbox.wait(owner=p.source, handle=out["handle"], timeout=self.wait_caps.clamp(p.source, wait_s))
            out["replies"], out["cursor"], out["timed_out"] = got["items"], got["cursor"], got["timed_out"]
        return out

    async def wait(self, p: "Principal", *, handle: str | None, timeout_s: float | None, project: str | None, block: str | None = None,
                   owner_facing: bool = False) -> dict[str, Any]:
        handle = handle.strip() if handle else None
        h = self.core.inbox.handle(handle) if handle else None
        owner, reader = self.inbox_reader(p, h)
        got = await self.core.inbox.wait(owner=owner, reader=reader, handle=handle, timeout=self.wait_caps.clamp(p.source, timeout_s),
                                         project=project, destination=block, scopes=p.scopes, owner_digests=p.is_unrestricted_owner)
        if handle:
            h = self.core.inbox.handle(handle)
            h = h if h and (p.is_unrestricted_owner or self.core.inbox.participant(h, p.source)) else None
            got["handle_status"] = (h["status"] if h else "unknown") if (h is None or not h["open"]) else "following"
        if got["timed_out"]:
            got["next"] = ("Tell the owner the work is still in progress. " if owner_facing else "") + "Check again with wait or inbox."
        return got

    def inbox(self, p: "Principal", *, since_cursor: int | None, handle: str | None, limit: int, project: str | None, block: str | None = None) -> dict[str, Any]:
        handle = handle.strip() if handle else None
        owner, reader = self.inbox_reader(p, self.core.inbox.handle(handle) if handle else None)
        out = self.core.inbox.read(owner=owner, reader=reader, handle=handle, since=since_cursor, limit=limit,
                                   project=project, destination=block, scopes=p.scopes, owner_digests=p.is_unrestricted_owner)
        senders = [block] if block else [n for n, b in self.core.blocks.items() if b.verb("send") is not None]
        open_ = [h for name in senders for h in self.core.inbox.handles(owner=p.source, destination=name, open_only=True,
                                                                      project=project, scopes=p.scopes, limit=20)]
        out["open"] = [{"handle": h["handle"], "conversation": h["handle"], "block": h["destination"], "target": h.get("target"), "thread_id": h["ref"],
                        "status": h["status"], "title": h["title"] or h["meta"].get("task", "")[:80]} for h in open_]
        return out

    def inbox_reader(self, p: "Principal", handle: dict[str, Any] | None) -> tuple[str | None, str]:
        if handle and p.is_unrestricted_owner and not self.core.inbox.participant(handle, p.source):
            return None, f"owner-read:{p.source}"
        return p.source, p.source

    async def verb(self, ctx: Context, verb: str, ref: str, **kw: Any) -> Any:
        return await self.run(self.core.verb(self.who(ctx), verb, ref, **kw))

    # The generic tools ---------------------------------------------------------------------

    def register_generic(self, mcp: MCPServer) -> None:
        @mcp.tool(title="Owner sign-in link", description="Mint a one-time web sign-in link that expires in five minutes. Show it directly to the owner and never paste it anywhere else.", annotations=WRITE)
        async def login_link(ctx: Context) -> dict[str, Any]:
            from ...sessions import LINK_TTL

            p = self.who(ctx)
            if not (base := self.core.cfg.public_url):
                raise ToolError("Set SWB_PUBLIC_URL to enable owner sign-in links.")
            token = self.core.auth.sessions.mint_link(p.client_id, p.source)
            self.core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination="core",
                                   action="login_link_minted", outcome="ok")
            return {"url": f"{base}/login#t={token}", "expires_in": LINK_TTL}

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
            in_reply_to: Annotated[str | None, Field(description="Not supported by send; use reply to answer a conversation")] = None,
        ) -> dict[str, Any]:
            if in_reply_to is not None:
                raise ToolError("use reply to answer a conversation")
            p = kit.who(ctx)
            mailbox = bool(to) and (to.strip().lower().startswith("agent:") or to.strip().lower() in core.known_agents())
            if project or not mailbox:
                kit.scoped(p, project)
            return await kit.send(p, wait_s=wait_s, task=task, to=to, project=project, title=title, repos=repos,
                                  files=[f.model_dump() for f in files] if files else None, new_conversation=new_conversation, forward=forward)

        @mcp.tool(title="Reply in a conversation", description="Send a follow-up into a conversation (its handle from send or an inbox item, or a block's id such as a thread id): answer its question, add context, redirect it, or answer a mailbox message. delivery (steer, queue, interrupt) applies where the block supports it.", annotations=SEND)
        async def reply(ctx: Context, text: str, conversation: Conversation, delivery: Literal["steer", "queue", "interrupt"] | None = None,
                        final: Annotated[bool, Field(description="false for an interim note (handed off, working on it); true for the answer")] = True,
                        to_owner: Annotated[bool, Field(description="Send an owner note from a forwarded Slack message or a triage conversation with notify_owner enabled.")] = False) -> dict[str, Any]:
            return await kit.verb(ctx, "reply", conversation, text=text, delivery=delivery, final=final, to_owner=to_owner)

        @mcp.tool(
            title="Wait for replies",
            description="Wait for replies on your conversations, or messages other agents left you: returns as soon as a reply, a question for the owner, a final result or a message arrives, or empty when timeout_s runs out (default and maximum depend on your client; see switchboard_status). Call it with no arguments. conversation, project and block only narrow it. Each item's 'from' says who it is from. Each call returns only what you have not seen.",
            annotations=READ,
        )
        async def wait(ctx: Context, conversation: str | None = None, timeout_s: Annotated[float | None, Field(ge=0)] = None, project: ProjectArg = None,
                       block: str | None = None,
                       owner_facing: Annotated[bool, Field(description="Set true while you are speaking with the owner.")] = False) -> dict[str, Any]:
            return await kit.wait(kit.who(ctx), handle=conversation, timeout_s=timeout_s, project=project, block=block, owner_facing=owner_facing)

        @mcp.tool(title="Your inbox", description="Read SwB messages and open conversations. The owner can also read Watch digests here. Omit since_cursor to read from your last position. Messages give their sender and trust. Replies give the original conversation in in_reply_to. Filter by conversation, project or block. Use reply with the message handle.", annotations=READ)
        async def inbox(ctx: Context, since_cursor: int | None = None, conversation: str | None = None, limit: Annotated[int, Field(ge=1, le=50)] = 50,
                        project: ProjectArg = None, block: str | None = None) -> dict[str, Any]:
            return kit.inbox(kit.who(ctx), since_cursor=since_cursor, handle=conversation, limit=limit, project=project, block=block)

        @mcp.tool(title="Conversation status", description="Status of a conversation (handle or the block's id): working, waiting, idle or failed, whether it needs the owner, its PRs and latest messages, as the block that owns it reports them.", annotations=READ_REMOTE)
        async def status(ctx: Context, conversation: Conversation, messages: Annotated[int, Field(ge=1, le=20)] = 5, include_tools: bool = False) -> dict[str, Any]:
            return await kit.verb(ctx, "status", conversation, messages=messages, include_tools=include_tools)

        @mcp.tool(title="Conversation result", description="The latest final reply of a conversation (handle or the block's id) in full, its PR links, and download links for delivered files (links expire).", annotations=READ_REMOTE)
        async def result(ctx: Context, conversation: Conversation, tail: Annotated[int, Field(ge=0, le=30)] = 0) -> dict[str, Any]:
            return await kit.verb(ctx, "result", conversation, tail=tail)

        @mcp.tool(title="Stop following a conversation", description="Stop following a conversation you sent. The work on the other side continues.", annotations=WRITE_IDEMPOTENT)
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
            p = kit.who(ctx)
            out = kit.mint_pairing(p, agent, project, client)
            base = core.cfg.public_url or ""
            return {**out, "redeem": f"POST {base}/connect with {{\"code\": \"{out['code']}\"}}",
                    "curl": f"curl -s -X POST {base}/connect -H 'content-type: application/json' -d '{{\"code\": \"{out['code']}\"}}'"}

        @mcp.tool(title="Pair a companion", description="Create a one-time pairing code valid for ten minutes. Enter it at the hidden prompt for swb companion connect <url>.", annotations=WRITE)
        async def companion_code(ctx: Context) -> dict[str, Any]:
            p = kit.who(ctx)
            if not (url := core.cfg.issuer):
                raise ToolError("Set a public SwB address first.")
            return core.companions.mint(url, origin=p.origin, source=p.source, client=p.client_id)

        @mcp.tool(title="List companions", description="List paired devices, their connection status, and their available actions.", annotations=READ)
        async def companion_list() -> Annotated[CallToolResult, dict[str, Any]]:
            rows = []
            remaining = DEVICE_OUTPUT_LIMIT - len('{"companions":[],"trust":"external"}')
            for row in core.companions.list(details=True):
                framed = frame_companion(row, limit=remaining - bool(rows))
                remaining -= len(to_json(framed)) + bool(rows)
                rows.append(framed)
            return device_output({"companions": rows, "trust": "external"})

        @mcp.tool(title="Run a companion action", description="Run an advertised capability action on a connected device. Use the device ID and action schema from companion_list.", annotations=SEND)
        async def companion_invoke(ctx: Context, companion: str, capability: str, action: str, args: dict[str, Any] | None = None,
                                   timeout_s: Annotated[float, Field(gt=0, le=120)] = 30) -> Annotated[CallToolResult, dict[str, Any]]:
            result = await kit.run(core.companions.invoke(companion, capability, action, args or {}, timeout_s, principal=kit.who(ctx)))
            file_data = None
            if (capability, action) == ("drop", "get"):
                if set(result) != {"name", "content_b64"} or not valid_name(result["name"]):
                    raise ToolError("Invalid Drop file result.")
                file_data = result["content_b64"]
                try:
                    decode_file_content(file_data)
                except CapabilityError as e:
                    raise ToolError(str(e)) from e
                result = {"name": result["name"]}
            framed = frame_device(result, f"{capability}.{action}", sender=companion,
                                  exact=DEVICE_ACTION_IDS.get((capability, action)))
            return device_output({"trust": "external", "from": f"companion {companion} {capability}.{action}",
                                  "result": framed}, file_data)

        @mcp.tool(title="Disconnect a companion", description="Revoke a paired device key and close its connection.", annotations=DELETE)
        async def companion_revoke(ctx: Context, companion: str) -> dict[str, Any]:
            p = kit.who(ctx)
            return {"ok": core.companions.revoke(companion, origin="owner", source=p.source, client=p.client_id)}

        @mcp.tool(title="Who can reach what", description="List the resource names and visibility rules for targets, channels, memory and routes. Tools use private visibility. Use a listed resource with visibility_set.", annotations=READ)
        async def visibility_list() -> dict[str, Any]:
            return {"defaults": {k: v if isinstance(v, str) else list(v) for k, v in core.visibility.defaults.items()},
                    "resources": core.visibility.rows(await core.visibility_resources())}

        @mcp.tool(title="Make a resource private or shared", description="Owner only. Use a resource from visibility_list such as target:beta or channel:github/acme/app. Tools stay private. Audited.", annotations=WRITE_IDEMPOTENT)
        async def visibility_set(ctx: Context, resource: str, visibility: Annotated[str | list[str], Field(description="private, shared, or a list of verified sender IDs or workspace labels, such as ['github:1234']")]) -> dict[str, Any]:
            from ...visibility import VisibilityError

            p = kit.who(ctx)
            try:
                resource, rule = await core.set_visibility(resource, visibility, f"{p.kind}:{p.source}")
            except VisibilityError as e:
                raise ToolError(str(e)) from e
            core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination="core", action="visibility_set", outcome="ok",
                              target=resource, detail=str(rule))
            return {"resource": resource, "visibility": rule}

        @mcp.tool(title="Connected agents", description="Agents connected with a pairing code: their key ids, project, client, when they last called, and who minted the code. Keys themselves are never shown again.", annotations=READ)
        async def connect_list(include_revoked: bool = False) -> dict[str, Any]:
            return {"keys": [{**row, **{field: iso(row[field]) for field in ("created_at", "last_used_at", "revoked_at")}}
                             for row in core.connections.list(include_revoked)]}

        @mcp.tool(title="Revoke a connected agent", description="Revoke one connected key (key_id) or every key of an agent. The agent's next call is refused; its memory and conversations stay. Audited.", annotations=DELETE)
        async def connect_revoke(ctx: Context, key_id: str | None = None, agent: str | None = None) -> dict[str, Any]:
            from ...connect import ConnectError

            p = kit.who(ctx)
            try:
                n = core.connections.revoke(key_id=key_id, agent=agent)
            except ConnectError as e:
                raise ToolError(str(e)) from e
            core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination="core", action="connect_revoke",
                              outcome="refused" if n is None else "ok", target=None if n is None else key_id or agent)
            if n is None:
                raise ToolError("Unknown key ID. Check connect_list.")
            return {"revoked": n}

        if not self.aliases:
            return

        @mcp.tool(
            title="Message another agent",
            description=kit.deprecated("send(to='agent:<name>')", "Leave a message in another agent's mailbox (to='claude', 'chatgpt', 'gemini', 'claude-code'). It waits until that agent next calls wait or inbox. You are named as the sender from your sign-in."),
            annotations=SEND,
        )
        async def agent_send(ctx: Context, to: Annotated[str, Field(description="The agent's name, as switchboard_status shows it")], text: str,
                             project: ProjectArg = None, in_reply_to: str | None = None, forward: str | None = None,
                             conversation: str | None = None) -> dict[str, Any]:
            if in_reply_to is not None or forward is not None:
                raise ToolError("use reply to answer a conversation or send to forward one")
            p = kit.alias(ctx, "agent_send")
            if conversation is not None:
                return await kit.run(core.notify_mode_owner(p, conversation, text, to=to))
            if project:
                kit.need(p, kit.pscope(project))
            if p.targets is not None:
                await kit.run(core.check_target(p, f"agent:{to}", project))
            return await kit.run(core.agent_send(p, to=to, text=text, project=project))
