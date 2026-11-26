"""Telegram's Bot API transport, with real chat-core routing and no network."""

import asyncio
import html
import json
import logging
import time
import traceback
from collections import defaultdict
from contextlib import contextmanager

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from conftest import OWNER, PUBLIC, STATIC_KEY, Clock, access_verifier, call, payload
from switchboard.app import create_app
from switchboard.blocks import BlockError
from switchboard.chat import Persona
from switchboard.core import Switchboard
from test_project_routines import Anthropic, TOKEN as ROUTINE_TOKEN

BOT, OWNER_ID, STRANGER = 111111, 222222, 333333
TOKEN = f"{BOT}:" + "fake_" * 8
REJECTED = f"{BOT}:" + "invalid_" * 5
GROUP, UNKNOWN = -100111222, -100333444
BOT_NAME, ROUTINE_NAME = "ExampleSwbBot", 'Ops & <Build> "Desk"'


async def until(predicate):
    async with asyncio.timeout(3):
        while not (result := predicate()):
            await asyncio.sleep(0.002)
    return result


class FakeBotAPI:
    def __init__(self):
        self.calls = defaultdict(list)
        self.updates = asyncio.Queue()
        self.send_errors = []
        self.send_times = []
        self.rejected = {}
        self.privacy = False
        self.hidden = set()
        self.bot_id = BOT

    async def handler(self, request):
        token, method = request.url.path.removeprefix("/bot").split("/")
        args = json.loads(request.content)
        self.calls[method].append(args)
        if method == "getMe":
            if token in self.rejected:
                return httpx.Response(self.rejected[token], json={"ok": False, "description": f"Invalid URL {request.url}"})
            result = {"id": self.bot_id, "username": BOT_NAME, "first_name": "Example bot", "can_read_all_group_messages": not self.privacy}
        elif method == "getChat":
            if args["chat_id"] in self.hidden:
                return httpx.Response(400, json={"ok": False, "description": "chat not found"})
            result = {"id": args["chat_id"], "type": "supergroup"}
        elif method == "getUpdates":
            assert request.extensions["timeout"]["read"] > args["timeout"]
            assert args["timeout"] == 50 and args["allowed_updates"] == ["message"]
            updates = await self.updates.get()
            if isinstance(updates, httpx.Response):
                return updates
            result = [u for u in updates if u["update_id"] >= args["offset"]]
        elif method == "sendMessage":
            self.send_times.append(time.monotonic())
            if self.send_errors:
                code, delay = self.send_errors.pop(0)
                if code is None:
                    raise httpx.ConnectError(f"Cannot reach {request.url}", request=request)
                return httpx.Response(code, json={"ok": False, "error_code": code, "description": f"Failed request {request.url}",
                                                   "parameters": {"retry_after": delay}})
            result = {"message_id": self.last_message_id}
        else:
            assert method == "setMessageReaction"
            result = True
        return httpx.Response(200, json={"ok": True, "result": result})

    @property
    def last_message_id(self):
        return 9000 + len(self.calls["sendMessage"])

    async def deliver(self, *updates):
        before = await until(lambda: len(self.calls["getUpdates"]))
        self.updates.put_nowait(list(updates))
        await until(lambda: len(self.calls["getUpdates"]) > before)


def update(update_id, *, chat=GROUP, kind="supergroup", user=OWNER_ID, text="hello", mention=False, message=None, topic=None, reply=None, **kw):
    raw = {"message_id": message or update_id, "chat": {"id": chat, "title": "Example group", "type": kind},
           "from": {"id": user, "first_name": "Example", "last_name": "User", "is_bot": user == BOT}, "text": text, **kw}
    if mention:
        raw["text"] = f"@{BOT_NAME.lower()} {text}"
        raw["entities"] = [{"type": "mention", "offset": 0, "length": len(BOT_NAME) + 1}]
    if topic is not None:
        raw.update(is_topic_message=True, message_thread_id=topic)
    if reply is not None:
        raw["reply_to_message"] = {"message_id": reply}
    return {"update_id": update_id, "message": raw}


