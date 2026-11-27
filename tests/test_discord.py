"""Discord dispatch, the Gateway session, REST posts and owner provisioning, all without a network."""

import asyncio
import base64
import json
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import aiohttp
import httpx
import pytest
import yaml
from PIL import Image
from starlette.testclient import TestClient

from conftest import OWNER, PUBLIC, STATIC_KEY, Clock, access_verifier, call, payload
from switchboard.app import create_app
from switchboard.blocks import BlockError
from switchboard.blocks.discord import gateway
from switchboard.chat import Persona
from switchboard.core import Switchboard

APP, GUILD, GUILD_2, BOT, USER = (str(100000000000000000 + n) for n in range(5))
CHANNEL, ROOT, DM, HOOK = "40", "100", "50", "75"
MOCK_TOKEN = ".".join(("A" * 24, "b" * 6, "C" * 35))
MOCK_WEBHOOK = "mock_" + "x" * 60
DEFAULT_INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)


class DiscordAPI:
    def __init__(self):
        self.calls, self.posts, self.reactions, self.routines = [], [], [], []
        self.webhooks = []
        self.channels = {CHANNEL: {"id": CHANNEL, "name": "builds", "type": 0, "guild_id": GUILD}, DM: {"id": DM, "type": 1}}
        self.responses = deque()
        self.application = {"id": APP, "owner": {"id": USER}}
        self.avatar_status = 200

    def handler(self, request):
        method, path = request.method, request.url.path.removeprefix("/api/v10")
        data = json.loads(request.content) if request.content else None
        self.calls.append((method, path, data))
        assert ("Authorization" in request.headers) == (not path.startswith("/webhooks/"))
        assert request.headers["User-Agent"].startswith("DiscordBot (")
        if path == "/gateway/bot":
            return httpx.Response(200, json={"url": "wss://gateway.discord.gg"})
        if path == "/oauth2/applications/@me":
            return httpx.Response(200, json=self.application)
        if path == "/users/@me" and method == "PATCH":
            return httpx.Response(self.avatar_status, json={"retry_after": 0} if self.avatar_status == 429 else {"id": BOT})
        if path == "/users/@me/channels":
            return httpx.Response(200, json=self.channels[DM])
        if "/reactions/" in path:
            self.reactions.append(request.url.raw_path)
            return httpx.Response(204)
        if path.endswith("/threads"):
            thread = self.channels[ROOT] = {"id": ROOT, "type": 11, "parent_id": CHANNEL, "name": data["name"]}
            return httpx.Response(201, json=thread)
        if path == f"/channels/{CHANNEL}/messages/{ROOT}":
            return httpx.Response(200, json={"content": f"<@!{BOT}> build-check check the build\nSecond line"})
        if path == f"/channels/{CHANNEL}/webhooks":
            if method == "GET":
                return httpx.Response(200, json=self.webhooks)
            webhook = {"id": HOOK, "application_id": APP, "token": MOCK_WEBHOOK}
            self.webhooks.append(webhook)
            return httpx.Response(200, json=webhook)
        if "/messages" in path or path.startswith("/webhooks/"):
            if self.responses:
                return self.responses.popleft()
            ident = str(700 + len(self.posts))
            self.posts.append((path, data, dict(request.url.params)))
            return httpx.Response(200, json={"id": ident})
        channel = self.channels.get(path.removeprefix("/channels/"))
        return httpx.Response(200, json=channel) if channel else httpx.Response(404)

    def routine(self, request):
        self.routines.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"claude_code_session_id": "session_1", "claude_code_session_url": "https://agents.example.test/session/1"})


class Socket:
    def __init__(self, interval=60_000):
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.closed = False
        self.feed(10, {"heartbeat_interval": interval})

    def feed(self, op, data=None, **kw):
        self.incoming.put_nowait(aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps({"op": op, "d": data, **kw}), ""))

    def disconnect(self, code):
        self.incoming.put_nowait(aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, code, ""))

    async def receive(self):
        return await self.incoming.get()

    async def send_json(self, data):
        await self.outgoing.put(data)

    async def close(self, **kw):
        self.closed = True


class Sockets:
    def __init__(self, *sockets):
        self.sockets, self.urls = deque(sockets), []

    @asynccontextmanager
    async def connect(self, url):
        self.urls.append(url)
        yield self.sockets.popleft()


