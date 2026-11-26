"""Blocks: every way into and out of switchboard is one Block behind one interface.

A block is an installed adapter instance (mcp, slack, capy, ...). It may take callers in (it serves routes, wraps the
app and authenticates them into a Principal), reach a system out (it declares typed actions the core runs through audit
and caps), or both. It declares what it is in a Manifest: its level on the capability ladder, the core verbs it
implements, the target kinds it offers, the agents and personas it provides, the secrets it needs and an instructions
fragment for the MCP server. A block reports what it hears back through the core (core.inbox, core.deliver) and never
imports another block.

The core knows only blocks, agents, identities, personas, targets (<block>:<kind>:<id>), messages, conversations,
routes, memory, approvals and audit. Add a block by subclassing Block, listing it in BLOCKS (or the switchboard.blocks
entry-point group) and configuring it under blocks.<name> with use: <type>.
"""

from __future__ import annotations

import importlib
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from starlette.routing import BaseRoute

if TYPE_CHECKING:
    from ..auth import Principal
    from ..core import Switchboard
    from ..projects import Project

BLOCKS = {
    "mcp": "switchboard.blocks.mcp:MCPBlock",
    "slack": "switchboard.blocks.slack:SlackBlock",
    "telegram": "switchboard.blocks.telegram:TelegramBlock",
    "github": "switchboard.blocks.github:GitHubBlock",
    "capy": "switchboard.blocks.capy:CapyBlock",
    "claude-routine": "switchboard.blocks.claude_routine:ClaudeRoutineBlock",
    "dots": "switchboard.blocks.dots:DotsBlock",
}
LEVELS = ("L0", "L1", "L2", "L3", "L4")
VERBS = ("send", "reply", "status", "result", "cancel", "targets")


class BlockError(Exception):
    """A refusal or failure the caller should see; the core records it in the audit log."""


@dataclass
class Action:
    name: str
    title: str
    description: str
    handler: Callable[..., Awaitable[Any]]
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    capped: bool = False
    audited: bool | None = None

    @property
    def is_audited(self) -> bool:
        return (not self.read_only) if self.audited is None else self.audited


@dataclass
class Call:
    """What a handler gets besides its own arguments: who is calling, through which block, and the core."""

    principal: "Principal"
    core: "Switchboard"
    destination: str
    action: str
    notes: dict[str, Any] = field(default_factory=dict)
    handle: str | None = None
    handle_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def block(self) -> str:
        return self.destination

    def note(self, **kw: Any) -> None:
        """Annotate the audit record for this call (target, ref, title, excerpt)."""
        self.notes.update({k: v for k, v in kw.items() if v is not None})


@dataclass(frozen=True)
class AgentSpec:
    """A caller identity a block or preset brings, and how an OAuth client is recognised as it."""

    id: str
    label: str
    redirect_uris: tuple[str, ...] = ()
    redirect_hosts: tuple[str, ...] = ()
    cimd_hosts: tuple[str, ...] = ()
    link_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items() if v}


@dataclass(frozen=True)
class TargetKind:
    kind: str
    slug_from: str | None = None
    persona: bool = False
    ref_prefix: str | None = None


@dataclass(frozen=True)
class Manifest:
    id: str
    title: str
    kind: str = "both"
    level_out: str | None = None
    level_in: str | None = None
    speaks: tuple[str, ...] = ("message", "status", "question", "result", "error")
    verbs: dict[str, str] = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    targets: tuple[TargetKind, ...] = ()
    agents: tuple[AgentSpec, ...] = ()
    personas: str | None = None
    link_keys: tuple[str, ...] = ()
    secrets: tuple[str, ...] = ()
    network: tuple[str, ...] = ()
    identities: tuple[dict[str, Any], ...] = ()
    config_schema: dict[str, Any] = field(default_factory=dict)
    instructions: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "title": self.title, "kind": self.kind,
                               "level": {"out": self.level_out, "in": self.level_in}, "speaks": list(self.speaks), "verbs": sorted(self.verbs)}
        if self.tools:
            out["tools"] = list(self.tools)
        if self.targets:
            out["targets"] = [{k: v for k, v in asdict(t).items() if v} for t in self.targets]
        provides: dict[str, Any] = {}
        if self.agents:
            provides["agents"] = [a.as_dict() for a in self.agents]
        if self.personas:
            provides["personas"] = self.personas
        if self.link_keys:
            provides["link_keys"] = list(self.link_keys)
        if provides:
            out["provides"] = provides
        needs = {k: list(v) for k, v in (("secrets", self.secrets), ("network", self.network)) if v}
        if needs:
            out["needs"] = needs
        if self.identities:
            out["identities"] = [dict(i) for i in self.identities]
        if self.config_schema:
            out["config_schema"] = self.config_schema
        if self.instructions:
            out["instructions"] = self.instructions
        return out


@dataclass(frozen=True)
class Address:
    """A target address: <block>:<kind>:<id>, or agent:<name> for an agent's mailbox."""

    block: str
    kind: str
    id: str

    def __str__(self) -> str:
        return f"agent:{self.id}" if self.block == "agent" else f"{self.block}:{self.kind}:{self.id}"


