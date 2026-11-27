from __future__ import annotations

from pathlib import Path

from ..mcp_config import LocalServer
from ..state import CompanionError
from .base import Capability
from .drop import DropCapability
from .notify import NotifyCapability

CAPABILITIES = ("notify", "drop", "muse", "mcp")


def registry(enabled: tuple[str, ...], drop_dir: Path, servers: dict[str, LocalServer] | None = None) -> tuple[dict[str, Capability], dict[str, str]]:
    available = {}
    unavailable = {}
    for cap_id in dict.fromkeys(enabled):
        if cap_id == "drop":
            cap = DropCapability(drop_dir)
        elif cap_id == "notify":
            cap = NotifyCapability()
        elif cap_id == "mcp":
            from .mcp import MCPCapability

            cap = MCPCapability(servers or {})
        elif cap_id == "muse":
            try:
                from .muse import MuseCapability
            except ImportError:
                unavailable[cap_id] = "Install swb-one[companion] to use Muse."
                continue
            cap = MuseCapability()
        else:
            raise CompanionError("Choose a capability from notify, drop, muse, or mcp.")
        if reason := cap.available():
            unavailable[cap_id] = reason
        else:
            available[cap_id] = cap
    return available, unavailable
