from __future__ import annotations

import re
import socket
import time
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, TypedDict
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .core import Switchboard

_STARTED_AT = time.monotonic()


class RuntimeInfo(TypedDict):
    mcp_url: str | None
    online: bool
    host: str
    version: str | None
    uptime_seconds: int


def runtime_info(core: Switchboard) -> RuntimeInfo:
    try:
        release = version("switchboard")
    except PackageNotFoundError:
        release = None
    mcp = next((block for block in core.blocks.values() if block.manifest.id == "mcp"), None)
    url = "/mcp" if mcp else None
    if mcp and (public_url := core.cfg.public_url):
        try:
            parts = urlsplit(public_url)
            if (parts.scheme in ("http", "https") and parts.hostname and parts.username is None and parts.port != 0
                    and not re.search(r"[\s\\?#]", public_url)):
                url = core.cfg.resource
        except ValueError:
            pass
    return {
        "mcp_url": url, "online": bool(mcp and mcp.online), "host": socket.gethostname(), "version": release,
        "uptime_seconds": int(max(0, time.monotonic() - _STARTED_AT)),
    }
