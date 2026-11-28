"""Target addresses and slugs: <block>:<kind>:<id>, agent:<name> for an agent's mailbox, project:<slug> for a route.

Every block's targets, the agents switchboard knows and its projects form one list. Each has slugs ("@swb <slug>"
in Slack, or send(to=<slug>)). On a slug collision the higher priority wins: targets.priority in the config lists
patterns ("capy:captain", "agent", "project"); without it, block targets beat agents and agents beat projects.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .blocks import Address, BlockError
from .routes import route_audit_name
from .trust import neutralize

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
NOT_TARGETS = frozenset({"slack", "local", "discord", "telegram"})
LEGACY_KINDS = ("captain", "project", "thread")
CAPTAIN_SLUG = "captain"


class CaptainTargetError(BlockError, ValueError):
    pass


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


def captain_choices(targets: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {t["address"]: t for t in targets if t["kind"] == CAPTAIN_SLUG}


def captain_selection_error(targets: Iterable[Mapping[str, Any]], slugs: Mapping[str, Mapping[str, Any]],
                            *, reveal: bool = True) -> str:
    choices = captain_choices(targets)
    if not choices:
        return "Connect a Captain or choose another target."
    message = "Choose a Captain by alias or full address."
    if not reveal:
        return message
    labels = []
    for address, row in sorted(choices.items()):
        aliases = sorted(slug for slug, target in slugs.items()
                         if slug != CAPTAIN_SLUG and SLUG_RE.fullmatch(slug) and target["address"] == address)
        name = route_audit_name(row.get("name")) or "Captain"
        name = neutralize(re.sub(r"[<>&@*_~]", "", name)).strip() or "Captain"
        labels.append(f"{name} ({' / '.join(aliases) or address})")
    return f"{message} Available Captains are {', '.join(labels)}."


def by_slug(targets: Iterable[Mapping[str, Any]], priority: Iterable[str] = (), aliases: Mapping[str, str] | None = None) -> dict[str, Mapping[str, Any]]:
    targets = tuple(targets)
    priority = tuple(priority)
    found: dict[str, list[Mapping[str, Any]]] = {}
    for t in targets:
        for s in t.get("slugs") or ():
            if s != CAPTAIN_SLUG and SLUG_RE.fullmatch(s):
                found.setdefault(s, []).append(t)
    out = {slug: min(ts, key=lambda t: rank(t, priority)) for slug, ts in found.items()}
    choices = captain_choices(targets)
    if len(choices) == 1:
        out[CAPTAIN_SLUG] = next(iter(choices.values()))
    for name, slug in (aliases or {}).items():
        alias = name.casefold()
        if alias == CAPTAIN_SLUG:
            continue
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
