"""ChatGPT dots: discovery, the relay channel, sends as the owner or as @swb, and the dots' threaded replies."""

import asyncio
import contextlib
import json

import httpx
import yaml
from starlette.testclient import TestClient

from slackfix import BOT_ID, BOT_USER, ENV, KEY_FULL, OWNER_A, FakeWorld, Hooks, base_config
from switchboard.app import create_app
from switchboard.core import Switchboard
from switchboard.slack.config import Grant
from switchboard.slack.writes import OWNER_RELAY, WriteError

PUBLIC = "https://switchboard.example.test"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
DOT, DOT_BOT, HELPER = "UDOT00001", "BDOT00001", "UHELP0001"
RELAY, OWNER_DM, GROUP = "GRELAY001", "DOWNDOT01", "GMPIM0001"


async def access(assertion):
    return None


def members():
    return [
        {"id": BOT_USER, "name": "swb", "is_bot": True, "profile": {"bot_id": BOT_ID}},
        {"id": DOT, "name": "example-dots", "real_name": "Owner Dots", "is_bot": True, "profile": {"bot_id": DOT_BOT, "api_app_id": "ADOTS"}},
        {"id": HELPER, "name": "helper", "is_bot": True, "profile": {"bot_id": "BHELP0001", "api_app_id": "AHELPER"}},
        {"id": "UGONE0001", "name": "old-dots", "is_bot": True, "deleted": True, "profile": {}},
        {"id": "UHUMAN001", "name": "dotty", "is_bot": False, "profile": {}},
        {"id": "UNOTION01", "name": "notion", "is_bot": True, "profile": {"api_app_id": "ANOTION"}},
    ]


def opened(token, kw, group=True):
    if DOT not in kw.get("users", ""):
        return {"ok": True, "channel": {"id": "DAPPROVER"}}
    if group and token == "xoxb-a" and kw.get("users") == f"{OWNER_A},{DOT}":
        return {"ok": True, "channel": {"id": GROUP}}
    return {"ok": False, "error": "missing_scope" if group else "cannot_dm_bot"}


def build(tmp_path, dots=None, resources=None, relay_exists=False, in_channel=(), dm=True, group=True):
    world, hooks = FakeWorld(), Hooks()
    scans = []
    world.canned["users_list"] = lambda token, kw: (scans.append(token), {"ok": True, "members": members()})[1]
    world.canned["conversations_list"] = lambda token, kw: {"ok": True, "channels": ([{"id": OWNER_DM, "user": DOT}] if dm else []) if kw.get("types") == "im"
                                                            else [{"id": RELAY, "name": "swb-dots"}] if relay_exists else []}
    world.canned["conversations_open"] = lambda token, kw: opened(token, kw, group)
    world.history = {}
    world.canned["conversations_history"] = lambda token, kw: {"ok": True, "messages": [m for m in world.history.get(kw["channel"], []) if float(m["ts"]) > float(kw.get("oldest") or 0)]}
    world.canned["conversations_replies"] = lambda token, kw: {"ok": True, "messages": [m for m in world.history.get(kw["channel"], []) if m.get("thread_ts") == kw["ts"]]}
    world.canned["conversations_create"] = lambda token, kw: {"ok": True, "channel": {"id": RELAY, "name": kw["name"]}}
    world.canned["conversations_members"] = lambda token, kw: {"ok": True, "members": [OWNER_A, *in_channel]}
    slack = base_config()
    slack["workspaces"]["a2a"].pop("sinks", None)
    cfg = {"public_url": PUBLIC, "slack": slack, "destinations": {"dots": {"workspace": "a2a", **(dots or {})}},
           "visibility": {"defaults": {"target": "private", "channel": "shared"}, "resources": resources or {}}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    core = Switchboard(path, tmp_path / "data", env={**ENV, "SWITCHBOARD_KEY_FULL": KEY_FULL}, access_verifier=access,
                       slack_options={"client_factory": world.factory, "http": httpx.AsyncClient(transport=httpx.MockTransport(hooks.handler)),
                                      "own_post_grace": 0, "connect": False})
    return core, world, scans


def tool(client, name, args):
    r = client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {KEY_FULL}"},
                    content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}))
    res = r.json()["result"]
    assert not res.get("isError"), res
    return json.loads(res["content"][0]["text"])


def calls(world, name):
    return [(t, kw) for t, n, kw in world.calls if n == name]


def settle(client, world, name, n):
    for _ in range(100):
        if len(calls(world, name)) >= n:
            break
        client.portal.call(asyncio.sleep, 0.03)
    return calls(world, name)


