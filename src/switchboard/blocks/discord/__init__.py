"""Discord message intake and bot or persona posts through the Discord API."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
from io import BytesIO
from typing import TYPE_CHECKING, Any, AsyncIterator, NamedTuple
from urllib.parse import quote, urlencode, urlsplit

import httpx
from PIL import Image

from .. import BlockError, Manifest
from ...chat import ChatBlock, Incoming, Persona
from ...env import env_value
from . import rest
from .gateway import Gateway

if TYPE_CHECKING:
    from ...core import Switchboard

PORTAL = "https://discord.com/developers/applications"
THREAD_TYPES = (10, 11, 12)
DM_TYPES = (1, 3)
THREAD_PARENT_TYPES = (0, 5)
INTENTS = {"GUILDS": 1 << 0, "GUILD_MESSAGES": 1 << 9, "DIRECT_MESSAGES": 1 << 12, "MESSAGE_CONTENT": 1 << 15}
DEFAULT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
FATAL_STEPS = {
    4010: "Discord rejected the Gateway shard configuration. Discord requires a valid shard configuration.",
    4011: "Discord requires sharding for this bot. Use a bot that does not require sharding.",
    4012: "Discord no longer accepts this Gateway API version. Upgrade switchboard.",
    4013: "Discord rejected the Gateway intents. Upgrade switchboard.",
    4014: "Turn on Message Content Intent under Bot in the Discord Developer Portal, or set message_content: false and lose follow-ups without a mention.",
}
WEBHOOK_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
PERSONA_NAME_RE = re.compile(r"discord|clyde", re.IGNORECASE)


MENTION_RE = re.compile(r"<(@!?|@&|#)(\d+)>")

class Channel(NamedTuple):
    parent_id: str | None
    name: str | None
    type: int
    guild_id: str | None = None


class DiscordBlock(ChatBlock):
    platform = "discord"
    connect_kinds = ("discord",)
    manifest = Manifest(id="discord", title="Discord", kind="both", level_out="L3", level_in="events", verbs={"reply": "reply"},
                        tools=("post", "status", "provision"), secrets=(DEFAULT_TOKEN_ENV,), network=("discord.com", "gateway.discord.gg"))
    id_re = re.compile(r"[1-9][0-9]{0,19}\Z")
    token_re = re.compile(r"(?=[A-Za-z0-9_.-]{50,100}\Z)[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
    token_hint = "From the Discord Developer Portal: your application, Bot, Reset Token."

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        where = f"blocks.{config.get('_name') or self.platform}"
        raw_spaces = config.get("spaces", {})
        if not isinstance(raw_spaces, dict):
            raise BlockError(f"{where}.spaces must map space labels to settings")
        guild_spaces: dict[str, str] = {}
        auto = [label for label, raw in raw_spaces.items() if isinstance(raw, dict) and raw.get("guild_id") is None]
        if len(auto) > 1:
            raise BlockError(f"{where}.spaces: only one space can leave out guild_id ({', '.join(map(str, auto))} do)")
        for label, raw in raw_spaces.items():
            if not isinstance(label, str) or not isinstance(raw, dict):
                raise BlockError(f"{where}.spaces.{label}: expected a space label and settings mapping")
            if raw.get("guild_id") is None:
                continue
            guild = self._snowflake(raw.get("guild_id"), f"{where}.spaces.{label}.guild_id")
            if guild in guild_spaces:
                raise BlockError(f"{where}.spaces.{label}.guild_id is already configured in space {guild_spaces[guild]!r}")
            guild_spaces[guild] = label
        self.dm_space = config.get("dm_space", next(iter(raw_spaces), None))
        if "dm_space" in config and (not isinstance(self.dm_space, str) or self.dm_space not in raw_spaces):
            raise BlockError(f"{where}.dm_space must name a configured space")
        self.threads = config.get("threads", True)
        self.personas = config.get("personas", True)
        self.message_content = config.get("message_content", True)
        self.connect = config.get("connect", True)
        for key in ("threads", "personas", "message_content", "connect"):
            if not isinstance(getattr(self, key), bool):
                raise BlockError(f"{where}.{key} must be true or false")
        self.ack_reaction = config.get("ack_reaction", "👀")
        if self.ack_reaction is not None and (not isinstance(self.ack_reaction, str) or not self.ack_reaction.strip()):
            raise BlockError(f"{where}.ack_reaction must be an emoji or null")
        if "token_env" in config and (not isinstance(config["token_env"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config["token_env"])):
            raise BlockError(f"{where}.token_env must name an environment variable")
        self._application_id = self._snowflake(config["application_id"], f"{where}.application_id") if config.get("application_id") is not None else None
        super().__init__(core, config)
        self.guild_spaces = guild_spaces
        self.auto_space = auto[0] if auto else None
        self.application_id = self._application_id
        self.app_owner_ids: frozenset[str] = frozenset()
        self.guild_info: dict[str, dict[str, Any]] = {}
        self.dm_users: dict[str, str | None] = {}
        self.user_id: str | None = None
        self.bot_name: str | None = None
        self.guilds: set[str] = set()
        self.channels: dict[str, Channel] = {}
        self.webhooks: dict[str, tuple[str, str]] = {}
        self.webhook_ids: set[str] = set()
        self._webhook_denied: set[str] = set()
        self._post_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._transport = config.get("transport") or httpx.AsyncHTTPTransport()
        self._gateway = self._new_gateway()
        self._gateway_task: asyncio.Task[None] | None = None
        self._owner_task: asyncio.Future[None] | None = None
        self._running = False

    @classmethod
    def _snowflake(cls, value: Any, where: str) -> str:
        ident = str(value)
        if not cls.id_re.fullmatch(ident) or int(ident) >= 1 << 64:
            raise BlockError(f"{where} must be a Discord snowflake")
        return ident

    @property
    def token_env(self) -> str:
        return self.config.get("token_env", DEFAULT_TOKEN_ENV)

    @property
    def intents(self) -> int:
        return sum(bit for name, bit in INTENTS.items() if name != "MESSAGE_CONTENT" or self.message_content)

    @property
    def permissions(self) -> int:
        return ((1 << 10) | (1 << 11) | (1 << 16) | (1 << 26) | (1 << 38) | ((1 << 35) if self.threads else 0)
                | ((1 << 6) if self.ack_reaction else 0) | ((1 << 29) if self.personas else 0))

    def owners(self, space: Any) -> frozenset[str]:
        return space.owner_ids or self.app_owner_ids

    def owner_hint(self) -> str:
        return "Send the bot a direct message. Copy your ID from discord_status into owner_ids."

    def space_for(self, guild: str) -> str | None:
        """The space a guild belongs to: the one that names it, else the space without a guild_id when exactly one joined,
        unclaimed guild is owned by that space's owner (the application owner unless owner_ids says otherwise)."""
        if guild in self.guild_spaces:
            return self.guild_spaces[guild]
        if self.auto_space is None:
            return None
        owners = self.owners(self.spaces[self.auto_space])
        mine = [g for g, info in self.guild_info.items() if g not in self.guild_spaces and info.get("owner_id") in owners]
        return self.auto_space if mine == [guild] else None

    def discovered(self) -> dict[str, Any]:
        guilds = []
        for guild, info in self.guild_info.items():
            channels = sorted(({"id": c, "name": ch.name, "type": ch.type} for c, ch in self.channels.items()
                               if ch.guild_id == guild and ch.type not in THREAD_TYPES), key=lambda c: c["name"] or "")
            guilds.append({"id": guild, "name": info.get("name"), "owner_id": info.get("owner_id"), "space": self.space_for(guild), "channels": channels})
        return {"application_owner_ids": sorted(self.app_owner_ids), "guilds": guilds,
                "dm_users": [{"id": u, "name": n} for u, n in self.dm_users.items()]}

    def status(self) -> dict[str, Any]:
        return {**super().status(), "discovered": self.discovered(),
                **({"personas": "missing Manage Webhooks"} if self._webhook_denied else {})}

    async def _load_owner(self) -> None:
        token = self.token()
        try:
            app = await self.request("GET", "/oauth2/applications/@me")
        except Exception:
            return
        if self.token() != token:
            return
        team = app.get("team") or {}
        owner = team.get("owner_user_id") if team else (app.get("owner") or {}).get("id")
        self.app_owner_ids = frozenset({str(owner)}) if owner and self.id_re.fullmatch(str(owner)) else frozenset()
        self.application_id = self._application_id or app.get("id") or self.application_id

    def _invite(self) -> str | None:
        if not self.application_id:
            return None
        params = {"client_id": self.application_id, "scope": "bot", "permissions": str(self.permissions)}
        if len(self.guild_spaces) == 1 and self.auto_space is None:
            params.update(guild_id=next(iter(self.guild_spaces)), disable_guild_select="true")
        return "https://discord.com/oauth2/authorize?" + urlencode(params)

    def connected(self) -> bool:
        return self._gateway.ready

    def missing(self) -> list[str]:
        out = []
        tool = f"{self.name}_provision"
        if not self.token():
            out.append(f"No bot token: open the link from {tool} and save it, or set {self.token_env}.")
        code = self._gateway.fatal_code
        if code == 4004:
            step = f"Open a new token link from {tool} and save a new Discord bot token."
            if env_value(self.core.cfg.env, self.token_env):
                step = f"Replace {self.token_env} and restart SwB, or unset it and restart to use a claim link. {step}"
            out.append(f"Discord rejected the bot token. {step}")
        elif code in FATAL_STEPS:
            out.append(FATAL_STEPS[code])
        invite = self._invite()
        if invite is None:
            out.append(f"Set blocks.{self.name}.application_id to the Application ID, or let the bot connect once.")
        else:
            out.extend(f"Invite the bot to {label}: {invite}" for guild, label in self.guild_spaces.items() if guild not in self.guilds)
            if self.auto_space and self.connected() and not any(self.space_for(g) == self.auto_space for g in self.guild_info):
                out.append(f"Invite the bot to a server you own for space {self.auto_space}: {invite}")
            if self._webhook_denied:
                out.append(f"Re-open the invite URL to grant Manage Webhooks: {invite}")
        out.extend(f"Set blocks.{self.name}.spaces.{s.label}.owner_ids to the owner's Discord user ID." for s in self.spaces.values() if not self.owners(s))
        return out

    def get_token_steps(self) -> list[str]:
        return ["Create a New Application in the Discord Developer Portal.",
                "Under Bot, reset or generate the bot token."
                + (" Turn on Message Content Intent under Bot." if self.message_content else "")]

    def setup(self) -> dict[str, Any]:
        source = "env" if env_value(self.core.cfg.env, self.token_env) else "stored" if self.token() else None
        return {"developer_portal_url": PORTAL, "invite_url": self._invite(), "application_id": self.application_id,
                "bot_user_id": self.user_id, "bot_name": self.bot_name, "token_source": source,
                "claim_pending": self.claim_pending(), "intents": [name for name, bit in INTENTS.items() if self.intents & bit],
                "steps": [*self.get_token_steps(),
                          f"Save the token in the link from {self.name}_provision, or set {self.token_env}.",
                          "Open the invite URL and add the bot to each configured Discord server."]}

    def _new_gateway(self) -> Gateway:
        return Gateway(self, connect=self.config.get("gateway_connect"))

    async def _stop_gateway(self) -> None:
        if self._gateway_task is not None:
            self._gateway_task.cancel()
            await asyncio.gather(self._gateway_task, return_exceptions=True)
            self._gateway_task = None

    def _start_gateway(self) -> None:
        if self.connect and (token := self.token()):
            self._gateway_task = asyncio.create_task(self._gateway.run(token), name=f"{self.name}-gateway")

    @contextlib.asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        base = super().lifespan()
        assert base is not None
        async with base:
            self._running = True
            try:
                self._start_gateway()
                yield
            finally:
                self._running = False
                async with self._connect_lock:
                    await self._stop_gateway()

    async def aclose(self) -> None:
        self._running = False
        async with self._connect_lock:
            await self._stop_gateway()
        await self._transport.aclose()

    async def secret_saved(self, name: str) -> None:
        if name != "bot_token" or env_value(self.core.cfg.env, self.token_env):
            return
        async with self._connect_lock:
            await self._stop_gateway()
            if self._owner_task is not None:
                self._owner_task.cancel()
                await asyncio.gather(self._owner_task, return_exceptions=True)
                self._owner_task = None
            self._gateway = self._new_gateway()
            self.user_id = self.bot_name = None
            self.application_id = self._application_id
            self.guilds.clear()
            self.guild_info.clear()
            self.app_owner_ids = frozenset()
            self.channels.clear()
            self.webhooks.clear()
            self.webhook_ids.clear()
            self._webhook_denied.clear()
            if self._running:
                self._start_gateway()

    async def request(self, method: str, path: str, *, json: dict[str, Any] | None = None,
                      params: dict[str, str] | None = None, webhook: bool = False, retry: bool = True) -> Any:
        token = None if webhook else self.token()
        if not webhook and not token:
            raise BlockError(f"No Discord bot token: open the link from {self.name}_provision or set {self.token_env}.")
        return await rest.request(self._transport, method, path, token, json=json, params=params, retry=retry)

    def bot_identity_guild(self, space: str) -> str | None:
        return next((guild for guild, label in self.guild_spaces.items() if label == space), None) or next(
            (guild for guild in self.guild_info if self.space_for(guild) == space), None
        )

    def identity_public_url(self) -> str | None:
        base = self.core.cfg.public_url
        if base and (url := urlsplit(base)).scheme == "https" and url.hostname:
            return base.rstrip("/")
        return None

    async def update_bot_identity(self, name: str | None, icon: bytes | None, *,
                                  space: str | None = None, reset: bool = False, allow_webhook_fallback: bool = False) -> bool:
        guild = self.bot_identity_guild(space) if space is not None else None
        if space is not None and guild is None:
            raise BlockError("Connect a Discord server to this space.")
        payload: dict[str, str | None] = {}
        if guild and reset:
            payload = {"nick": None, "avatar": None}
        if name is not None:
            payload["nick" if guild else "username"] = name
        if icon is not None:
            with Image.open(BytesIO(icon)) as image:
                if image.format != "PNG":
                    out = BytesIO()
                    image.convert("RGBA").save(out, "PNG")
                    icon = out.getvalue()
            payload["avatar"] = "data:image/png;base64," + base64.b64encode(icon).decode("ascii")
        if not payload:
            return False
        path = f"/guilds/{guild}/members/@me" if guild else "/users/@me"
        try:
            result = await self.request("PATCH", path, json=payload, retry=False)
        except rest.DiscordHTTPError as exc:
            if not exc.guild_avatar_rejected or space is None:
                raise
            previous = self.core.bot_identities.get(self.name, space)
            if reset and previous and previous.webhook_avatar:
                await self.request("PATCH", path, json={"nick": None}, retry=False)
                return False
            if reset or not (allow_webhook_fallback and self.personas and self.identity_public_url()):
                raise
            nick = name or (previous.name if previous else None)
            if not nick:
                raise
            await self.request("PATCH", path, json={"nick": nick}, retry=False)
            return True
        if guild is None and name is not None:
            self.bot_name = (result.get("global_name") or result.get("username") or name) if isinstance(result, dict) else name
        return False

    def _cache_channel(self, data: dict[str, Any], guild_id: str | None = None) -> Channel:
        ident = data["id"]
        old = self.channels.get(ident)
        channel = Channel(data.get("parent_id"), data.get("name"), data["type"],
                          guild_id or data.get("guild_id") or (old.guild_id if old else None))
        self.channels[ident] = channel
        return channel

    async def _channel(self, channel: str) -> Channel:
        if channel in self.channels:
            return self.channels[channel]
        return self._cache_channel(await self.request("GET", f"/channels/{channel}"))

    async def dispatch(self, event: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update the Gateway's channel and bot state or ingest a normalized message."""
        if event == "READY":
            user = data["user"]
            self.user_id, self.bot_name = user["id"], user.get("global_name") or user.get("username")
            self.application_id = self._application_id or (data.get("application") or {}).get("id")
            self.guilds = {guild["id"] for guild in data.get("guilds", [])}
            self.guild_info = {}
            self.channels.clear()
            if self._owner_task is not None:
                self._owner_task.cancel()
            self._owner_task = asyncio.ensure_future(self._load_owner())
            self._owner_task.add_done_callback(lambda t: t.cancelled() or t.exception())
            identity = self.core.bot_identities.get(self.name)
            if not identity or not identity.icon:
                await self.apply_avatar(user["id"], lambda image: self.update_bot_identity(None, image))
        elif event == "GUILD_CREATE":
            self.guilds.add(data["id"])
            self.guild_info[data["id"]] = {"name": data.get("name"), "owner_id": data.get("owner_id")}
            for channel in (*data.get("channels", []), *data.get("threads", [])):
                self._cache_channel(channel, data["id"])
        elif event == "GUILD_UPDATE" and data["id"] in self.guild_info:
            self.guild_info[data["id"]] = {"name": data.get("name"), "owner_id": data.get("owner_id")}
        elif event == "GUILD_DELETE" and not data.get("unavailable"):
            self.guilds.discard(data["id"])
            self.guild_info.pop(data["id"], None)
        elif event in ("CHANNEL_CREATE", "CHANNEL_UPDATE", "THREAD_CREATE", "THREAD_UPDATE"):
            self._cache_channel(data)
        elif event in ("CHANNEL_DELETE", "THREAD_DELETE"):
            self.channels.pop(data["id"], None)
        elif event == "THREAD_LIST_SYNC":
            for thread in data.get("threads", []):
                self._cache_channel(thread, data.get("guild_id"))
        elif event == "MESSAGE_CREATE":
            return await self._message(data)
        return None

    async def _message(self, data: dict[str, Any]) -> dict[str, Any] | None:
        author, guild = data["author"], data.get("guild_id")
        if (author["id"] == self.user_id or data.get("webhook_id") in self.webhook_ids
                or (self.application_id and data.get("application_id") == self.application_id)):
            return None
        dm = guild is None
        if dm and len(self.dm_users) < 50:
            self.dm_users[author["id"]] = author.get("global_name") or author.get("username")
        space = self.dm_space if dm else self.space_for(guild)
        if space is None:
            return None
        channel, message = data["channel_id"], data["id"]
        if dm:
            place = self.channels.setdefault(channel, Channel(None, None, 1))
        else:
            place = await self._channel(channel)
        thread = channel if place.type in THREAD_TYPES else None
        if thread and not place.parent_id:
            return None
        parent = place.parent_id if thread else channel
        name = (await self._channel(parent)).name if thread else place.name
        text = data.get("content") or ""
        mention = False
        if self.user_id:
            bot = rf"<@!?{re.escape(self.user_id)}>"
            mention = any(user.get("id") == self.user_id for user in data.get("mentions", [])) or bool(re.search(bot, text))
            text = re.sub(rf"^\s*{bot}\s*", "", text, count=1)
        ref = data.get("message_reference") or {}
        reply_to = ref.get("message_id") if ref.get("type", 0) == 0 and ref.get("channel_id", channel) == channel else None
        return await self.ingest(Incoming(space=space, channel=parent, message=message, user=author["id"], text=text,
                                          user_name=(data.get("member") or {}).get("nick") or author.get("global_name") or author.get("username"),
                                          channel_name=name, is_bot=bool(author.get("bot") or data.get("webhook_id")), mention=mention, dm=dm,
                                          thread=thread, reply_to=reply_to, link=f"https://discord.com/channels/{guild or '@me'}/{channel}/{message}"))

    async def _thread(self, channel: str, root: str, create: bool) -> str | None:
        async with self._post_lock:
            try:
                found = await self._channel(root)
            except rest.DiscordHTTPError as e:
                if e.status != 404:
                    raise
            else:
                if found.type not in THREAD_TYPES or found.parent_id != channel:
                    raise BlockError("The Discord thread does not belong to this channel")
                return root
            if not create:
                return None
            name, author = "swb", None
            with contextlib.suppress(BlockError):
                original = await self.request("GET", f"/channels/{channel}/messages/{root}")
                author = (original.get("author") or {}).get("id")
                names = {u["id"]: u.get("global_name") or u.get("username") for u in original.get("mentions") or [] if u.get("id") != self.user_id}
                def label(m: re.Match[str]) -> str:
                    kind, ident = m.groups()
                    if kind == "#":
                        known = self.channels.get(ident)
                        return f"#{known.name}" if known and known.name else ""
                    return f"@{names[ident]}" if kind != "@&" and ident in names else ""

                text = MENTION_RE.sub(label, original.get("content") or "")
                first = " ".join(next((line for line in text.splitlines() if line.strip()), "").split())
                who = (original.get("author") or {}).get("global_name") or (original.get("author") or {}).get("username")
                name = first[:100] or (f"Reply to {who}"[:100] if who else name)
            try:
                created = await self.request("POST", f"/channels/{channel}/messages/{root}/threads", json={"name": name})
            except rest.DiscordHTTPError:
                try:
                    found = await self._channel(root)
                except rest.DiscordHTTPError:
                    found = None
                if found is None or found.type not in THREAD_TYPES or found.parent_id != channel:
                    raise
                return root
            self._cache_channel(created)
            if author and author != self.user_id:
                with contextlib.suppress(BlockError):
                    await self.request("PUT", f"/channels/{created['id']}/thread-members/{author}", retry=False)
            return created["id"]

    async def _webhook(self, channel: str) -> tuple[str, str]:
        async with self._post_lock:
            if channel not in self.webhooks:
                def ours(w: dict[str, Any]) -> bool:
                    return bool(WEBHOOK_TOKEN_RE.fullmatch(w.get("token") or "") and
                                ((self.user_id and (w.get("user") or {}).get("id") == self.user_id)
                                 or (self.application_id and w.get("application_id") == self.application_id)))

                path = f"/channels/{channel}/webhooks"
                found = next((w for w in await self.request("GET", path) if ours(w)), None)
                webhook = found or await self.request("POST", path, json={"name": "swb"})
                secret = webhook.get("token") or ""
                if not WEBHOOK_TOKEN_RE.fullmatch(secret):
                    raise BlockError("Discord did not return a usable webhook token")
                self.webhooks[channel] = (webhook["id"], secret)
                self.webhook_ids.add(webhook["id"])
            return self.webhooks[channel]

    async def post(self, space: str, channel: str, text: str, *, root: str | None = None, reply_to: str | None = None,
                   persona: Persona | None = None) -> str | list[str]:
        self._snowflake(channel, "Discord channel")
        if root is not None:
            self._snowflake(root, "Discord thread")
        place = await self._channel(channel)
        dm = place.type in DM_TYPES
        thread = channel if place.type in THREAD_TYPES else None
        parent = place.parent_id if thread else channel
        if parent is None:
            raise BlockError("Discord did not return the thread's parent channel")
        guild_id = place.guild_id or ((await self._channel(parent)).guild_id if thread else None)
        if guild_id and self.space_for(guild_id) != space:
            raise BlockError("The Discord channel does not belong to this space")
        if guild_id and (identity := self.core.bot_identities.get(self.name, space)):
            if identity.post_every_agent:
                persona = None
            if persona is None and identity.webhook_avatar and self.core.find_avatar(identity.icon) and (base := self.identity_public_url()):
                persona = Persona(id=self.name, name=identity.name, icon_url=f"{base}/avatars/{identity.icon}")
        if root and not dm and thread is None:
            thread = await self._thread(channel, root, self.threads and place.type in THREAD_PARENT_TYPES)
        reference = (reply_to or (root if thread is None else None)) if dm or not self.threads or thread is None else None
        data: dict[str, Any] = {"allowed_mentions": {"parse": []}}
        bot_path = f"/channels/{thread or channel}/messages"
        webhook_path = None
        persona_data: dict[str, str] = {}
        if self.personas and persona is not None and not dm and reference is None:
            try:
                webhook_id, secret = await self._webhook(parent)
                webhook_path = f"/webhooks/{webhook_id}/{secret}"
            except rest.DiscordHTTPError as exc:
                if exc.status != 403:
                    raise
                self._webhook_denied.add(parent)
            persona_data["username"] = PERSONA_NAME_RE.sub("swb", persona.name).strip()[:80].rstrip() or "swb"
            avatar = persona.avatar or (f"{persona.id}.png" if not persona.icon_url else None)
            avatar_url = (self.core.avatar_url(avatar) if avatar else None) or persona.icon_url
            if avatar_url:
                with contextlib.suppress(ValueError):
                    url = urlsplit(avatar_url)
                    if url.scheme == "https" and url.hostname:
                        persona_data["avatar_url"] = avatar_url
        chunks = []
        start, length = 0, 0
        for end, char in enumerate(text):
            size = 1 + (ord(char) > 0xFFFF)
            if length + size > 2000:
                chunks.append(text[start:end])
                start, length = end, 0
            length += size
        chunks.append(text[start:])
        if reference:
            data["message_reference"] = {"message_id": reference}
        ids = []
        params = {"wait": "true", **({"thread_id": thread} if thread else {})}
        for chunk in chunks:
            body = {**data, "content": chunk}
            if webhook_path:
                try:
                    sent = await self.request("POST", webhook_path, json={**body, **persona_data}, params=params, webhook=True)
                except rest.DiscordHTTPError as exc:
                    if exc.status != 403:
                        raise
                    self._webhook_denied.add(parent)
                    self.webhooks.pop(parent, None)
                    webhook_path = None
                else:
                    self._webhook_denied.discard(parent)
            if not webhook_path:
                sent = await self.request("POST", bot_path, json=body)
            ids.append(sent["id"])
            data.pop("message_reference", None)
        return ids[0] if len(ids) == 1 else ids

    async def open_dm(self, space: str, user: str) -> str | None:
        self._snowflake(user, "Discord user")
        try:
            data = await self.request("POST", "/users/@me/channels", json={"recipient_id": user})
        except BlockError:
            return None
        channel = data.get("id")
        if channel:
            self.channels[channel] = Channel(None, None, 1)
        return channel

    async def ack(self, msg: Incoming) -> None:
        if self.ack_reaction:
            with contextlib.suppress(BlockError):
                emoji = quote(self.ack_reaction, safe="")
                await self.request("PUT", f"/channels/{msg.thread or msg.channel}/messages/{msg.message}/reactions/{emoji}/@me")