@pytest.fixture
def discord(tmp_path):
    @contextmanager
    def build(*, settings=None, sockets=None, token=True, slack=None):
        api = DiscordAPI()
        cfg = {"version": 2, "public_url": PUBLIC,
               "auth": {"owner_emails": ["owner@example.test"], "cloudflare_access": {"team_domain": "access.example.test", "aud": "test"}},
               "blocks": {"mcp": {"use": "mcp"},
                          "claude-routine": {"use": "claude-routine", "routines": {"build-check": {"routine_id": "trig_EXAMPLE", "token_env": "EXAMPLE_ROUTINE_TOKEN"}}},
                          "discord": {"use": "discord", "agents": {"claude": ["home"]}, "spaces": {"home": {"guild_id": GUILD, "owner_ids": [USER]}},
                                      **(settings or {})}}}
        if slack is not None:
            cfg["blocks"]["slack"] = {"use": "slack", **slack}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(cfg, sort_keys=False))
        env = {"SWITCHBOARD_KEY_CLAUDE": STATIC_KEY, "EXAMPLE_ROUTINE_TOKEN": "mock-routine"}
        if token:
            env["DISCORD_BOT_TOKEN"] = MOCK_TOKEN
        core = Switchboard(path, tmp_path / "data", env=env, clock=Clock(),
                           oauth_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404))),
                           access_verifier=access_verifier,
                           destination_options={"discord": {"transport": httpx.MockTransport(api.handler), "connect": sockets is not None,
                                                           "gateway_connect": sockets.connect if sockets else None},
                                                "claude-routine": {"transport": httpx.MockTransport(api.routine)}})
        with TestClient(create_app(core), base_url=PUBLIC) as client:
            block = core.blocks["discord"]
            client.portal.call(block.refresh)
            yield core, client, block, api
    return build


def ready(**kw):
    return {"user": {"id": BOT, "username": "swb"}, "application": {"id": APP}, "guilds": [{"id": GUILD}],
            "session_id": "session_example", "resume_gateway_url": "wss://gateway-us-example.discord.gg", **kw}


def message(**kw):
    return {"guild_id": GUILD, "channel_id": CHANNEL, "id": ROOT, "author": {"id": USER, "username": "Owner"},
            "member": {"nick": "Tester"}, "content": f"<@!{BOT}> build-check check the build", "mentions": [], **kw}


@pytest.mark.parametrize(("settings", "permissions"), [({}, "309237713984"), ({"personas": True}, "309774584896"),
                                                       ({"threads": False, "ack_reaction": None}, "274877975552")])
def test_invite_uses_only_configured_bot_permissions(discord, settings, permissions):
    with discord(settings={"application_id": APP, **settings}) as (_, client, block, _):
        setup = payload(call(client, STATIC_KEY, "discord_status"))["setup"]
        invite = urlsplit(setup["invite_url"])
        assert (invite.scheme, invite.netloc, invite.path) == ("https", "discord.com", "/oauth2/authorize")
        assert parse_qs(invite.query) == {"client_id": [APP], "scope": ["bot"], "permissions": [permissions], "guild_id": [GUILD],
                                         "disable_guild_select": ["true"]}
        assert setup["token_source"] == "env"


def test_dispatch_routes_owner_and_the_native_thread_keeps_the_target(discord):
    with discord() as (core, client, block, api):
        dispatch = lambda event, data: client.portal.call(block.dispatch, event, data)
        dispatch("READY", ready())
        dispatch("GUILD_CREATE", {"id": GUILD, "channels": [api.channels[CHANNEL]], "threads": []})
        event = dispatch("MESSAGE_CREATE", message())
        assert event["trust"] == "owner" and event["chat_kind"] == "mention" and event["target"] == "build-check"
        assert event["text"] == "check the build" and event["from"]["user_name"] == "Tester"
        assert event["from"]["link"] == f"https://discord.com/channels/{GUILD}/{CHANNEL}/{ROOT}"
        assert len(api.routines) == 1 and "check the build" in api.routines[0]
        assert api.reactions == [f"/api/v10/channels/{CHANNEL}/messages/{ROOT}/reactions/%F0%9F%91%80/@me".encode()]
        handle = core.store.one("SELECT handle FROM chat_relays")["handle"]
        client.portal.call(block.relay, {"kind": "final", "handle": handle, "text": "all green @everyone"})
        assert ("POST", f"/channels/{CHANNEL}/messages/{ROOT}/threads", {"name": message()["content"]}) in api.calls
        assert api.posts == [(f"/channels/{ROOT}/messages", {"allowed_mentions": {"parse": []}, "content": "all green @everyone"}, {})]
        follow = dispatch("MESSAGE_CREATE", message(channel_id=ROOT, id="101", content="and staging?"))
        assert follow["target"] == "build-check" and follow["chat_kind"] == "reply"
        assert follow["from"]["root"] == ROOT and follow["from"]["channel"] == CHANNEL and follow["from"]["channel_name"] == "builds"
        assert follow["from"]["link"] == f"https://discord.com/channels/{GUILD}/{ROOT}/101"
        assert len(api.routines) == 2 and "and staging?" in api.routines[-1]