@contextlib.contextmanager
def started(tmp_path, **kw):
    core, world, scans = build(tmp_path, **kw)
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        client.portal.call(core.blocks["dots"].rescan)
        client.portal.call(core.origins["slack"].refresh)
        yield core, world, scans, client


def test_discovery_finds_dots_by_name_or_app_id_and_never_by_humans_or_itself(tmp_path):
    with started(tmp_path, dots={"app_ids": ["AHELPER"]}) as (core, world, scans, client):
        block = core.blocks["dots"]
        assert {d["slug"] for d in block.dots()} == {"example-dots", "helper"}
        addrs = {t["slugs"][0]: t["address"] for t in tool(client, "targets", {})["targets"] if t.get("kind") == "dot"}
        assert addrs == {"example-dots": f"dots:dot:{DOT}", "helper": f"dots:dot:{HELPER}"}
        assert core.slack.targets["example-dots"].kind == "dot"
        assert any(r[0] == "dot example-dots" for r in block.page_rows())


def test_config_renames_and_disables_dots_and_dots_are_private_by_default(tmp_path):
    with started(tmp_path, dots={"app_ids": ["AHELPER"], "dots": {"example-dots": {"slug": "dots"}, "helper": {"disabled": True}}}) as (core, world, scans, client):
        assert [d["slug"] for d in core.blocks["dots"].dots()] == ["dots"]
        assert core.visibility.rule("target:dots") == "private"


def test_team_join_and_user_change_of_a_bot_rescan(tmp_path):
    with started(tmp_path) as (core, world, scans, client):
        before = len(scans)
        client.portal.call(core.slack.handle_socket, "a2a", "events_api", {"event": {"type": "team_join", "user": {"id": "UNEW", "is_bot": True}}})
        client.portal.call(core.slack.handle_socket, "a2a", "events_api", {"event": {"type": "user_change", "user": {"id": "UHUMAN001", "is_bot": False}}})
        assert len(scans) == before + 1


def test_the_relay_channel_is_created_as_the_owner_and_nobody_is_invited(tmp_path):
    with started(tmp_path) as (core, world, scans, client):
        (create,) = calls(world, "conversations_create")
        assert create == ("xoxp-a", {"name": "swb-dots", "is_private": True})
        assert not [n for _, n, _ in world.calls if "invite" in n or "join" in n]
        assert core.blocks["dots"].problems() == []
        assert any(f"/invite <@{BOT_USER}> <@{DOT}>" in r[2] for r in core.blocks["dots"].page_rows())


def test_an_existing_relay_channel_is_reused(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT)) as (core, world, scans, client):
        assert not calls(world, "conversations_create")
        assert core.blocks["dots"].relay_channel() == RELAY and core.blocks["dots"].problems() == []


def mention(text, user=OWNER_A, channel="CPUBLIC01", ts="1700000500.000001", **extra):
    return {"type": "app_mention", "user": user, "channel": channel, "channel_type": "channel", "ts": ts, "text": f"<@{BOT_USER}> {text}", **extra}


def dot_says(world, channel, ts, text, thread_ts=None):
    world.history.setdefault(channel, []).append({"type": "message", "user": DOT, "bot_id": DOT_BOT, "ts": ts, "text": text, **({"thread_ts": thread_ts} if thread_ts else {})})


def test_the_owner_posts_in_his_own_dm_with_the_dot_without_approval_and_the_answer_comes_back(tmp_path):
    with started(tmp_path, relay_exists=True) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("example-dots what's on my calendar?"))
        (post,) = [kw for t, kw in settle(client, world, "chat_postMessage", 1) if t == "xoxp-a"]
        assert post["channel"] == OWNER_DM and post["text"] == "what's on my calendar?" and not post.get("thread_ts")
        assert core.slack.store.list_requests() == []
        world.history.setdefault(OWNER_DM, []).append({"type": "message", "user": OWNER_A, "ts": "1700000600.1", "text": "me typing"})
        dot_says(world, OWNER_DM, "1699999999.9", "old answer")
        dot_says(world, OWNER_DM, "1700000600.2", "two meetings")
        assert client.portal.call(core.blocks["dots"].poll_dms) == 1
        assert client.portal.call(core.blocks["dots"].poll_dms) == 0
        got = [kw for t, kw in settle(client, world, "chat_postMessage", 2) if t == "xoxb-a"]
        assert len(got) == 1 and got[0]["channel"] == "DAPPROVER" and got[0]["text"].endswith("two meetings")


