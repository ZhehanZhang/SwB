from __future__ import annotations

import asyncio
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .api_write import _Body, _error, _owner
from .blocks import BlockError
from .blocks.discord import DiscordBlock
from .blocks.slack import SlackBlock
from .blocks.telegram import TelegramBlock
from .bot_identity import DEFAULT_ICON, DEFAULT_NAME, MAX_IDENTITY_BODY, BotIdentity, normalize_icon

Chat = DiscordBlock | SlackBlock | TelegramBlock


class _IdentityBody(_Body):
    space: str | None = None
    name: str | None = None
    icon: str | None = None
    reset: bool = False


def _spaces(block: Chat) -> dict[str, str]:
    if isinstance(block, DiscordBlock):
        guilds = {**block.guild_spaces, **{guild: block.space_for(guild) for guild in block.guilds}}
        return {label: block.guild_info.get(guild, {}).get("name") or label for guild, label in guilds.items() if label}
    if isinstance(block, SlackBlock):
        return {label: label for label in block.engine.config.workspaces} if block.engine is not None else {}
    return {label: label for label in block.spaces}


def _capabilities(block: Chat, space: str) -> tuple[bool, str | None]:
    if isinstance(block, SlackBlock):
        return block.bot_identity_capabilities(space or None)
    return bool(block.token()) and (not space or isinstance(block, DiscordBlock)), None


def _icon_allowed(block: Chat) -> bool:
    return not isinstance(block, SlackBlock) or bool(block.engine and block.identity_public_url())


def _current(block: Chat) -> BotIdentity:
    if isinstance(block, DiscordBlock):
        name = block.bot_name
    elif isinstance(block, TelegramBlock):
        name = (block._bot or {}).get("name")
    else:
        return BotIdentity(DEFAULT_NAME, DEFAULT_ICON)
    avatar = block._avatar_status
    return BotIdentity(name or DEFAULT_NAME, avatar["file"] if avatar["applied"] and avatar["file"] else DEFAULT_ICON)


def _snapshot(block: Chat, spaces: dict[str, str]) -> dict[str, Any]:
    registry = block.core.bot_identities
    default = BotIdentity(DEFAULT_NAME, DEFAULT_ICON)
    current = _current(block)
    stored = registry.get(block.name)
    base = BotIdentity(stored.name or current.name, stored.icon or current.icon) if stored else current

    def identity(space: str) -> dict[str, Any]:
        own = registry.get(block.name, space)
        effective = BotIdentity(own.name or base.name, own.icon or base.icon) if own else base
        icon_url = f"/avatars/{effective.icon}"
        if effective.icon != DEFAULT_ICON and not registry.icon_path(effective.icon) and not isinstance(block, SlackBlock):
            icon_url = f"/api/connections/{block.manifest.id}/avatar"
        allowed, url = _capabilities(block, space)
        return {"name": effective.name, "icon_url": icon_url, "can_edit_icon": allowed and _icon_allowed(block),
                "can_edit": allowed, "permissions_url": url, "overridden": own is not None if space else effective != default}

    return {"block": block.name, "platform": block.manifest.id, "identity": identity(""),
            "spaces": [{"id": space, "name": label, "identity": identity(space)}
                       for space, label in sorted(spaces.items())]}


async def _input(request: Request) -> _IdentityBody | None:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return None
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_IDENTITY_BODY:
            return None
        raw.extend(chunk)
    try:
        return _IdentityBody.model_validate_json(raw)
    except (ValidationError, ValueError):
        return None


async def bot_identity(request: Request) -> JSONResponse:
    core = request.app.state.core
    block = core.blocks.get(request.path_params["block"])
    if not isinstance(block, (DiscordBlock, SlackBlock, TelegramBlock)):
        return _error("identity_unavailable", 404)
    spaces = _spaces(block)
    if request.method in ("GET", "HEAD"):
        return JSONResponse(_snapshot(block, spaces))
    body = await _input(request)
    if body is None or (body.space is not None and body.space not in spaces) or (
        body.reset and (body.name is not None or body.icon is not None)
    ) or (not body.reset and body.name is None and body.icon is None):
        return _error("invalid_identity")
    space = body.space or ""
    if not _capabilities(block, space)[0] or (body.icon is not None and not _icon_allowed(block)):
        return _error("missing_permission", 403)
    name = body.name.strip() if body.name is not None else None
    min_length, max_length = (1 if space else 2, 32) if isinstance(block, DiscordBlock) else (
        (1, 64) if isinstance(block, TelegramBlock) else (1, 80)
    )
    if name is not None and (not min_length <= len(name) <= max_length or not name.isprintable()):
        return _error("invalid_identity")
    try:
        avatar = await asyncio.to_thread(normalize_icon, body.icon) if body.icon is not None else None
    except ValueError:
        return _error("invalid_icon")
    registry = core.bot_identities
    async with registry.write_lock:
        prior = registry.get(block.name, space)
        base = registry.get(block.name) or _current(block)
        current = prior or (BotIdentity("", "") if isinstance(block, SlackBlock) and space else base)
        next_name = DEFAULT_NAME if body.reset else name or current.name
        try:
            icon = registry.save_icon(avatar) if avatar is not None else DEFAULT_ICON if body.reset else current.icon
            if not isinstance(block, SlackBlock):
                if body.reset and space:
                    await block.update_bot_identity(None, None, space=space, reset=True)
                else:
                    if avatar is None and (body.reset or (space and prior is None)):
                        file = core.find_avatar(icon)
                        if file is None:
                            return _error("invalid_icon")
                        avatar = file.read_bytes()
                    changed_name = next_name if body.reset or name is not None or (space and prior is None) else None
                    await block.update_bot_identity(changed_name, avatar, space=space or None, reset=body.reset)
            registry.set(block.name, space, None if body.reset and space else BotIdentity(next_name, icon))
        except (BlockError, OSError):
            return _error("identity_not_saved", 502)
        principal = _owner(request)
        core.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id,
                          destination=block.name, action="bot_identity.reset" if body.reset else "bot_identity.set",
                          outcome="ok", target=space or block.name)
    return JSONResponse(_snapshot(block, spaces))


BOT_IDENTITY_ROUTES = [Route("/bot-identities/{block}", bot_identity, methods=["GET", "POST"])]
