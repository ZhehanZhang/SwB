"""Destinations: where the core sends work. Each one is a plugin behind the Destination interface.

A destination declares actions (name, description, typed async handler). It never talks to an origin
directly: origins expose actions (the MCP origin turns `delegate` on `capy` into the tool
`capy_delegate`), and the core routes every call through audit and caps first. Add a destination by
subclassing Destination, listing it in DESTINATIONS, and configuring it under `destinations.<name>`.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from ..auth import Principal
    from ..core import Switchboard
    from ..projects import Project

DESTINATIONS = {
    "capy": "switchboard.destinations.capy:CapyDestination",
}


class DestinationError(Exception):
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
    burst: bool = False
    audited: bool | None = None

    @property
    def is_audited(self) -> bool:
        return (not self.read_only) if self.audited is None else self.audited


@dataclass
class Call:
    """What a handler gets besides its own arguments: who is calling, through which origin, and the core."""

    principal: "Principal"
    core: "Switchboard"
    destination: str
    action: str
    notes: dict[str, Any] = field(default_factory=dict)

    def note(self, **kw: Any) -> None:
        """Annotate the audit record for this call (target, ref, title, excerpt)."""
        self.notes.update({k: v for k, v in kw.items() if v is not None})


class Destination:
    name = ""
    title = ""

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        self.core = core
        self.config = config

    def actions(self) -> list[Action]:
        return []

    def action(self, name: str) -> Action:
        for a in self.actions():
            if a.name == name:
                return a
        raise DestinationError(f"{self.name} has no action {name!r}")

    @property
    def daily_cap(self) -> int | None:
        """Optional and off by default: a hard daily limit on capped actions."""
        cap = self.config.get("daily_cap")
        return int(cap) if cap is not None else None

    @property
    def burst_limit(self) -> tuple[int, float] | None:
        """Runaway guard on burst actions: at most count calls per caller in window_s. null switches it off."""
        raw = self.config.get("burst", {"count": 10, "window_s": 600})
        return (int(raw.get("count", 10)), float(raw.get("window_s", 600))) if raw else None

    def problems(self) -> list[str]:
        return []

    def status(self) -> dict[str, Any]:
        return {}

    @property
    def online(self) -> bool:
        return True

    async def validate_route_target(self, target: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Normalize a route's target and defaults (resolve names to ids), or raise DestinationError."""
        raise DestinationError(f"{self.name} does not support routes")

    async def context_section(self, principal: "Principal", project: "Project | None") -> str | None:
        return None

    async def autolink(self, principal: "Principal", project: "Project", *, force: bool = False) -> dict[str, Any] | None:
        """Link a project to its counterpart here and route it, reporting changes and candidates. None: not supported."""
        return None

    async def start(self) -> None:
        """Begin background work (resume streams) once the event loop runs."""

    async def aclose(self) -> None:
        pass


def load(name: str) -> type[Destination]:
    if name not in DESTINATIONS:
        raise ValueError(f"unknown destination {name!r}; known: {', '.join(DESTINATIONS)}")
    module, _, cls = DESTINATIONS[name].partition(":")
    return getattr(importlib.import_module(module), cls)
