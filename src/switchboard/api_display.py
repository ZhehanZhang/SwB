"""Human-readable labels for Capy identities in the owner console."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from contextlib import suppress
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from .routes import route_audit_name
from .slack.config import AVATAR_RE

if TYPE_CHECKING:
    from .core import Switchboard

_JAM = r"jam_[A-Za-z0-9_-]+"
_JAM_ID = re.compile(rf"\b{_JAM}", re.IGNORECASE)
_ADDRESS = re.compile(rf"(?:(?:(?P<block>[\w-]+):)?(?P<kind>captain|thread):)?(?P<id>{_JAM})", re.IGNORECASE)
_DESCRIPTION = re.compile(rf"(?P<kind>captain|thread) (?:(?P<name>.+) \((?P<named_id>{_JAM})\)|(?P<id>{_JAM}))", re.IGNORECASE)
SLACK_PROFILE_PREFIX = "slack-profile-"
CAPY_AVATAR_URL = "/avatars/capy.png"


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


def conversation_captain(value: str | None, destination: str, kind: str | None,
                         targets: Mapping[str, dict[str, Any]]) -> dict[str, Any] | None:
    if target := captain_target(value, destination, targets):
        return target
    if not value:
        return None
    if match := _ADDRESS.fullmatch(value) or _DESCRIPTION.fullmatch(value):
        parts = match.groupdict()
        if parts.get("block") or (parts.get("kind") or "").casefold() == "thread":
            return None
        ident = parts.get("named_id") or parts["id"]
        matches = [t for t in targets.values() if t["id"] == ident]
    elif kind == "captain":
        matches = [t for t in targets.values() if value.casefold() in t.get("slugs", ())]
    else:
        return None
    return matches[0] if len(matches) == 1 else None


def display_name(value: Any) -> str | None:
    return route_audit_name(value.strip()) if isinstance(value, str) else None


def local_avatar_url(core: Switchboard, url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    if base := core.avatars.public_base_url:
        url = url.removeprefix(base)
    path, separator, version = url.partition("?")
    if (path.startswith("/avatars/") and AVATAR_RE.fullmatch(path.removeprefix("/avatars/"))
            and (not separator or re.fullmatch(r"v=[0-9]{1,20}", version))):
        return url
    return None


def captain_avatar_url(core: Switchboard, target: Mapping[str, Any]) -> str | None:
    if target.get("kind") != "captain":
        return None
    url = target.get("avatar_url")
    if url is None and core.avatars.public_base_url is None:
        persona = target.get("persona")
        name = persona.get("avatar_file") if isinstance(persona, Mapping) else None
        if isinstance(name, str) and (file := core.find_avatar(name)):
            try:
                return f"/avatars/{name}?v={file.stat().st_mtime_ns}"
            except FileNotFoundError:
                return None
    return local_avatar_url(core, url)


async def slack_profile_avatar(core: Switchboard, url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc not in ("avatars.slack-edge.com", "a.slack-edge.com"):
            return None
        name = SLACK_PROFILE_PREFIX + hashlib.sha256(url.encode()).hexdigest()[:32]
        images = {"png": (b"\x89PNG\r\n\x1a\n",), "jpg": (b"\xff\xd8\xff",), "gif": (b"GIF87a", b"GIF89a")}
        for ext in images:
            if (core.avatar_cache / f"{name}.{ext}").is_file():
                return f"/avatars/{name}.{ext}"
        async with core.avatar_http.stream("GET", url, follow_redirects=False, timeout=3) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes(64 * 1024):
                data.extend(chunk)
                if len(data) > 2 * 1024 * 1024:
                    return None
        if ext := next((ext for ext, prefixes in images.items() if data.startswith(prefixes)), None):
            name = f"{name}.{ext}"
            core.avatar_cache.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=core.avatar_cache) as tmp:
                tmp.write(data)
                tmp.flush()
                with suppress(FileExistsError):
                    (core.avatar_cache / name).hardlink_to(tmp.name)
            return f"/avatars/{name}"
    except (OSError, ValueError, httpx.HTTPError):
        return None
    return None


def capy_label(value: str | None, destination: str, targets: Mapping[str, dict[str, Any]],
               *, name: str | None = None) -> str | None:
    if not value or not (match := _ADDRESS.fullmatch(value) or _DESCRIPTION.fullmatch(value)):
        return None
    parts = match.groupdict()
    kind = (parts.get("kind") or "").lower()
    captain = _captain(parts, destination, targets) or {}
    for candidate in (captain.get("name"), name, parts.get("name")):
        if (label := display_name(candidate)) and not _JAM_ID.search(label):
            return label
    return "Captain" if kind == "captain" or captain else "Capy thread"
