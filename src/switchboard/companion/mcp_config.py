from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx2

from ..config import LOOPBACK_HOSTS

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}")
MAX_SERVERS = 16
MAX_SPECS_BYTES = 32 * 1024


@dataclass(frozen=True)
class LocalServer:
    argv: tuple[str, ...] = field(default=(), repr=False)
    url: str | None = field(default=None, repr=False)

    @property
    def kind(self) -> str:
        return "http" if self.url is not None else "stdio"


def local_servers(specs: tuple[str, ...]) -> dict[str, LocalServer]:
    out = {}
    try:
        if len(specs) > MAX_SERVERS or len(json.dumps(specs, ensure_ascii=False).encode("utf-8")) > MAX_SPECS_BYTES:
            raise ValueError
        for spec in specs:
            name, _, target = spec.partition("=")
            if not NAME.fullmatch(name) or name in out or not target.strip() or any(c in target for c in "\x00\r\n"):
                raise ValueError
            if re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", target.lstrip()):
                parsed = urlsplit(target)
                url = httpx2.URL(target)
                if (parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS or url.host not in LOOPBACK_HOSTS
                        or parsed.username is not None or parsed.port == 0 or re.search(r"[\x00-\x20\x7f\\#]", target)):
                    raise ValueError
                out[name] = LocalServer(url=str(url))
            else:
                argv = tuple(shlex.split(target))
                if not argv or not argv[0]:
                    raise ValueError
                out[name] = LocalServer(argv)
    except (ValueError, TypeError, httpx2.InvalidURL):
        raise ValueError("Use --mcp NAME=COMMAND or NAME=URL. HTTP servers must use loopback addresses.") from None
    return out