@pytest.fixture
def run(tmp_path):
    path = tmp_path / "switchboard.yaml"

    @contextmanager
    def start(*, bot=None, token=TOKEN, connect=True, **config):
        bot = FakeBotAPI() if bot is None else bot
        anthropic = Anthropic()
        telegram = {"use": "telegram", "dm_space": "dm", "agents": {"claude": "*"}, "spaces": {
            "group": {"owner_ids": [OWNER_ID], "chats": [GROUP]},
            "dm": {"owner_ids": [OWNER_ID], "default_target": "claude"}}}
        cfg = {"version": 2, "public_url": PUBLIC,
               "auth": {"owner_emails": ["owner@example.test"], "cloudflare_access": {"team_domain": "access.example.test", "aud": "aud1"}},
               "blocks": {
                   "mcp": {"use": "mcp"}, "telegram": {**telegram, **config},
                   "claude-routine": {"use": "claude-routine", "routines": {
                       "example-runner": {"routine_id": "trig_000EXAMPLE", "token_env": "ROUTINE_TOKEN", "name": ROUTINE_NAME}}}},
               "visibility": {"resources": {f"channel:group/{GROUP}": "shared"}}}
        path.write_text(yaml.safe_dump(cfg, sort_keys=False))
        env = {"ROUTINE_TOKEN": ROUTINE_TOKEN, "SWITCHBOARD_KEY_CLAUDE": STATIC_KEY}
        if token:
            env[config.get("token_env", "TELEGRAM_BOT_TOKEN")] = token
        core = Switchboard(path, tmp_path / "data", env=env, clock=Clock(), access_verifier=access_verifier,
                           destination_options={"telegram": {"transport": httpx.MockTransport(bot.handler), "connect": connect},
                                                "claude-routine": {"transport": httpx.MockTransport(anthropic.handler)}})
        with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
            yield core, client, core.blocks["telegram"], bot, anthropic

    return start