def test_a_shared_dot_answers_in_the_slack_thread_and_threaded_dm_answers_count(tmp_path):
    with started(tmp_path, relay_exists=True, resources={"target:example-dots": "shared"}) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("example-dots ping"))
        (post,) = [kw for t, kw in settle(client, world, "chat_postMessage", 1) if t == "xoxp-a"]
        dot_says(world, OWNER_DM, "1700000600.3", "pong", thread_ts="1700000000.000001")
        assert client.portal.call(core.blocks["dots"].poll_dms) == 1
        got = [kw for t, kw in settle(client, world, "chat_postMessage", 2) if t == "xoxb-a"]
        assert got and got[0]["channel"] == "CPUBLIC01" and got[0]["thread_ts"] == "1700000500.000001" and got[0]["text"] == "pong"


def test_without_a_dm_the_owner_falls_back_to_the_relay_channel_and_its_threads(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT), dm=False) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("example-dots hello"))
        (post,) = [kw for t, kw in settle(client, world, "chat_postMessage", 1) if t == "xoxp-a"]
        assert post["channel"] == RELAY and post["text"] == f"<@{DOT}> hello"
        thread = "1700000000.000001"
        client.portal.call(core.slack.ingest, "a2a", {"type": "message", "user": "USOMEONE", "channel": RELAY, "ts": "1700000600.1", "thread_ts": thread, "text": "me too"})
        client.portal.call(core.slack.ingest, "a2a", {"type": "message", "subtype": "bot_message", "bot_id": DOT_BOT, "channel": RELAY, "ts": "1700000600.2",
                                                     "thread_ts": "1699999999.9", "text": "wrong thread"})
        client.portal.call(core.slack.ingest, "a2a", {"type": "message", "bot_id": DOT_BOT, "user": DOT, "channel": RELAY, "ts": "1700000600.3",
                                                     "thread_ts": thread, "text": "hi back"})
        got = [kw for t, kw in settle(client, world, "chat_postMessage", 2) if t == "xoxb-a"]
        assert len(got) == 1 and got[0]["text"].endswith("hi back")


def test_a_mention_in_the_relay_channel_is_never_routed(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT)) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("example-dots hi", channel=RELAY))
        client.portal.call(asyncio.sleep, 0.2)
        assert not calls(world, "chat_postMessage")


def test_an_agent_send_posts_as_swb_in_a_group_dm_and_gets_the_dot_answer(tmp_path):
    with started(tmp_path, relay_exists=True) as (core, world, scans, client):
        out = tool(client, "send", {"to": "example-dots", "task": "summarise my week"})
        assert out["posted"] and out["as"] == "swb" and out["mode"] == "group_dm"
        ((token, post),) = calls(world, "chat_postMessage")
        assert token == "xoxb-a" and post["channel"] == GROUP and post["text"].startswith(f"<@{DOT}> summarise my week") and "via switchboard" in post["text"]
        client.portal.call(core.slack.ingest, "a2a", {"type": "message", "bot_id": DOT_BOT, "user": DOT, "channel": GROUP, "channel_type": "mpim",
                                                     "ts": "1700000700.1", "text": "busy week"})
        res = tool(client, "result", {"conversation": out["conversation"]})
        assert res["status"] == "answered" and res["final"]["from"]["dot"] == "example-dots" and "busy week" in res["final"]["text"]
        assert "not from the owner" in res["final"]["text"]


def test_when_slack_refuses_the_group_dm_an_agent_send_uses_the_relay_channel(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT), group=False) as (core, world, scans, client):
        out = tool(client, "send", {"to": "example-dots", "task": "hello"})
        assert out["mode"] == "relay" and out["channel"] == RELAY
        assert any("cannot_dm_bot" in r[1] for r in core.blocks["dots"].page_rows())
        client.portal.call(core.slack.ingest, "a2a", {"type": "message", "bot_id": DOT_BOT, "user": DOT, "channel": RELAY, "ts": "1700000700.1",
                                                     "thread_ts": out["ts"], "text": "hi"})
        assert tool(client, "result", {"conversation": out["conversation"]})["status"] == "answered"


def test_the_owner_relay_key_posts_only_into_a_relay_channel_and_other_user_posts_still_need_approval(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT)) as (core, world, scans, client):
        key = Grant(id=OWNER_RELAY, persona=None, allow_persona_override=False, allowed_personas=None, scopes={"a2a": ("user",)})
        try:
            client.portal.call(core.slack.gate.submit, key, "a2a", "user", "post_message", {"channel": "CPUBLIC01", "text": "hi"})
        except WriteError:
            pass
        else:
            raise AssertionError("the owner relay key posted outside the relay channel")
        other = Grant(id="full", persona=None, allow_persona_override=False, allowed_personas=None, scopes={"a2a": ("user",)})
        out = client.portal.call(core.slack.gate.submit, other, "a2a", "user", "post_message", {"channel": RELAY, "text": "hi"})
        assert out["status"] != "sent"
