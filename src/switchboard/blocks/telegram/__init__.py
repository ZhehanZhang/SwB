"""Telegram messages in via long polling, and Bot API replies out. Telegram topics stay transport metadata."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator

from ...chat import RETENTION_S, ChatBlock, Incoming, Persona
from .. import BlockError, Manifest
from .api import BotAPI, TelegramError

if TYPE_CHECKING:
    from ...core import Switchboard

log = logging.getLogger("switchboard.blocks.telegram")
COMMAND = re.compile(r"/([a-z0-9_]+)(?:@([a-z0-9_]+))?(?=\s|$)", re.I)
USERNAME = re.compile(r"[A-Za-z0-9_]{1,32}")
OFFSET_TTL_S = 6 * 86400
MAX_TEXT = 4096
SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_offsets (block TEXT NOT NULL, bot INTEGER NOT NULL, next_id INTEGER NOT NULL,
  at REAL NOT NULL, PRIMARY KEY (block, bot));
CREATE TABLE IF NOT EXISTS telegram_topics (block TEXT NOT NULL, chat TEXT NOT NULL, message TEXT NOT NULL,
  topic INTEGER NOT NULL, at REAL NOT NULL, PRIMARY KEY (block, chat, message));
"""


def _address(text: str, entities: list[dict[str, Any]], bot_id: int, username: str) -> tuple[bool, str]:
    stripped = text.lstrip()
    command = COMMAND.match(stripped)
    if command:
        name, bot = command.groups()
        if (bot or "").lower() == username.lower() or (bot is None and name.lower() == "swb"):
            return True, stripped[command.end():].lstrip()
    if not entities:
        return False, text
    encoded = text.encode("utf-16-le")
    leading = len(encoded) - len(stripped.encode("utf-16-le"))
    mention = False
    for entity in entities:
        kind = entity["type"]
        if kind not in ("mention", "text_mention"):
            continue
        start = 2 * entity["offset"]
        end = start + 2 * entity["length"]
        if kind == "mention":
            ours = encoded[start:end].decode("utf-16-le", errors="ignore").lower() == f"@{username.lower()}"
        else:
            ours = (entity.get("user") or {}).get("id") == bot_id
        if ours:
            mention = True
            if start == leading:
                return True, encoded[end:].decode("utf-16-le", errors="ignore").lstrip()
    return mention, text


def _chunks(text: str, prefix: str | None) -> Iterator[str]:
    if prefix is None:
        for i in range(0, len(text), MAX_TEXT):
            yield text[i:i + MAX_TEXT]
        return
    chunk, size = [prefix], len(prefix)
    for char in text:
        escaped = html.escape(char)
        if size + len(escaped) > MAX_TEXT:
            yield "".join(chunk)
            chunk, size = [], 0
        chunk.append(escaped)
        size += len(escaped)
    yield "".join(chunk)


