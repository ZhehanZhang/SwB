"""Target addresses and slugs: <block>:<kind>:<id>, agent:<name> for an agent's mailbox, project:<slug> for a route.

Every block's targets, the agents switchboard knows and its projects form one list. Each has slugs ("@swb <slug>"
in Slack, or send(to=<slug>)). On a slug collision the higher priority wins: targets.priority in the config lists
patterns ("capy:captain", "agent", "project"); without it, block targets beat agents and agents beat projects.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .blocks import Address

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
NOT_TARGETS = frozenset({"slack", "local"})
LEGACY_KINDS = ("captain", "project", "thread")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]


def rank(t: Mapping[str, Any], priority: Iterable[str] = ()) -> int:
    """Lower wins. Listed patterns first, in order; then block targets, agents, projects."""
    keys = (f"{t['block']}:{t['kind']}", t["block"], t["kind"])
    for i, pat in enumerate(priority):
        if pat in keys:
            return i
    base = 1000 + (0 if t["block"] not in ("agent", "project") else 1 if t["block"] == "agent" else 2)
    return base


def by_slug(targets: Iterable[Mapping[str, Any]], priority: Iterable[str] = (), aliases: Mapping[str, str] | None = None) -> dict[str, Mapping[str, Any]]:
    priority = tuple(priority)
    found: dict[str, list[Mapping[str, Any]]] = {}
    for t in targets:
        for s in t.get("slugs") or ():
            if SLUG_RE.match(s):
                found.setdefault(s, []).append(t)
    out = {slug: min(ts, key=lambda t: rank(t, priority)) for slug, ts in found.items()}
    for alias, slug in (aliases or {}).items():
        if slug in out:
            out[alias] = out[slug]
        elif ":" in slug:
            hit = next((t for t in targets if t["address"] == slug), None)
            if hit is not None:
                out[alias] = hit
    return out


def parse(text: str, blocks: Iterable[str]) -> Address | None:
    """An explicit address, or None for a bare word (a slug or an agent name the caller resolves)."""
    text = (text or "").strip()
    head, sep, rest = text.partition(":")
    if not sep:
        return None
    head = head.lower()
    if head == "agent":
        return Address("agent", "agent", rest.strip().lower())
    if head in set(blocks):
        kind, sep2, ident = rest.partition(":")
        return Address(head, kind.strip().lower(), ident.strip()) if sep2 else Address(head, "", rest.strip())
    if head == "project":
        return Address("project", "project", rest.strip())
    return None