def test_dispatch_filters_own_and_unknown_guild_messages_and_sends_dms_to_configured_space(discord):
    spaces = {"home": {"guild_id": GUILD, "owner_ids": [USER]},
              "direct": {"guild_id": GUILD_2, "owner_ids": [USER], "default_target": "build-check"}}
    with discord(settings={"dm_space": "direct", "spaces": spaces, "personas": True}) as (_, client, block, api):
        dispatch = lambda event, data: client.portal.call(block.dispatch, event, data)
        dispatch("READY", ready())
        client.portal.call(lambda: block.post("home", CHANNEL, "me", persona=Persona("helper", "Helper")))
        for dropped in (message(author={"id": BOT}), message(webhook_id=HOOK), message(application_id=APP), message(guild_id="99")):
            assert dispatch("MESSAGE_CREATE", dropped) is None
        assert not api.routines
        event = dispatch("MESSAGE_CREATE", message(guild_id=None, channel_id=DM, content="hello"))
        assert event["from"]["space"] == "direct" and event["chat_kind"] == "dm" and event["trust"] == "owner"
        assert event["from"]["link"] == f"https://discord.com/channels/@me/{DM}/{ROOT}" and len(api.routines) == 1
        assert "guild_id" not in parse_qs(urlsplit(block.setup()["invite_url"]).query)


def test_channel_cache_refreshes_from_gateway_and_loads_a_missing_parent(discord):
    with discord() as (_, client, block, api):
        dispatch = lambda event, data: client.portal.call(block.dispatch, event, data)
        dispatch("READY", ready())
        dispatch("THREAD_LIST_SYNC", {"threads": [{"id": ROOT, "type": 11, "parent_id": CHANNEL, "name": "thread"}]})
        dispatch("THREAD_UPDATE", {"id": ROOT, "type": 11, "parent_id": CHANNEL, "name": "updated thread"})
        event = dispatch("MESSAGE_CREATE", message(channel_id=ROOT, content="build-check hi", mentions=[{"id": BOT}]))
        assert event["from"]["channel_name"] == "builds" and event["from"]["root"] == ROOT
        assert ("GET", f"/channels/{CHANNEL}", None) in api.calls and ("GET", f"/channels/{ROOT}", None) not in api.calls
        dispatch("CHANNEL_UPDATE", {**api.channels[CHANNEL], "name": "new name"})
        event = dispatch("MESSAGE_CREATE", message(channel_id=ROOT, id="101", content="follow-up"))
        assert event["from"]["channel_name"] == "new name"


@pytest.mark.parametrize("existing", [False, True])
def test_persona_webhooks_are_reused_per_parent_and_do_not_leak_secrets(discord, caplog, existing):
    with discord(settings={"personas": True}) as (_, client, block, api):
        client.portal.call(block.dispatch, "READY", ready())
        api.webhooks = [{"id": "76", "application_id": "99", "token": "different"}]
        if existing:
            api.webhooks.append({"id": HOOK, "user": {"id": BOT}, "token": MOCK_WEBHOOK})
        caplog.set_level("INFO", logger="httpx")
        client.portal.call(lambda: block.post("home", CHANNEL, "hi", root=ROOT,
                                              persona=Persona("helper", "DiScOrD CLYDE " + "x" * 90, "https://assets.example.test/icon.png")))
        client.portal.call(lambda: block.post("home", CHANNEL, "again", root=ROOT, persona=Persona("h", "H", "http://assets.example.test/icon.png")))
        path, data, params = api.posts[0]
        assert path.startswith(f"/webhooks/{HOOK}/") and params == {"wait": "true", "thread_id": ROOT}
        assert len(data["username"]) == 80 and all(word not in data["username"].lower() for word in ("discord", "clyde"))
        assert data["avatar_url"] == "https://assets.example.test/icon.png" and data["allowed_mentions"] == {"parse": []}
        assert "avatar_url" not in api.posts[1][1]
        assert len([c for c in api.calls if c[1] == f"/channels/{CHANNEL}/webhooks"]) == (1 if existing else 2)
        assert MOCK_WEBHOOK not in caplog.text and MOCK_TOKEN not in caplog.text


