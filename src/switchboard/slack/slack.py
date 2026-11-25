from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Callable

from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_async_handlers import AsyncRateLimitErrorRetryHandler
from slack_sdk.web.async_client import AsyncWebClient

from .config import WorkspaceConfig
from .events import ClassifyContext, permalink
from .personas import persona_post_args

if TYPE_CHECKING:
    from .engine import SlackEngine as Core

log = logging.getLogger("switchboard.slack")

# switchboard never adds anyone (itself included) to a conversation, in any workspace.
FORBIDDEN_METHODS = frozenset(
    {
        "conversations.join",
        "conversations.invite",
        "conversations.inviteShared",
        "channels.join",
        "channels.invite",
        "groups.invite",
        "admin.conversations.invite",
    }
)
CHANNEL_ID_RE = re.compile(r"^[CGD][A-Z0-9]{6,}$")


class ForbiddenSlackMethod(RuntimeError):
    pass


class GuardedClient(AsyncWebClient):
    async def api_call(self, api_method: str, **kwargs: Any) -> Any:  # type: ignore[override]
        if api_method in FORBIDDEN_METHODS:
            raise ForbiddenSlackMethod(f"switchboard never calls {api_method}")
        return await super().api_call(api_method, **kwargs)


def make_client(token: str) -> GuardedClient:
    client = GuardedClient(token=token)
    client.retry_handlers.append(AsyncRateLimitErrorRetryHandler(max_retry_count=3))
    return client


def slack_error(exc: Exception) -> str:
    if isinstance(exc, SlackApiError):
        return str(exc.response.get("error") or "slack_error")
    return type(exc).__name__


