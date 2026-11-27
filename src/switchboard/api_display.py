"""Human-readable labels for Capy identities in the owner console."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from .routes import route_audit_name
from .slack.config import AVATAR_RE

if TYPE_CHECKING:
    from .core import Switchboard

_JAM = r"jam_[A-Za-z0-9_-]+"
_JAM_ID = re.compile(rf"\b{_JAM}", re.IGNORECASE)
_ADDRESS = re.compile(rf"(?:(?:(?P<block>[\w-]+):)?(?P<kind>captain|thread):)?(?P<id>{_JAM})", re.IGNORECASE)
_DESCRIPTION = re.compile(rf"(?P<kind>captain|thread) (?:(?P<name>.+) \((?P<named_id>{_JAM})\)|(?P<id>{_JAM}))", re.IGNORECASE)


async def captain_targets(core: Switchboard, destinations: Iterable[str]) -> dict[str, dict[str, Any]]:
    return {
        target["address"]: target
        for name in set(destinations)
        if (block := core.blocks.get(name)) and block.manifest.id == "capy"
        for target in await core.targets(block=name, kind="captain")
    }


def _captain(parts: dict[str, str | None], destination: str, targets: Mapping[str, dict[str, Any]]) -> dict[str, Any] | None:
    if (parts.get("kind") or "").lower() != "thread":
        block = parts.get("block") or destination
        ident = parts.get("named_id") or parts["id"]
        return targets.get(f"{block}:captain:{ident}")
    return None


def captain_target(value: str | None, destination: str, targets: Mapping[str, dict[str, Any]]) -> dict[str, Any] | None:
    if value and (match := _ADDRESS.fullmatch(value) or _DESCRIPTION.fullmatch(value)):
        return _captain(match.groupdict(), destination, targets)
    return None


def captain_avatar_url(core: Switchboard, target: Mapping[str, Any]) -> str | None:
    if target.get("kind") != "captain" or not isinstance(url := target.get("avatar_url"), str):
        return None
    if base := core.avatars.public_base_url:
        url = url.removeprefix(base)
    path, separator, version = url.partition("?")
    if (path.startswith("/avatars/") and AVATAR_RE.fullmatch(path.removeprefix("/avatars/"))
            and (not separator or re.fullmatch(r"v=[0-9]{1,20}", version))):
        return url
    return None


def capy_label(value: str | None, destination: str, targets: Mapping[str, dict[str, Any]],
               *, name: str | None = None) -> str | None:
    if not value or not (match := _ADDRESS.fullmatch(value) or _DESCRIPTION.fullmatch(value)):
        return None
    parts = match.groupdict()
    kind = (parts.get("kind") or "").lower()
    captain = _captain(parts, destination, targets) or {}
    for candidate in (captain.get("name"), name, parts.get("name")):
        if (label := route_audit_name(candidate)) and not _JAM_ID.search(label):
            return label
    return "Captain" if kind == "captain" or captain else "Capy thread"