def test_in_place_references_dm_open_and_message_split(discord):
    with discord(settings={"threads": False, "personas": True, "ack_reaction": None}) as (_, client, block, api):
        text = "a" * 2000 + "👀" * 1000 + "z"
        ids = client.portal.call(lambda: block.post("home", CHANNEL, text, root=ROOT, reply_to="102", persona=Persona("h", "Helper")))
        assert ids == ["700", "701", "702"] and [p[1]["content"] for p in api.posts] == ["a" * 2000, "👀" * 1000, "z"]
        assert all(p[0] == f"/channels/{CHANNEL}/messages" and p[1]["allowed_mentions"] == {"parse": []} for p in api.posts)
        assert api.posts[0][1]["message_reference"] == {"message_id": "102"} and "message_reference" not in api.posts[1][1]
        assert client.portal.call(block.open_dm, "home", USER) == DM
        assert ("POST", "/users/@me/channels", {"recipient_id": USER}) in api.calls
        client.portal.call(lambda: block.post("home", DM, "private", root=ROOT, reply_to=ROOT, persona=Persona("h", "Helper")))
        assert api.posts[-1] == (f"/channels/{DM}/messages", {"content": "private", "allowed_mentions": {"parse": []},
                                                              "message_reference": {"message_id": ROOT}}, {})


def test_native_reply_to_second_chunk_routes_to_the_original_target_without_a_discord_thread(discord):
    with discord(settings={"threads": False}) as (core, client, block, api):
        client.portal.call(block.dispatch, "READY", ready())
        client.portal.call(block.dispatch, "MESSAGE_CREATE", message())
        handle = core.store.one("SELECT handle FROM chat_relays")["handle"]
        client.portal.call(block.relay, {"kind": "final", "handle": handle, "text": "a" * 2000 + "second chunk"})
        assert [p[0] for p in api.posts] == [f"/channels/{CHANNEL}/messages"] * 2
        assert api.posts[1][1]["content"] == "second chunk"
        reference = {"type": 0, "channel_id": CHANNEL, "message_id": "701"}
        follow = client.portal.call(block.dispatch, "MESSAGE_CREATE", message(id="101", content="and staging?", message_reference=reference))
        assert follow["chat_kind"] == "reply" and follow["target"] == "build-check" and follow["from"]["root"] == ROOT
        assert len(api.routines) == 2 and "and staging?" in api.routines[-1]


def test_discord_channel_must_belong_to_the_requested_space(discord):
    with discord() as (_, client, block, api):
        api.channels["99"] = {"id": "99", "type": 0, "guild_id": GUILD_2}
        with pytest.raises(BlockError, match="does not belong to this space"):
            client.portal.call(block.post, "home", "99", "wrong server")
        assert not api.posts


def test_rest_retries_json_and_header_rate_limits_with_a_bound(discord):
    with discord() as (_, client, block, api):
        api.responses.extend([httpx.Response(429, json={"retry_after": 0}), httpx.Response(429, headers={"Retry-After": "0"})])
        assert client.portal.call(block.post, "home", CHANNEL, "hello") == "700"
        assert len([c for c in api.calls if c[0] == "POST"]) == 3
        api.responses.extend(httpx.Response(429, json={"retry_after": 0}) for _ in range(5))
        with pytest.raises(BlockError, match="HTTP 429"):
            client.portal.call(block.post, "home", CHANNEL, "hello again")
        assert len(api.responses) == 1


async def finish_ready(socket, block):
    async with asyncio.timeout(3):
        assert (await socket.outgoing.get())["op"] == 2
        socket.feed(0, ready(), t="READY", s=1)
        socket.feed(0, {"id": CHANNEL, "type": 0}, t="CHANNEL_CREATE", s=2)
        while CHANNEL not in block.channels:
            await asyncio.sleep(0)