def test_owner_mention_and_sticky_reply_go_through_chat_core(run):
    with run() as (core, c, block, bot, anthropic):
        c.portal.call(bot.deliver, update(11, mention=True, text="example-runner can you check?", topic=42))
        assert "can you check?" in anthropic.fired[0][1] and f"@{BOT_NAME.lower()}" not in anthropic.fired[0][1]
        assert "https://t.me/c/111222/11" in anthropic.fired[0][1]
        assert bot.calls["setMessageReaction"] == [{"chat_id": GROUP, "message_id": 11, "reaction": [{"type": "emoji", "emoji": "👀"}]}]
        handle = core.store.one("SELECT handle FROM chat_relays")["handle"]
        answer = 'Result: <ok> & "ready"'
        result = payload(call(c, STATIC_KEY, "reply", {"conversation": handle, "text": answer}))
        assert result["delivered"]
        c.portal.call(until, lambda: core.store.one("SELECT message FROM chat_sent"))
        assert bot.calls["sendMessage"] == [{
            "chat_id": GROUP, "reply_parameters": {"message_id": 11, "allow_sending_without_reply": True}, "message_thread_id": 42,
            "text": f"<b>{html.escape(ROUTINE_NAME)}</b>\n{html.escape(answer)}", "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}]
        c.portal.call(bot.deliver, update(12, text="and staging?", topic=42, reply=bot.last_message_id))
        assert len(anthropic.fired) == 2 and "and staging?" in anthropic.fired[-1][1]
        assert [r["root"] for r in core.store.q("SELECT root FROM chat_relays ORDER BY rowid")] == ["11", "11"]
        assert block.missing() == []


def test_stranger_cannot_wake_or_receive_a_reply_from_a_private_target(run):
    with run() as (core, c, _, bot, anthropic):
        c.portal.call(bot.deliver, update(11, user=STRANGER, text="/swb example-runner please run"))
        context = json.loads(core.store.one("SELECT data FROM inbox WHERE kind = 'context'")["data"])
        assert context["trust"] == "external" and context["target"] == "example-runner" and "<<<swb-untrusted" in context["text"]
        assert not anthropic.fired and not bot.calls["sendMessage"] and not bot.calls["setMessageReaction"]


def test_native_reply_to_second_chunk_keeps_the_original_target(run):
    with run(persona_prefix=False) as (core, c, _, bot, anthropic):
        c.portal.call(bot.deliver, update(11, mention=True, text="example-runner start"))
        handle = core.store.one("SELECT handle FROM chat_relays")["handle"]
        result = payload(call(c, STATIC_KEY, "reply", {"conversation": handle, "text": "x" * 4097}))
        assert result["delivered"]
        c.portal.call(until, lambda: core.store.one("SELECT 1 FROM chat_sent WHERE message = '9002'"))
        assert [len(post["text"]) for post in bot.calls["sendMessage"]] == [4096, 1]
        assert [(row["message"], row["root"]) for row in core.store.q("SELECT message, root FROM chat_sent ORDER BY message")] == [
            ("9001", "11"), ("9002", "11")]
        c.portal.call(bot.deliver, update(12, text="follow up", reply=9002))
        assert len(anthropic.fired) == 2 and "follow up" in anthropic.fired[-1][1]
        event = json.loads(core.store.one("SELECT data FROM inbox WHERE kind = 'telegram_event' ORDER BY id DESC")["data"])
        assert (event["chat_kind"], event["target"], event["from"]["root"]) == ("reply", "example-runner", "11")


def test_dm_space_seen_chats_and_bot_self_filter(run):
    with run() as (core, c, block, bot, _):
        c.portal.call(bot.deliver, update(10, chat=UNKNOWN, mention=True), update(11, chat=775555, user=BOT, kind="private"),
                      update(12, chat=OWNER_ID, kind="private", text=f"/ask@{BOT_NAME} testing"))
        events = core.store.q("SELECT data FROM inbox WHERE kind = 'telegram_event'")
        event, = (json.loads(row["data"]) for row in events)
        assert (event["chat_kind"], event["from"]["space"], event["text"], event["from"]["user_name"]) == ("dm", "dm", "testing", "Example User")
        assert c.portal.call(block.open_dm, "dm", str(OWNER_ID)) == str(OWNER_ID)
        assert c.portal.call(block.open_dm, "dm", str(GROUP)) is None
        assert block.setup()["seen_chats"] == [{"id": OWNER_ID, "title": "Example group", "type": "private"},
                                               {"id": UNKNOWN, "title": "Example group", "type": "supergroup"}]
        c.portal.call(bot.deliver, *(update(i, chat=-1000000 - i, mention=True) for i in range(20, 44)))
        assert [seen["id"] for seen in block.setup()["seen_chats"]] == [-1000000 - i for i in range(43, 23, -1)]


def test_mentions_use_utf16_entities_and_only_strip_a_leading_address(run):
    with run() as (_, c, block, bot, _):
        incoming = []

        async def capture(msg):
            incoming.append(msg)

        block.ingest = capture
        c.portal.call(bot.deliver,
                      update(21, text=f"😀 @{BOT_NAME}", entities=[{"type": "mention", "offset": 3, "length": len(BOT_NAME) + 1}]),
                      update(22, text="😀 Bot then rest", entities=[{"type": "text_mention", "offset": 0, "length": 6, "user": {"id": BOT}}]),
                      update(23, text="/swb@SomeOtherBot leave me alone"),
                      update(24, text=f"@{BOT_NAME} no entity"),
                      update(25, text=f"/help@{BOT_NAME.upper()} help"),
                      update(26, text="/swb dispatch", topic=42))
        assert [(x.mention, x.text, x.thread) for x in incoming] == [
            (True, f"😀 @{BOT_NAME}", None), (True, "then rest", None),
            (False, "/swb@SomeOtherBot leave me alone", None), (False, f"@{BOT_NAME} no entity", None),
            (True, "help", None), (True, "dispatch", None)]


def test_offset_survives_restart_and_is_scoped_to_bot_identity(run):
    with run() as (_, c, _, bot, anthropic):
        assert c.portal.call(until, lambda: bot.calls["getUpdates"])[0]["offset"] == 0
        c.portal.call(bot.deliver, update(51, text="/swb example-runner first"), update(50, chat=UNKNOWN))
        assert len(anthropic.fired) == 1
        assert bot.calls["getUpdates"][-1]["offset"] == 52
    with run() as (_, c, _, bot, anthropic):
        assert c.portal.call(until, lambda: bot.calls["getUpdates"])[0]["offset"] == 52
        c.portal.call(bot.deliver, update(51, text="/swb example-runner first"), update(52, text="/swb example-runner next"))
        assert len(anthropic.fired) == 1 and "next" in anthropic.fired[0][1]
    other = FakeBotAPI()
    other.bot_id = 444444
    with run(bot=other) as (_, c, _, bot, _):
        assert c.portal.call(until, lambda: bot.calls["getUpdates"])[0]["offset"] == 0


def test_post_splits_html_on_character_boundaries_and_returns_each_id(run):
    with run(connect=False) as (_, c, block, bot, _):
        body = ("&<>\"'b" * 1000) + "end"
        name = '<agent & "team">'
        result = c.portal.call(lambda: block.post("group", str(GROUP), body, root="5", persona=Persona("agent", name)))
        texts = [p["text"] for p in bot.calls["sendMessage"]]
        prefix = f"<b>{html.escape(name)}</b>\n"
        assert result == [str(9001 + i) for i in range(len(texts))]
        assert len(texts) > 1 and texts[0].startswith(prefix)
        assert all(0 < len(t) <= 4096 for t in texts)
        assert html.unescape(texts[0].removeprefix(prefix)) + "".join(html.unescape(t) for t in texts[1:]) == body
        assert all(p["parse_mode"] == "HTML" and p["reply_parameters"]["message_id"] == 5 for p in bot.calls["sendMessage"])
        assert not bot.calls["getMe"] and not block.connected()


def test_persona_can_be_disabled_and_plain_text_splits_at_4096(run):
    with run(connect=False, persona_prefix=False) as (_, c, block, bot, _):
        c.portal.call(lambda: block.post("group", str(GROUP), "<" * 4097, persona=Persona("agent", "Agent")))
        assert [len(p["text"]) for p in bot.calls["sendMessage"]] == [4096, 1]
        assert all("parse_mode" not in p and set(p["text"]) == {"<"} for p in bot.calls["sendMessage"])


def test_polling_resumes_at_the_same_offset_after_rate_limit(run, caplog):
    with run(ack_reaction=None) as (_, c, block, bot, anthropic):
        c.portal.call(bot.deliver, update(59, text="/swb example-runner first"))
        assert not bot.calls["setMessageReaction"]
        failure = httpx.Response(429, json={"ok": False, "error_code": 429, "description": TOKEN, "parameters": {"retry_after": 1}})
        c.portal.call(bot.updates.put_nowait, failure)
        c.portal.call(until, lambda: not block.connected())
        c.portal.call(until, lambda: len(bot.calls["getUpdates"]) == 3)
        assert [call["offset"] for call in bot.calls["getUpdates"]] == [0, 60, 60]
        c.portal.call(bot.deliver, update(60, text="/swb example-runner next"))
        assert len(anthropic.fired) == 2
    assert TOKEN not in caplog.text


def test_429_waits_for_retry_after_and_token_does_not_reach_logs_or_exceptions(run, caplog):
    caplog.set_level(logging.DEBUG)
    with run() as (_, c, block, bot, _):
        bot.send_errors[:] = [(429, 1)]
        c.portal.call(block.post, "group", str(GROUP), "retry me")
        assert bot.calls["sendMessage"][0] == bot.calls["sendMessage"][1]
        assert bot.send_times[1] - bot.send_times[0] >= 1
        bot.send_errors[:] = [(None, None), (400, None)]
        for expected in ("network request failed", "API error 400"):
            with pytest.raises(BlockError, match=expected) as exc:
                c.portal.call(block.post, "group", str(GROUP), "fail")
            assert TOKEN not in "".join(traceback.format_exception(exc.value))
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("code", [401, 404])
def test_missing_before_token_and_after_botfather_rejects_it(run, code, caplog):
    bot = FakeBotAPI()
    bot.rejected[REJECTED] = code
    with run(token=None, bot=bot) as (_, c, block, _, _):
        setup = block.setup()
        assert setup["bot_father_url"] == "https://t.me/BotFather"
        assert setup["token_source"] is None and setup["bot_url"] is None and setup["claim_pending"] is False
        assert block.missing() == ["no bot token: open the link from telegram_provision, or set TELEGRAM_BOT_TOKEN"]
        provision = payload(call(c, STATIC_KEY, "telegram_provision", {}))
        assert provision["setup"]["claim_pending"] is True
        path = provision["link"].removeprefix(PUBLIC)
        form = c.get(path, headers=OWNER)
        assert form.status_code == 200, form.text
        csrf = form.text.split('name="csrf" value="')[1].split('"')[0]
        assert c.post(path, headers=OWNER, data={"csrf": csrf, "token": REJECTED}).status_code == 200
        assert c.portal.call(until, block.missing) == ["token rejected: open a new token link"]
        assert not block.connected() and len(bot.calls["getMe"]) == 1
        assert REJECTED not in caplog.text


def test_claim_saves_token_and_reconnects_and_status_orders_remaining_setup(run):
    bot = FakeBotAPI()
    bot.privacy, bot.hidden = True, {GROUP}
    spaces = {"group": {"chats": [GROUP], "listen": [GROUP]}, "dm": {"owner_ids": [OWNER_ID]}}
    with run(token=None, bot=bot, spaces=spaces) as (core, c, block, _, _):
        assert block.missing() == ["no bot token: open the link from telegram_provision, or set TELEGRAM_BOT_TOKEN",
                                   "set blocks.telegram.spaces.group.owner_ids (DM the bot and look up your id in telegram_status)"]
        provision = payload(call(c, STATIC_KEY, "telegram_provision", {}))
        path = provision["link"].removeprefix(PUBLIC)
        form = c.get(path, headers=OWNER)
        assert form.status_code == 200, form.text
        csrf = form.text.split('name="csrf" value="')[1].split('"')[0]
        response = c.post(path, headers=OWNER, data={"csrf": csrf, "token": TOKEN})
        assert response.status_code == 200 and TOKEN not in response.text
        c.portal.call(until, block.connected)
        status = payload(call(c, STATIC_KEY, "telegram_status", {}))
        assert status["missing"] == ["in @BotFather: /setprivacy, choose the bot, Disable, then remove and re-add the bot to the group",
                                     f"add the bot to group chat {GROUP}",
                                     "set blocks.telegram.spaces.group.owner_ids (DM the bot and look up your id in telegram_status)"]
        setup = status["setup"]
        assert setup["privacy_mode"] is True and setup["token_source"] == "stored" and setup["claim_pending"] is False
        assert setup["bot_url"] == f"https://t.me/{BOT_NAME}" and setup["add_to_group_url"] == f"https://t.me/{BOT_NAME}?startgroup=swb"
        assert "/swb@<bot username>" in setup["steps"][2]
        assert TOKEN not in core.store.one("SELECT sealed FROM block_secrets")["sealed"]


@pytest.mark.parametrize("config,path", [
    ({"spaces": {"group": {"chats": [1]}, "dm": {}}}, "spaces.group.chats"),
    ({"spaces": {"group": {"chats": [str(GROUP)]}, "dm": {}}}, "spaces.group.chats"),
    ({"spaces": {"group": {"chats": [GROUP]}, "dm": {"chats": [GROUP]}}}, "spaces.dm.chats"),
    ({"dm_space": "unknown"}, "dm_space"),
    ({"persona_prefix": "false"}, "persona_prefix"),
    ({"ack_reaction": False}, "ack_reaction"),
    ({"token_env": 42}, "token_env"),
])
def test_invalid_config_reports_precise_path(run, config, path):
    with pytest.raises(BlockError, match=rf"blocks\.telegram\.{path}"):
        with run(token=None, **config):
            pytest.fail("invalid config accepted")