class TelegramBlock(ChatBlock):
    platform = "telegram"
    connect_kinds = ("telegram",)
    manifest = Manifest(id="telegram", title="Telegram", kind="both", level_out="L3", level_in="events",
                        verbs={"reply": "reply"}, tools=("post", "status", "provision"),
                        secrets=("TELEGRAM_BOT_TOKEN",), network=("api.telegram.org",))
    token_re = re.compile(r"[0-9]{5,16}:[A-Za-z0-9_-]{30,64}")
    token_hint = "From @BotFather: /newbot (or /token for an existing bot), then copy the HTTP API token."

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        where = f"blocks.{self.name}"
        if "token_env" in config and (not isinstance(config["token_env"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config["token_env"])):
            raise BlockError(f"{where}.token_env: expected an environment variable name")
        self.ack_reaction = config.get("ack_reaction", "👀")
        if self.ack_reaction is not None and (not isinstance(self.ack_reaction, str) or not self.ack_reaction.strip()):
            raise BlockError(f"{where}.ack_reaction: expected an emoji or null")
        self.persona_prefix = config.get("persona_prefix", True)
        if not isinstance(self.persona_prefix, bool):
            raise BlockError(f"{where}.persona_prefix: expected true or false")
        self.dm_space = config.get("dm_space", next(iter(self.spaces), None))
        if "dm_space" in config and (not isinstance(self.dm_space, str) or self.dm_space not in self.spaces):
            raise BlockError(f"{where}.dm_space: expected a configured space label")
        self._groups: dict[int, str] = {}
        for label, raw in (self.config.get("spaces") or {}).items():
            key = f"{where}.spaces.{label}.chats"
            ids = raw.get("chats", [])
            if not isinstance(ids, list) or any(type(chat) is not int or chat >= 0 for chat in ids):
                raise BlockError(f"{key}: expected a list of negative integer Telegram group chat ids")
            for chat in ids:
                if chat in self._groups:
                    raise BlockError(f"{key}: chat {chat} is already listed in {self._groups[chat]}")
                self._groups[chat] = label
        core.store.db.executescript(SCHEMA)
        self._api = BotAPI(config.get("transport"))
        self._bot: dict[str, Any] | None = None
        self._privacy: bool | None = None
        self._online = self._rejected = self._running = False
        self._unseen: set[int] = set()
        self._seen: dict[int, dict[str, Any]] = {}
        self._connection: asyncio.Task[None] | None = None
        self._connection_lock = asyncio.Lock()

    @property
    def token_env(self) -> str:
        return self.config.get("token_env", "TELEGRAM_BOT_TOKEN")

    def connected(self) -> bool:
        return self._online

    def missing(self) -> list[str]:
        steps = []
        if not self.token():
            steps.append(f"no bot token: open the link from {self.name}_provision, or set {self.token_env}")
        if self._rejected:
            steps.append("token rejected: open a new token link")
        if self._privacy and any(s.listen for s in self.spaces.values()):
            steps.append("in @BotFather: /setprivacy, choose the bot, Disable, then remove and re-add the bot to the group")
        steps.extend(f"add the bot to {label} chat {chat}" for chat, label in self._groups.items() if chat in self._unseen)
        steps.extend(f"set blocks.{self.name}.spaces.{s.label}.owner_ids (DM the bot and look up your id in {self.name}_status)"
                     for s in self.spaces.values() if not s.owner_ids)
        return steps

    def setup(self) -> dict[str, Any]:
        url = f"https://t.me/{self._bot['username']}" if self._bot else None
        return {"bot_father_url": "https://t.me/BotFather", "bot_url": url,
                "add_to_group_url": f"{url}?startgroup=swb" if url else None, "bot": dict(self._bot) if self._bot else None,
                "privacy_mode": self._privacy, "token_source": "env" if self.core.cfg.env.get(self.token_env) else
                ("stored" if self.token() else None), "claim_pending": self.claims.pending("bot_token"),
                "seen_chats": list(reversed(self._seen.values())),
                "steps": ["1. Create a bot in @BotFather with /newbot.",
                          f"2. Open the token link from {self.name}_provision and paste the token.",
                          f"3. Add the bot to the group, send /swb@<bot username>, then copy the id from {self.name}_status into chats.",
                          f"4. DM the bot, find your id in {self.name}_status, and add it to owner_ids."]}

    @contextlib.asynccontextmanager
    async def _telegram_lifespan(self) -> AsyncIterator[None]:
        try:
            async with super().lifespan():
                self._running = bool(self.config.get("connect", True))
                await self.secret_saved("bot_token")
                try:
                    yield
                finally:
                    self._running = False
                    await self.secret_saved("bot_token")
        finally:
            await self._api.aclose()

    def lifespan(self) -> contextlib.AbstractAsyncContextManager[None]:
        return self._telegram_lifespan()

    async def secret_saved(self, name: str) -> None:
        if name != "bot_token":
            return
        async with self._connection_lock:
            if self._connection is not None:
                self._connection.cancel()
                await asyncio.gather(self._connection, return_exceptions=True)
                self._connection = None
            self._bot, self._privacy = None, None
            self._online = self._rejected = False
            self._unseen.clear()
            if self._running and (token := self.token()):
                self._connection = asyncio.create_task(self._connect(token), name=f"{self.name}-poll")

    async def _connect(self, token: str) -> None:
        if not self.token_re.fullmatch(token):
            self._rejected = True
            return
        delay, next_check = 1, 0.0
        clock = asyncio.get_running_loop().time
        while True:
            try:
                if self._bot is None:
                    try:
                        me = await self._api.call(token, "getMe")
                    except TelegramError as exc:
                        if exc.code in (401, 404):
                            self._rejected = True
                            return
                        raise
                    if not isinstance(me, dict) or type(me.get("id")) is not int:
                        raise TelegramError("getMe", "invalid bot identity")
                    username = me.get("username")
                    if not isinstance(username, str) or not USERNAME.fullmatch(username):
                        raise TelegramError("getMe", "invalid bot username")
                    self._bot = {"id": me["id"], "username": username, "name": me.get("first_name")}
                    self._privacy = not me.get("can_read_all_group_messages", False)
                    next_check = 0
                if clock() >= next_check:
                    for chat in self._groups:
                        try:
                            await self._api.call(token, "getChat", chat_id=chat)
                        except TelegramError:
                            self._unseen.add(chat)
                        else:
                            self._unseen.discard(chat)
                    self.core.store.x("DELETE FROM telegram_topics WHERE block = ? AND at < ?", (self.name, self.core.clock() - RETENTION_S))
                    next_check = clock() + 60
                if self.refreshed_at is None:
                    await self.refresh()
                self._online = True
                count = await self._poll(token, self._bot["id"], self._bot["username"])
                delay = 1
                if not count:
                    await asyncio.sleep(0.1)
            except TelegramError as exc:
                self._online = False
                if exc.code in (401, 404):
                    self._bot, self._privacy = None, None
                log.warning("%s connection: %s", self.name, exc)
                await asyncio.sleep(max(delay, exc.retry_after or 0))
                delay = min(2 * delay, 60)
            except Exception:
                self._online = False
                log.warning("%s connection: update processing failed", self.name)
                await asyncio.sleep(delay)
                delay = min(2 * delay, 60)

    async def _poll(self, token: str, bot_id: int, username: str) -> int:
        row = self.core.store.one("SELECT next_id FROM telegram_offsets WHERE block = ? AND bot = ? AND at > ?",
                                  (self.name, bot_id, self.core.clock() - OFFSET_TTL_S))
        offset = row["next_id"] if row else 0
        updates = await self._api.call(token, "getUpdates", retry=False, offset=offset, timeout=50, allowed_updates=["message"])
        if not isinstance(updates, list):
            raise TelegramError("getUpdates", "invalid update list")
        batch = sorted((u for u in updates if isinstance(u, dict) and type(u.get("update_id")) is int), key=lambda u: u["update_id"])
        for update in batch:
            if update["update_id"] < offset:
                continue
            if isinstance(update.get("message"), dict):
                await self._receive(update["message"], bot_id, username)
            offset = update["update_id"] + 1
            self.core.store.x("INSERT OR REPLACE INTO telegram_offsets(block, bot, next_id, at) VALUES (?,?,?,?)",
                              (self.name, bot_id, offset, self.core.clock()))
        return len(batch)

    async def _receive(self, raw: dict[str, Any], bot_id: int, username: str) -> None:
        sender, chat = raw.get("from") or {}, raw.get("chat") or {}
        user, channel, mid = sender.get("id"), chat.get("id"), raw.get("message_id")
        if user == bot_id or type(channel) is not int or type(mid) is not int:
            return
        kind = chat.get("type")
        dm = kind == "private"
        space = self.dm_space if dm else self._groups.get(channel) if kind in ("group", "supergroup") else None
        if dm or space is None:
            self._seen.pop(channel, None)
            self._seen[channel] = {"id": channel, "title": chat.get("title"), "type": kind}
            if len(self._seen) > 20:
                self._seen.pop(next(iter(self._seen)))
        text = raw.get("text") or raw.get("caption")
        if space is None or type(user) is not int or not text:
            return
        mention, text = _address(text, raw.get("entities") or raw.get("caption_entities") or [], bot_id, username)
        topic = raw.get("message_thread_id")
        if raw.get("is_topic_message") and type(topic) is int and topic > 1:
            self._save_topic(str(channel), str(mid), topic)
        reply_to = (raw.get("reply_to_message") or {}).get("message_id")
        name = " ".join(n for n in (sender.get("first_name"), sender.get("last_name")) if n) or sender.get("username")
        internal = str(channel).removeprefix("-100") if kind == "supergroup" else ""
        link = f"https://t.me/c/{internal}/{mid}" if internal.isdecimal() else None
        await self.ingest(Incoming(space=space, channel=str(channel), message=str(mid), user=str(user), text=text, user_name=name,
                                   channel_name=chat.get("title"), is_bot=bool(sender.get("is_bot")), mention=mention, dm=dm,
                                   reply_to=str(reply_to) if type(reply_to) is int else None, link=link))

    def _save_topic(self, channel: str, message: str, topic: int) -> None:
        self.core.store.x("INSERT OR REPLACE INTO telegram_topics(block, chat, message, topic, at) VALUES (?,?,?,?,?)",
                          (self.name, channel, message, topic, self.core.clock()))

    async def post(self, space: str, channel: str, text: str, *, root: str | None = None, reply_to: str | None = None,
                   persona: Persona | None = None) -> str | list[str]:
        token = self.token()
        if not token or not self.token_re.fullmatch(token):
            raise BlockError("Telegram needs a valid bot token")
        ref = reply_to or root
        try:
            chat_id, reply_id = int(channel), int(ref) if ref else None
        except ValueError:
            raise BlockError("Telegram chat and message ids must be integers") from None
        params: dict[str, Any] = {"chat_id": chat_id, "link_preview_options": {"is_disabled": True}}
        topic = None
        if reply_id is not None:
            params["reply_parameters"] = {"message_id": reply_id, "allow_sending_without_reply": True}
            found = self.core.store.one("SELECT topic FROM telegram_topics WHERE block = ? AND chat = ? AND message IN (?,?) ORDER BY message = ? DESC",
                                        (self.name, str(chat_id), str(reply_id), root, str(reply_id)))
            if found:
                topic = found["topic"]
                params["message_thread_id"] = topic
        prefix = None
        if self.persona_prefix and persona is not None:
            prefix = f"<b>{html.escape(persona.name)}</b>\n"
            if len(prefix) >= MAX_TEXT:
                raise BlockError("Telegram persona name is too long")
            params["parse_mode"] = "HTML"
        ids = []
        for chunk in _chunks(text, prefix):
            if not chunk.strip():
                continue
            result = await self._api.call(token, "sendMessage", text=chunk, **params)
            if not isinstance(result, dict) or type(result.get("message_id")) is not int:
                raise TelegramError("sendMessage", "invalid message id")
            mid = str(result["message_id"])
            if topic:
                self._save_topic(channel, mid, topic)
            ids.append(mid)
        if not ids:
            raise BlockError("Telegram message text is empty")
        return ids[0] if len(ids) == 1 else ids

    async def open_dm(self, space: str, user: str) -> str | None:
        return user if re.fullmatch(r"[1-9][0-9]{0,23}", user) else None

    async def ack(self, msg: Incoming) -> None:
        if self.ack_reaction and (token := self.token()) and self.token_re.fullmatch(token):
            with contextlib.suppress(TelegramError):
                await self._api.call(token, "setMessageReaction", retry=False, chat_id=int(msg.channel), message_id=int(msg.message),
                                     reaction=[{"type": "emoji", "emoji": self.ack_reaction}])