def test_gateway_applies_avatar_only_when_the_resolved_file_changes(discord, tmp_path):
    uploaded = tmp_path / "data" / "avatars"
    configured = tmp_path / "branding"
    uploaded.mkdir(parents=True)
    configured.mkdir()
    name = "switchboard.png"
    Image.new("RGBA", (8, 8), "red").save(uploaded / name)
    Image.new("RGB", (8, 8), "blue").save(configured / name)
    for image in (uploaded / name, None, configured / name):
        socket = Socket()
        with discord(settings={"avatar": name}, sockets=Sockets(socket), slack={"avatar_dirs": [str(configured)]}) as (_, client, block, api):
            client.portal.call(finish_ready, socket, block)
            status = payload(call(client, STATIC_KEY, "discord_status"))
            assert status["connected"] and status["avatar"] == {"file": name, "applied": True, "error": None}
            changes = [data["avatar"] for method, path, data in api.calls if (method, path) == ("PATCH", "/users/@me")]
            assert len(changes) == (image is not None)
            if image is not None:
                assert changes == ["data:image/png;base64," + base64.b64encode(image.read_bytes()).decode("ascii")]
        if image is None:
            (uploaded / name).unlink()


@pytest.mark.parametrize(("file", "patches", "error"), [
    (None, 0, None),
    ("missing-avatar.png", 0, "avatar file not found"),
    ("switchboard.png", 1, "Discord REST returned HTTP 429"),
])
def test_gateway_avatar_failure_does_not_break_connection_or_retry(discord, caplog, file, patches, error):
    socket = Socket()
    with discord(settings={"avatar": file} if file else {}, sockets=Sockets(socket)) as (_, client, block, api):
        api.avatar_status = 429
        client.portal.call(finish_ready, socket, block)
        status = payload(call(client, STATIC_KEY, "discord_status"))
        assert status["connected"] and status["avatar"] == {"file": file, "applied": False, "error": error}
        assert sum((method, path) == ("PATCH", "/users/@me") for method, path, _ in api.calls) == patches
        if error:
            assert caplog.text.count(error) == 1


def test_gateway_identifies_heartbeats_resumes_and_reports_fatal_intents(discord):
    first, second = Socket(), Socket()
    sockets = Sockets(first, second)
    with discord(sockets=sockets) as (_, client, block, _):
        async def drive():
            async with asyncio.timeout(3):
                identify = await first.outgoing.get()
                assert identify["op"] == 2 and identify["d"]["intents"] == DEFAULT_INTENTS
                first.feed(0, ready(), t="READY", s=42)
                first.feed(1)
                assert await first.outgoing.get() == {"op": 1, "d": 42}
                first.feed(11)
                first.feed(7)
                resume = await second.outgoing.get()
                assert resume["op"] == 6 and resume["d"]["session_id"] == "session_example" and resume["d"]["seq"] == 42
                assert sockets.urls == ["wss://gateway.discord.gg/?v=10&encoding=json", "wss://gateway-us-example.discord.gg/?v=10&encoding=json"]
                second.feed(0, {}, t="RESUMED", s=43)
                while not block.connected():
                    await asyncio.sleep(0)
                assert block.missing() == [] and block.setup()["bot_user_id"] == BOT
                second.disconnect(4014)
                await block._gateway_task
                assert not block.connected() and "Turn on Message Content Intent under Bot" in block.missing()[0]
                assert first.closed and second.closed
        client.portal.call(drive)


def test_gateway_reconnects_on_missed_ack_and_honors_invalid_session(discord, monkeypatch):
    bounds = []

    def uniform(lo, hi):
        bounds.append((lo, hi))
        return 0

    monkeypatch.setattr(gateway, "random", SimpleNamespace(random=lambda: 0, uniform=uniform))
    first, second, third, fourth = Socket(interval=100), Socket(), Socket(), Socket()
    with discord(sockets=Sockets(first, second, third, fourth)) as (_, client, _, _):
        async def drive():
            async with asyncio.timeout(3):
                assert (await first.outgoing.get())["op"] == 2
                assert await first.outgoing.get() == {"op": 1, "d": None}
                first.feed(0, ready(), t="READY", s=42)
                assert (await second.outgoing.get())["op"] == 6
                second.feed(9, True)
                assert (await third.outgoing.get())["op"] == 6
                third.feed(9, False)
                assert (await fourth.outgoing.get())["op"] == 2
                assert bounds.count((1, 5)) == 2
        client.portal.call(drive)


