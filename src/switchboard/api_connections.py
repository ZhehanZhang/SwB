from __future__ import annotations

from hashlib import sha256
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .blocks import Block
from .blocks.discord import DiscordBlock
from .blocks.github import GitHubBlock
from .blocks.slack import SlackBlock
from .blocks.telegram import TelegramBlock
from .chat import ChatBlock
from .guard import audit_client_ip

BLOCK_TYPES = {"discord": DiscordBlock, "github": GitHubBlock, "slack": SlackBlock, "telegram": TelegramBlock}
AVATAR_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}


def _block(request: Request) -> tuple[str, Block | None]:
    platform = request.path_params["platform"]
    kind = BLOCK_TYPES.get(platform)
    if kind is None:
        raise HTTPException(404)
    blocks = request.app.state.core.blocks
    exact = blocks.get(platform)
    return platform, exact if isinstance(exact, kind) else next((b for b in blocks.values() if isinstance(b, kind)), None)


def _avatar(platform: str, status: dict[str, Any], bot_id: str | int | None) -> dict[str, Any]:
    avatar = status["avatar"]
    return {
        "file": avatar["file"],
        "state": "applied" if avatar["applied"] else "failed" if avatar["error"] else "pending" if avatar["file"] else "not_configured",
        "url": f"/api/connections/{platform}/avatar" if avatar["applied"] and bot_id is not None else None,
    }


def _discord(block: DiscordBlock, status: dict[str, Any]) -> dict[str, Any]:
    discovered = status["discovered"]
    setup = status["setup"]
    owner_ids = set(discovered["application_owner_ids"])
    owner_ids.update(owner for space in block.spaces.values() for owner in block.owners(space))
    guilds = []
    for guild in discovered["guilds"]:
        label = guild["space"]
        space = block.spaces.get(label)
        listen = set(status["spaces"][label]["listen"]) if label is not None else set()
        channels = []
        for channel in guild["channels"]:
            ident, name = channel["id"], channel["name"]
            project = None
            if space is not None:
                project = space.channel_projects.get(f"#{name}", space.project) if name else space.project
                project = space.channel_projects.get(ident, project)
            channels.append({"id": ident, "name": name, "project": project, "listening": ident in listen})
        guilds.append({"id": guild["id"], "name": guild["name"], "space": label, "owner_id": guild["owner_id"], "channels": channels})
    return {
        "bot_name": setup["bot_name"],
        "invite_url": setup["invite_url"],
        "owner_ids": sorted(owner_ids),
        "guilds": guilds,
        "avatar": _avatar("discord", status, setup["bot_user_id"]),
    }


def _telegram(block: TelegramBlock, status: dict[str, Any]) -> dict[str, Any]:
    setup = status["setup"]
    bot = setup["bot"]
    chats = []
    for chat in setup["seen_chats"]:
        ident, kind = str(chat["id"]), chat["type"]
        label = (block.dm_space if kind == "private" else block._groups.get(chat["id"])
                 if kind in ("group", "supergroup") else None)
        listen = status["spaces"][label]["listen"] if label is not None else ()
        state = "direct" if kind == "private" and label is not None else "listening" if ident in listen else "unlisted"
        chats.append({
            "id": ident, "title": chat["title"], "type": kind, "space": label,
            "listen": state,
        })
    return {
        "bot_name": (bot["name"] or f"@{bot['username']}") if bot else None,
        "owner_ids": sorted({owner for space in block.spaces.values() for owner in block.owners(space)}),
        "chats": chats,
        "avatar": _avatar("telegram", status, bot["id"] if bot else None),
    }


async def connection(request: Request) -> JSONResponse:
    platform, block = _block(request)
    details = None
    connected = False
    state = None
    if block is not None:
        status = block.status()
        if isinstance(block, DiscordBlock):
            connected = status["connected"]
            details = _discord(block, status)
        elif isinstance(block, TelegramBlock):
            connected = status["connected"]
            details = _telegram(block, status)
        elif isinstance(block, GitHubBlock):
            state = status["state"]
            details = {"repositories": sorted(block.registered_repositories()), "credential": status["credential"],
                       "dropped": status["dropped"], "refused": status["refused"]}
        else:
            workspaces = [
                {"name": name, "intake": ws["intake"], "connected": ws["connected"]}
                for name, ws in sorted(status.get("workspaces", {}).items())
            ]
            connected = any(ws["connected"] for ws in workspaces)
            details = {"workspaces": workspaces}
    state = state or ("not_configured" if block is None else "connected" if connected else "disconnected")
    return JSONResponse({"platform": platform, "block": block.name if block is not None else None, "state": state, "details": details})


def avatar(request: Request) -> Response:
    _, block = _block(request)
    if not isinstance(block, ChatBlock):
        raise HTTPException(404)
    status = block.status()
    photo, setup = status["avatar"], status["setup"]
    bot_id = setup.get("bot_user_id") if isinstance(block, DiscordBlock) else (setup["bot"] or {}).get("id")
    if not photo["file"] or not photo["applied"] or bot_id is None:
        raise HTTPException(404)
    core = block.core
    path = core.find_avatar(photo["file"])
    media = AVATAR_TYPES.get(path.suffix) if path is not None else None
    applied = core.store.one("SELECT value FROM block_state WHERE block = ? AND key = ?", (block.name, f"avatar:{bot_id}"))
    if path is None or media is None or not applied:
        raise HTTPException(404)
    try:
        data = path.read_bytes()
    except OSError:
        raise HTTPException(404) from None
    if sha256(data).hexdigest() != applied["value"]:
        raise HTTPException(404)
    return Response(data, media_type=media)


async def companions(request: Request) -> JSONResponse:
    return JSONResponse({"companions": request.app.state.core.companions.list()})


async def companion_revoke(request: Request) -> JSONResponse:
    registry = request.app.state.core.companions
    if not registry.revoke(request.path_params["companion"], origin="owner", source=f"admin:{request.state.owner.label}",
                           client=audit_client_ip(request.scope)):
        raise HTTPException(404)
    return JSONResponse({"ok": True})


CONNECTION_ROUTES = [Route("/connections/{platform}", connection), Route("/connections/{platform}/avatar", avatar),
                     Route("/companions", companions), Route("/companions/{companion}/revoke", companion_revoke, methods=["POST"])]
