from __future__ import annotations

from importlib import import_module
from pathlib import Path

from ..mcp_config import LocalServer
from ..state import CompanionError
from .base import Capability
from .drop import DropCapability
from .notify import NotifyCapability

CAPABILITIES = ("notify", "drop", "muse", "mcp", "hooks", "herdr", "codex", "opencode", "dsh")
HARNESSES = {
    "hooks": "HooksCapability", "herdr": "HerdrCapability", "codex": "CodexCapability", "opencode": "OpenCodeCapability",
    "dsh": "DshCapability",
}


def registry(enabled: tuple[str, ...], drop_dir: Path, servers: dict[str, LocalServer] | None = None,
             *, memory_sync: bool = True) -> tuple[dict[str, Capability], dict[str, str]]:
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
        elif cap_id in HARNESSES:
            module = import_module(f".{cap_id}", __name__)
            cap = module.HooksCapability(memory_sync=memory_sync) if cap_id == "hooks" else getattr(module, HARNESSES[cap_id])()
        else:
            raise CompanionError(f"Choose a capability from {', '.join(CAPABILITIES)}.")
        if reason := cap.available():
            unavailable[cap_id] = reason
        else:
            available[cap_id] = cap
    return available, unavailable
