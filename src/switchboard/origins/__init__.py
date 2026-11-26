"""Origins: where requests come from. Each one is an inbound adapter behind the Origin interface.

An origin authenticates its callers into a Principal and calls the core: core operations
(memory, projects, routes, context) and destination actions via Switchboard.invoke. It never
imports a destination. Add an origin by subclassing Origin, listing it in ORIGINS, and configuring
it under `origins.<name>`.
"""

from __future__ import annotations

import importlib
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from starlette.routing import BaseRoute

if TYPE_CHECKING:
    from ..core import Switchboard

ORIGINS = {
    "mcp": "switchboard.origins.mcp:MCPOrigin",
    "slack": "switchboard.origins.slack:SlackOrigin",
}


class Origin:
    name = ""

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        self.core = core
        self.config = config

    def routes(self) -> list[BaseRoute]:
        """HTTP routes this origin serves (mounted on the one Starlette app)."""
        return []

    def wrap(self, app: Any) -> Any:
        """Optional ASGI middleware, e.g. the MCP origin's bearer gate."""
        return app

    def lifespan(self) -> AbstractAsyncContextManager[None] | None:
        return None

    def problems(self) -> list[str]:
        return []


def load(name: str) -> type[Origin]:
    if name not in ORIGINS:
        raise ValueError(f"unknown origin {name!r}; known: {', '.join(ORIGINS)}")
    module, _, cls = ORIGINS[name].partition(":")
    return getattr(importlib.import_module(module), cls)