def test_gateway_skips_optional_intent_and_reconnects_after_claim_rotation(discord):
    first, second = Socket(), Socket()
    sockets = Sockets(first, second)
    with discord(settings={"message_content": False}, sockets=sockets, token=False) as (core, client, block, _):
        def status():
            return payload(call(client, STATIC_KEY, "discord_status"))

        def save(token):
            provision = payload(call(client, STATIC_KEY, "discord_provision"))
            assert status()["setup"]["claim_pending"]
            link = urlsplit(provision["link"]).path
            assert link.rsplit("/", 1)[0] == "/claims/discord"
            page = client.get(link, headers=OWNER)
            assert page.status_code == 200, page.text
            csrf = page.text.split('name="csrf" value="')[1].split('"')[0]
            assert client.post(link, headers=OWNER, data={"csrf": csrf, "token": token}).status_code == 200

        async def identify(socket, value):
            async with asyncio.timeout(3):
                event = await socket.outgoing.get()
                assert event["op"] == 2 and event["d"]["intents"] == DEFAULT_INTENTS & ~(1 << 15)
                assert event["d"]["token"] == value

        before = status()
        assert not before["connected"] and before["setup"]["token_source"] is None
        assert "discord_provision" in before["missing"][0] and "DISCORD_BOT_TOKEN" in before["missing"][0]
        assert "application_id" in before["missing"][1]
        save(MOCK_TOKEN)
        client.portal.call(identify, first, MOCK_TOKEN)
        assert status()["setup"]["token_source"] == "stored" and not status()["setup"]["claim_pending"]
        assert MOCK_TOKEN not in core.store.one("SELECT sealed FROM block_secrets")["sealed"]
        rotated = MOCK_TOKEN[::-1]
        save(rotated)
        client.portal.call(identify, second, rotated)
        assert first.closed
    assert second.closed


@pytest.mark.parametrize(("settings", "key"), [({"spaces": {"home": {"guild_id": "bad"}}}, "spaces.home.guild_id"),
                                               ({"dm_space": "missing"}, "dm_space"), ({"application_id": -1}, "application_id"),
                                               ({"personas": "true"}, "personas"), ({"avatar": "../switchboard.png"}, "avatar")])
def test_invalid_discord_config_names_the_path(discord, settings, key):
    with pytest.raises(BlockError, match=rf"blocks\.discord\.{key}"):
        with discord(settings=settings):
            pytest.fail("invalid Discord configuration was accepted")


def test_a_space_without_guild_id_binds_the_owners_server_and_trusts_the_application_owner(discord):
    spaces = {"spaces": {"home": {"project": None}}, "agents": {"claude": ["home"]}}
    with discord(settings=spaces) as (core, client, block, api):
        dispatch = lambda event, data: client.portal.call(block.dispatch, event, data)
        dispatch("READY", ready(guilds=[{"id": GUILD}, {"id": GUILD_2}]))
        client.portal.call(block._load_owner)
        assert block.app_owner_ids == {USER}
        dispatch("GUILD_CREATE", {"id": GUILD, "name": "Home", "owner_id": USER, "channels": [api.channels[CHANNEL]], "threads": []})
        dispatch("GUILD_CREATE", {"id": GUILD_2, "name": "Elsewhere", "owner_id": "999", "channels": [], "threads": []})
        event = dispatch("MESSAGE_CREATE", message())
        assert event["trust"] == "owner" and event["from"]["space"] == "home"
        assert dispatch("MESSAGE_CREATE", message(guild_id=GUILD_2, channel_id="41", id="102")) is None
        status = payload(call(client, STATIC_KEY, "discord_status"))
        assert status["spaces"]["home"]["owners"] == 1
        assert status["discovered"]["application_owner_ids"] == [USER]
        assert [(g["id"], g["name"], g["space"]) for g in status["discovered"]["guilds"]] == [(GUILD, "Home", "home"), (GUILD_2, "Elsewhere", None)]
        assert status["discovered"]["guilds"][0]["channels"] == [{"id": CHANNEL, "name": "builds", "type": 0}]
        assert not any("owner_ids" in m for m in status["missing"])


def test_the_auto_space_binds_nothing_when_two_owned_servers_are_joined(discord):
    with discord(settings={"spaces": {"home": {}}, "agents": {}}) as (_, client, block, _api):
        dispatch = lambda event, data: client.portal.call(block.dispatch, event, data)
        dispatch("READY", ready(guilds=[{"id": GUILD}, {"id": GUILD_2}]))
        client.portal.call(block._load_owner)
        for guild in (GUILD, GUILD_2):
            dispatch("GUILD_CREATE", {"id": guild, "name": guild, "owner_id": USER, "channels": [], "threads": []})
        assert block.space_for(GUILD) is None and block.space_for(GUILD_2) is None
        assert dispatch("MESSAGE_CREATE", message()) is None