class WorkspaceRuntime:
    def __init__(self, core: "Core", cfg: WorkspaceConfig, client_factory: Callable[[str], Any]):
        self.core = core
        self.cfg = cfg
        self.clients: dict[str, Any] = {}
        self.missing: list[str] = []
        for name, ident in cfg.identities.items():
            token = core.env.get(ident.token_env)
            if token:
                self.clients[name] = client_factory(token)
            else:
                self.missing.append(ident.token_env)
        self.app_token = core.env.get(cfg.app_token_env) if cfg.app_token_env else None
        if cfg.intake == "socket" and not self.app_token and cfg.app_token_env:
            self.missing.append(cfg.app_token_env)
        self.team_url: str | None = None
        self.bot_user_id: str | None = None
        self.bot_id: str | None = None
        self.user_id: str | None = None
        self.errors: list[str] = []
        self.connected = False
        self.socket: Any = None
        self.tasks: list[asyncio.Task] = []
        self.inflight: set[asyncio.Task] = set()
        self.channel_names: dict[str, str] = {}
        self.channel_ids: dict[str, str] = {}
        self.user_names: dict[str, str] = {}
        self.watched_ids: frozenset[str] = frozenset()
        self.unresolved_watched: list[str] = []
        self.last_event_at: float | None = None

    @property
    def label(self) -> str:
        return self.cfg.label

    @property
    def team_id(self) -> str:
        return self.cfg.team_id

    def client(self, identity: str) -> Any:
        client = self.clients.get(identity)
        if client is None:
            raise LookupError(f"workspace {self.label!r} has no usable {identity} token")
        return client

    def reader(self, identity: str | None = None) -> Any:
        if identity:
            return self.client(identity)
        for name in ("bot", "user"):
            if name in self.clients:
                return self.clients[name]
        raise LookupError(f"workspace {self.label!r} has no usable token")

    def classify_context(self) -> ClassifyContext:
        return ClassifyContext(
            has_bot="bot" in self.clients,
            bot_user_id=self.bot_user_id,
            bot_id=self.bot_id,
            app_id=self.cfg.app_id,
            watched=self.watched_ids,
            watch_all=self.cfg.watch_all,
            skip_user_ids=self.cfg.skip_user_ids,
        )

    async def start(self, connect: bool = True) -> None:
        for name, client in list(self.clients.items()):
            try:
                info = await client.auth_test()
            except Exception as exc:
                self.errors.append(f"{name} token: auth.test failed ({slack_error(exc)})")
                self.clients.pop(name)
                continue
            if info.get("team_id") != self.team_id:
                self.errors.append(f"{name} token belongs to team {info.get('team_id')}, expected {self.team_id}")
                self.clients.pop(name)
                continue
            if name == "user" and info.get("bot_id"):
                self.errors.append("user identity was given a bot token; refusing it")
                self.clients.pop(name)
                continue
            self.team_url = info.get("url") or self.team_url
            if name == "bot":
                self.bot_user_id = info.get("user_id")
                self.bot_id = info.get("bot_id")
            else:
                self.user_id = info.get("user_id")
        if self.clients:
            await self.resolve_watched()
        if connect and self.clients:
            if self.cfg.intake == "socket" and self.app_token:
                self.tasks.append(asyncio.create_task(self._run_socket(), name=f"socket:{self.label}"))
            elif self.cfg.intake == "poll":
                self.tasks.append(asyncio.create_task(self._poll_loop(), name=f"poll:{self.label}"))

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks.clear()
        if self.socket is not None:
            try:
                await self.socket.close()
            except Exception:
                pass
            self.socket = None
        self.connected = False

    async def _list_channels(self) -> None:
        cursor = None
        reader = self.reader()
        while True:
            resp = await reader.conversations_list(
                types="public_channel,private_channel", exclude_archived=True, limit=500, cursor=cursor
            )
            for ch in resp.get("channels", []):
                self.channel_names[ch["id"]] = ch.get("name") or ch["id"]
                if ch.get("name"):
                    self.channel_ids[ch["name"]] = ch["id"]
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return

    async def resolve_watched(self) -> None:
        ids, unresolved = set(), []
        names = [w.lstrip("#") for w in self.cfg.watched if not CHANNEL_ID_RE.match(w)]
        if names:
            try:
                await self._list_channels()
            except Exception as exc:
                self.errors.append(f"conversations.list failed ({slack_error(exc)})")
        if self.cfg.watch_all and "bot" in self.clients:
            try:
                cursor = None
                while True:
                    resp = await self.clients["bot"].users_conversations(types="public_channel,private_channel", exclude_archived=True, limit=500, cursor=cursor)
                    for ch in resp.get("channels", []):
                        ids.add(ch["id"])
                        self.channel_names[ch["id"]] = ch.get("name") or ch["id"]
                    cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                    if not cursor:
                        break
            except Exception as exc:
                self.errors.append(f"users.conversations failed ({slack_error(exc)})")
        for w in self.cfg.watched:
            if CHANNEL_ID_RE.match(w):
                ids.add(w)
            elif w.lstrip("#") in self.channel_ids:
                ids.add(self.channel_ids[w.lstrip("#")])
            else:
                unresolved.append(w)
        self.watched_ids = frozenset(ids)
        self.unresolved_watched = unresolved

    async def resolve_channel(self, ref: str) -> str:
        if CHANNEL_ID_RE.match(ref):
            return ref
        name = ref.lstrip("#")
        if name not in self.channel_ids:
            await self._list_channels()
        if name not in self.channel_ids:
            raise LookupError(f"no channel named {ref!r} visible in {self.label}")
        return self.channel_ids[name]

    async def channel_name(self, channel: str) -> str | None:
        if channel in self.channel_names:
            return self.channel_names[channel]
        try:
            resp = await self.reader().conversations_info(channel=channel)
            ch = resp.get("channel") or {}
            name = ch.get("name") or (f"dm:{ch.get('user')}" if ch.get("is_im") else None)
        except Exception:
            name = None
        if name:
            self.channel_names[channel] = name
        return name

    async def user_name(self, user: str | None) -> str | None:
        if not user or not user.startswith(("U", "W")):
            return None
        if user in self.user_names:
            return self.user_names[user]
        try:
            resp = await self.reader().users_info(user=user)
            u = resp.get("user") or {}
            prof = u.get("profile") or {}
            name = prof.get("display_name") or prof.get("real_name") or u.get("name")
        except Exception:
            name = None
        if name:
            self.user_names[user] = name
        return name

    async def _run_socket(self) -> None:
        from slack_sdk.socket_mode.aiohttp import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse

        async def on_request(client: Any, req: Any) -> None:
            await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            self.last_event_at = time.time()
            task = asyncio.create_task(self.core.handle_socket(self.label, req.type, req.payload))
            self.inflight.add(task)
            task.add_done_callback(self.inflight.discard)

        self.socket = SocketModeClient(app_token=self.app_token, web_client=AsyncWebClient())
        self.socket.socket_mode_request_listeners.append(on_request)
        try:
            await self.socket.connect()
            self.connected = True
            log.info("workspace %s: socket mode connected", self.label)
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.errors.append(f"socket mode failed ({slack_error(exc)})")
            log.error("workspace %s: socket mode failed: %s", self.label, slack_error(exc))
            self.connected = False

    async def _poll_loop(self) -> None:
        self.connected = True
        while True:
            for channel in sorted(self.watched_ids):
                try:
                    await self.poll_channel(channel)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("workspace %s: poll %s failed: %s", self.label, channel, slack_error(exc))
                await asyncio.sleep(1)
            await asyncio.sleep(self.cfg.poll_seconds)

    async def poll_channel(self, channel: str) -> int:
        store = self.core.store
        cursor = store.get_cursor(self.label, channel)
        if cursor is None:
            store.set_cursor(self.label, channel, f"{self.core.clock():.6f}")
            return 0
        reader = self.reader("user" if "user" in self.clients else None)
        resp = await reader.conversations_history(channel=channel, oldest=cursor, inclusive=False, limit=100)
        messages = sorted(resp.get("messages", []), key=lambda m: float(m["ts"]))
        channel_type = {"D": "im", "G": "group"}.get(channel[0], "channel")
        for msg in messages:
            await self.core.ingest(self.label, {**msg, "type": "message", "channel": channel, "channel_type": channel_type})
        if messages:
            store.set_cursor(self.label, channel, messages[-1]["ts"])
            self.last_event_at = time.time()
        return len(messages)

    async def execute(self, identity: str, action: str, params: dict[str, Any], persona: dict[str, Any] | None) -> dict[str, Any]:
        client = self.client(identity)
        if action == "post_message":
            args: dict[str, Any] = {"channel": params["channel"], "text": params["text"]}
            if params.get("thread_ts"):
                args["thread_ts"] = params["thread_ts"]
                if params.get("reply_broadcast"):
                    args["reply_broadcast"] = True
            if identity == "bot":
                args.update(persona_post_args(persona, self.core.avatar_url))
            resp = await client.chat_postMessage(**args)
            channel, ts = resp["channel"], resp["ts"]
            self.core.store.mark_sent(self.team_id, channel, ts)
            return {"channel": channel, "ts": ts, "permalink": permalink(self.team_url, channel, ts, params.get("thread_ts"))}
        if action == "add_reaction":
            await client.reactions_add(channel=params["channel"], timestamp=params["ts"], name=params["name"])
            return {"channel": params["channel"], "ts": params["ts"], "reaction": params["name"]}
        raise ValueError(f"unknown action {action!r}")

    def status(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "user_only": self.cfg.user_only,
            "identities": sorted(self.clients),
            "missing_env": self.missing,
            "intake": self.cfg.intake,
            "connected": self.connected,
            "watched": len(self.watched_ids),
            "unresolved_watched": self.unresolved_watched,
            "errors": self.errors[-5:],
            "last_event_at": self.last_event_at,
        }
