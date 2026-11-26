"""Slug routing for "@swb <slug> <text>": which Captain, agent or project a Slack message is for.

Targets come from the core (switchboard.targets): every block's targets (Captains, say), the agents switchboard
knows and its projects. On a slug collision block targets win over agents and agents over projects, unless the
config's targets.priority says otherwise; slack.target_aliases overrides both. build_targets is the same rule for
plain lists, kept for tests and scripts. A message with no matching slug goes to the thread's earlier target, else the workspace's
default_target.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from ..targets import slugify

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
KIND_ORDER = ("captain", "agent", "project")
LEAD_MENTION = re.compile(r"^\s*(?:<@[A-Z0-9]+(?:\|[^>]*)?>[\s,:]*)+")
NOT_TARGETS = frozenset({"slack", "local"})


@dataclass(frozen=True)
class Target:
    slug: str
    kind: str
    id: str
    name: str
    persona: str | None = None
    address: str | None = None
    project: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["Target", "build_targets", "near", "slugify", "split_slug"]


def split_slug(text: str, bot_user_id: str | None) -> tuple[str, str]:
    """('teamcaptain', 'rest of the message') for '<@BOT> TeamCaptain: rest of the message'; ('', text) without a candidate."""
    body = text or ""
    lead = LEAD_MENTION.match(body)
    if lead and (bot_user_id is None or f"<@{bot_user_id}" in lead.group(0)):
        body = body[lead.end():]
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9-]{0,39})(?:[:,]|\s+|$)", body)
    if not m:
        return "", body.strip()
    return m.group(1).lower(), body[m.end():].strip()


def build_targets(captains: Iterable[Mapping[str, Any]], agents: Iterable[str], projects: Iterable[Mapping[str, Any]],
                  aliases: Mapping[str, str] | None = None) -> dict[str, Target]:
    found: dict[str, list[Target]] = {}

    def add(t: Target) -> None:
        if SLUG_RE.match(t.slug):
            found.setdefault(t.slug, []).append(t)

    for c in captains:
        if c.get("archived"):
            continue
        t = Target(slugify(c.get("name") or ""), "captain", c["id"], c.get("name") or c["id"], persona=slugify(c.get("name") or ""))
        add(t)
        if c.get("code"):
            add(Target(str(c["code"]).lower(), "captain", c["id"], t.name, persona=t.persona))
    for a in agents:
        if a not in NOT_TARGETS:
            add(Target(a, "agent", a, a, persona=a))
    for p in projects:
        add(Target(p["slug"], "project", p["slug"], p.get("name") or p["slug"]))
    out = {slug: min(ts, key=lambda t: KIND_ORDER.index(t.kind)) for slug, ts in found.items()}
    for alias, slug in (aliases or {}).items():
        if slug in out:
            out[alias] = Target(alias, out[slug].kind, out[slug].id, out[slug].name, out[slug].persona)
    return out


def near(word: str, slugs: Iterable[str]) -> list[str]:
    """Slugs a word was probably meant to be (a typo), so an unknown slug gets a hint but ordinary words do not."""
    def distance(a: str, b: str) -> int:
        row = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            prev, row[0] = row[0], i
            for j, cb in enumerate(b, 1):
                prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
        return row[-1]

    if len(word) < 4:
        return []
    scored = sorted((distance(word, s), s) for s in slugs if len(s) >= 4)
    return [s for d, s in scored if d <= max(1, min(2, len(s) // 4))][:3]