def test_configured_owner_ids_win_and_a_team_owner_is_read(discord):
    with discord(settings={"spaces": {"home": {"guild_id": GUILD, "owner_ids": ["555"]}}, "agents": {}}) as (_, client, block, api):
        api.application = {"id": APP, "owner": {"id": "1"}, "team": {"owner_user_id": USER}}
        client.portal.call(block.dispatch, "READY", ready())
        client.portal.call(block._load_owner)
        assert block.app_owner_ids == {USER}
        assert block.owners(block.spaces["home"]) == {"555"}
        assert block.trust_of(block.spaces["home"], USER) == "external"


def test_dms_are_listed_as_candidates_but_never_trusted(discord):
    with discord(settings={"spaces": {"home": {"guild_id": GUILD, "owner_ids": [USER]}}}) as (_, client, block, api):
        api.application = {"id": APP, "owner": {}}
        client.portal.call(block.dispatch, "READY", ready())
        client.portal.call(block._load_owner)
        client.portal.call(block.dispatch, "MESSAGE_CREATE", message(guild_id=None, channel_id=DM, id="103", author={"id": "777", "username": "someone"}))
        discovered = payload(call(client, STATIC_KEY, "discord_status"))["discovered"]
        assert discovered["dm_users"] == [{"id": "777", "name": "someone"}] and discovered["application_owner_ids"] == []
        assert block.trust_of(block.spaces["home"], "777") == "external"


def test_only_one_space_may_leave_out_guild_id(discord):
    with pytest.raises(BlockError, match="only one space"):
        with discord(settings={"spaces": {"a": {}, "b": {}}, "agents": {}}):
            pass


def test_connect_discord_hands_out_the_token_link_then_the_invite(discord):
    with discord(token=False, settings={"spaces": {"home": {}}, "agents": {}}) as (_, client, block, _api):
        out = payload(call(client, STATIC_KEY, "connect", {"what": "discord"}))
        assert out["what"] == "discord" and "/claims/discord/" in out["next"][0]["link"]
    with discord(settings={"spaces": {"home": {}}, "agents": {}}) as (_, client, block, _api):
        client.portal.call(block.dispatch, "READY", ready(guilds=[]))
        client.portal.call(block._load_owner)
        block._gateway.ready = True
        steps = [s["do"] for s in payload(call(client, STATIC_KEY, "connect", {"what": "discord"}))["next"]]
        assert any(s.startswith("Invite the bot to a server you own for space home: https://discord.com/oauth2/authorize?") for s in steps)


def test_guild_ownership_transfer_unbinds_the_auto_space(discord):
    with discord(settings={"spaces": {"home": {}}, "agents": {}}) as (_, client, block, _api):
        dispatch = lambda event, data: client.portal.call(block.dispatch, event, data)
        dispatch("READY", ready(guilds=[{"id": GUILD}]))
        client.portal.call(block._load_owner)
        dispatch("GUILD_CREATE", {"id": GUILD, "name": "Home", "owner_id": USER, "channels": [], "threads": []})
        assert block.space_for(GUILD) == "home"
        dispatch("GUILD_UPDATE", {"id": GUILD, "name": "Home", "owner_id": "999"})
        assert block.space_for(GUILD) is None


def test_an_owner_lookup_for_a_replaced_token_is_discarded(discord, monkeypatch):
    with discord(settings={"spaces": {"home": {}}, "agents": {}}) as (_, client, block, _api):
        tokens = iter(["old", "old", "new"])
        monkeypatch.setattr(block, "token", lambda: next(tokens))
        client.portal.call(block._load_owner)
        assert block.app_owner_ids == frozenset()


def test_connect_discord_keeps_a_rejected_token_step(discord):
    with discord(settings={"spaces": {"home": {"guild_id": GUILD, "owner_ids": [USER]}}, "agents": {}}) as (_, client, block, _api):
        block._gateway.fatal_code = 4004
        steps = [s["do"] for s in payload(call(client, STATIC_KEY, "connect", {"what": "discord"}))["next"]]
        assert any(s.startswith("Discord rejected the bot token.") for s in steps)