class Block:
    manifest = Manifest(id="", title="")
    wakes_agents = False
    """True for a block whose targets wake an agent (a Claude routine): a bare agent name goes to its target first."""

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        self.core = core
        self.config = config

    @property
    def name(self) -> str:
        return str(self.config.get("_name") or self.manifest.id)

    @property
    def title(self) -> str:
        return self.manifest.title

    # The in side: HTTP routes, middleware, lifespan ------------------------------------------

    def routes(self) -> list[BaseRoute]:
        """HTTP routes this block serves (mounted on the one Starlette app)."""
        return []

    def wrap(self, app: Any) -> Any:
        """Optional ASGI middleware, e.g. the MCP block's bearer gate."""
        return app

    def lifespan(self) -> AbstractAsyncContextManager[None] | None:
        return None

    # The out side: actions and verbs ----------------------------------------------------------

    def actions(self) -> list[Action]:
        return []

    def action(self, name: str) -> Action:
        for a in self.actions():
            if a.name == name:
                return a
        raise BlockError(f"{self.name} has no action {name!r}")

    def verb(self, verb: str) -> Action | None:
        name = self.manifest.verbs.get(verb)
        return self.action(name) if name else None

    def bind(self) -> None:
        """Called once every block exists, e.g. for the MCP block to collect the other blocks' tools."""

    def verb_args(self, verb: str, ref: str, extra: dict[str, Any]) -> dict[str, Any]:
        """The arguments of the action behind reply, status, result or cancel for one conversation."""
        return {"ref": ref, **extra}

    def mcp_tools(self, mcp: Any, kit: Any) -> None:
        """Register this block's own MCP tools (named <block>_<tool>) on the MCP block's server."""

    def send_args(self, target: Address | None, message: dict[str, Any]) -> dict[str, Any]:
        """The send action's arguments for a message to one of this block's targets (None: the route decides)."""
        if target is None:
            return dict(message)
        return {**message, "target": f"{target.kind}:{target.id}" if target.kind else target.id}

    def parse_route_target(self, text: str) -> dict[str, Any]:
        """A routes_set target as this block stores it: '<kind>:<id>' becomes {kind: id}."""
        kind, sep, ident = (text or "").strip().partition(":")
        if not sep or not ident.strip():
            raise BlockError(f"a {self.name} route target looks like <kind>:<id>")
        return {kind.strip().lower(): ident.strip()}

    def wake_target(self, agent: str, project: str) -> Address | None:
        """For a block that wakes agents: the target that wakes this agent for this project (a project routine), if any."""
        return None

    def owns_ref(self, ref: str) -> bool:
        """Whether a conversation reference (a thread id, say) belongs to this block."""
        return any(t.ref_prefix and ref.startswith(t.ref_prefix) for t in self.manifest.targets)

    async def targets(self) -> list[dict[str, Any]]:
        """Targets this block offers: {kind, id, name, slugs, persona?}. The core adds the address and the block."""
        return []

    async def render_avatar(self, persona: dict[str, Any], http: Any, out_dir: Any, remote: bool = False) -> str | None:
        """Draw a dynamic persona's picture into out_dir and return its file name, or None."""
        return None

    def route_address(self, target: dict[str, Any]) -> Address | None:
        """The address of a stored route target, e.g. {captain: jam_x} -> capy:captain:jam_x."""
        for k in self.manifest.targets:
            if target.get(k.kind):
                return Address(self.name, k.kind, str(target[k.kind]))
        return None

    @property
    def daily_cap(self) -> int | None:
        """Optional and off by default: a hard daily limit on capped actions."""
        cap = self.config.get("daily_cap")
        return int(cap) if cap is not None else None

    async def validate_route_target(self, target: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Normalize a route's target and defaults (resolve names to ids), or raise BlockError."""
        raise BlockError(f"{self.name} does not support routes")

    async def context_section(self, principal: "Principal", project: "Project | None") -> str | None:
        return None

    async def autolink(self, principal: "Principal", project: "Project", *, force: bool = False) -> dict[str, Any] | None:
        """Link a project to its counterpart here and route it, reporting changes and candidates. None: not supported."""
        return None

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        """Rows for the admin page's Blocks card: (label, hint, value, ok)."""
        return []

    # Both sides ------------------------------------------------------------------------------

    def problems(self) -> list[str]:
        return []

    def status(self) -> dict[str, Any]:
        return {}

    @property
    def online(self) -> bool:
        return True

    @property
    def has_in(self) -> bool:
        return self.manifest.kind in ("source", "both")

    @property
    def has_out(self) -> bool:
        return self.manifest.kind in ("sink", "both")

    @property
    def supports_routes(self) -> bool:
        return type(self).validate_route_target is not Block.validate_route_target

    async def start(self) -> None:
        """Begin background work (resume streams) once the event loop runs."""

    async def aclose(self) -> None:
        pass


def registry() -> dict[str, str]:
    out = dict(BLOCKS)
    try:
        for ep in entry_points(group="switchboard.blocks"):
            out.setdefault(ep.name, ep.value)
    except Exception:
        pass
    return out


def load(use: str) -> type[Block]:
    known = registry()
    if use not in known:
        raise ValueError(f"unknown block type {use!r}; known: {', '.join(sorted(known))}")
    module, _, cls = known[use].partition(":")
    return getattr(importlib.import_module(module), cls)
