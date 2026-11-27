"""ChatGPT dots: discovery, the relay channel, sends as the owner or as @swb, and the dots' threaded replies."""

import asyncio
import contextlib
import json
import sqlite3
from functools import partial

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from conftest import call, error_text, principal
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
        {"id": DOT, "name": "workspace-dots", "real_name": "Workspace Dots", "is_bot": True, "profile": {"bot_id": DOT_BOT, "api_app_id": "ADOTS", "image_192": "https://img.example/dot.png"}},
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


def build(tmp_path, dots=None, resources=None, relay_exists=False, in_channel=(), dm=True, group=True, known=None, routines=None):
    world, hooks = FakeWorld(), Hooks()
    scans = []
    world.canned["users_list"] = lambda token, kw: (scans.append(token), {"ok": True, "members": members()})[1]
    world.canned["conversations_list"] = lambda token, kw: {"ok": True, "channels": ([{"id": OWNER_DM, "user": DOT}] if dm else []) if kw.get("types") == "im"
                                                            else [{"id": RELAY, "name": "agents"}] if relay_exists else []}
    world.canned["conversations_open"] = lambda token, kw: opened(token, kw, group)
    world.history = {}
    world.canned["conversations_history"] = lambda token, kw: {"ok": True, "messages": [m for m in world.history.get(kw["channel"], []) if float(m["ts"]) > float(kw.get("oldest") or 0)]}
    world.canned["conversations_replies"] = lambda token, kw: {"ok": True, "messages": [m for m in world.history.get(kw["channel"], []) if m.get("thread_ts") == kw["ts"]]}
    world.canned["conversations_create"] = lambda token, kw: {"ok": True, "channel": {"id": RELAY, "name": kw["name"]}}
    world.canned["conversations_members"] = lambda token, kw: {"ok": True, "members": [OWNER_A, *in_channel]}
    slack = base_config()
    slack["workspaces"]["a2a"].pop("sinks", None)
    if known:
        slack["workspaces"]["a2a"]["known"] = known
    cfg = {"public_url": PUBLIC, "slack": slack, "destinations": {"dots": {"workspace": "a2a", **(dots or {})}},
           "visibility": {"defaults": {"target": "private", "channel": "shared"}, "resources": resources or {}}}
    if routines:
        cfg["destinations"]["claude-routine"] = {"routines": routines}
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
        assert {d["slug"] for d in block.dots()} == {"workspace-dots", "helper"}
        addrs = {t["slugs"][0]: t["address"] for t in tool(client, "targets", {})["targets"] if t.get("kind") == "dot"}
        assert addrs == {"workspace-dots": f"dots:dot:{DOT}", "helper": f"dots:dot:{HELPER}"}
        assert core.slack.targets["workspace-dots"].kind == "dot"
        assert any(r[0] == "dot workspace-dots" for r in block.page_rows())


def test_config_renames_and_disables_dots_and_dots_are_private_by_default(tmp_path):
    with started(tmp_path, dots={"app_ids": ["AHELPER"], "dots": {"workspace-dots": {"slug": "dots"}, "helper": {"disabled": True}}}) as (core, world, scans, client):
        assert [d["slug"] for d in core.blocks["dots"].dots()] == ["dots"]
        assert core.visibility.rule("target:dots") == "private"


def test_dot_discovery_preserves_a_known_registrant_and_leaves_unknown_registrants_blank(tmp_path):
    with started(tmp_path, dots={"dots": {
        "override": {"user_id": "UMISSING01"}, "workspace-dots": {"name": "Example alias"},
    }}) as (core, _, _, client):
        targets = {row["id"]: row for row in client.portal.call(core.targets) if row["kind"] == "dot"}
        assert targets[DOT]["name"] == "Example alias"
        assert targets[DOT]["created_by"] is None
        assert targets["UMISSING01"]["created_by"] is None
        core.store.x("UPDATE dots SET created_by = ? WHERE user_id = ?", ("Alex Rivera", DOT))
        client.portal.call(core.blocks["dots"].rescan)
        assert core.blocks["dots"].dot(DOT)["created_by"] == "Alex Rivera"


@pytest.mark.parametrize("slack", [None, {}])
def test_dots_warn_once_and_skip_rescan_without_a_slack_workspace(make, caplog, slack):
    config = {"version": 2, "blocks": {"dots": {}}}
    if slack is not None:
        config["blocks"]["slack"] = slack
    core, client = make(config, env={})
    dots = core.blocks["dots"]
    assert getattr(dots, "_task", None) is None
    assert client.portal.call(dots.rescan) == client.portal.call(dots.rescan) == []
    warnings = [r for r in caplog.records if r.name == "switchboard.blocks.dots"]
    assert [(r.getMessage(), r.exc_info) for r in warnings] == [(dots.next_step(), None)]


def test_dots_use_configured_mailboxes_without_slack(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"version": 2, "public_url": PUBLIC, "blocks": {"mcp": {}, "dots": {
        "delivery": "mailbox", "default": "research-dot",
        "dots": {"research-dot": {"name": "Research", "slug": "research"}, "planning-dot": {"mailbox": "claude"},
                 "disabled-dot": {"disabled": True}},
    }}}))
    core = Switchboard(path, tmp_path / "data", env={"SWITCHBOARD_KEY_FULL": KEY_FULL}, access_verifier=access)
    with TestClient(create_app(core), base_url=PUBLIC) as client:
        assert core.slack is None
        assert core.blocks["dots"].next_step() is None
        targets = tool(client, "targets", {"kind": "dot"})["targets"]
        assert {t["address"]: t["slugs"] for t in targets} == {
            "dots:dot:research-dot": ["research", "dot"], "dots:dot:planning-dot": ["planning-dot"],
        }
        assert next(t["name"] for t in targets if t["id"] == "research-dot") == "Research"
        assert core.visibility.rule("target:research") == "private"
        original = client.portal.call(partial(core.agent_send, principal("gemini"), to="full", text="Check the figures"))["handle"]
        out = tool(client, "send", {"to": "dot", "task": "Check the figures", "forward": original})
        handle = out["handle"]
        assert out["mode"] == "mailbox" and out["dot"] == "research" and out["to"] == "chatgpt"
        assert out["conversation"] == handle and out["mailbox"] and out["delivered"]
        assert core.inbox.handle(original)["meta"]["forwarded_to"] == handle
        h = core.inbox.handle(handle)
        assert h["destination"] == "mailbox" and h["owner"] == "chatgpt"
        assert h["meta"]["forward_of"] == h["meta"]["root"] == original
        (item,) = core.inbox.read(owner="chatgpt", since=0)["items"]
        assert item["handle"] == handle and item["text"] == "Check the figures" and item["from"]["agent"] == "full"

        client.portal.call(partial(core.verb, principal("chatgpt"), "reply", handle, text="The figures match"))
        res = tool(client, "result", {"conversation": handle})
        assert res["status"] == "answered" and res["final"]["text"] == "The figures match"
        res = client.portal.call(core.verb, principal("gemini"), "result", original)
        assert res["status"] == "answered" and res["final"]["text"] == "The figures match"

        custom = tool(client, "send", {"to": "dots:dot:planning-dot", "title": "Plan the review", "task": "Draft an outline"})
        (item,) = core.inbox.read(owner="claude", since=0)["items"]
        assert item["handle"] == custom["conversation"] and item["text"] == "Draft an outline"
        assert item["subject"] == core.inbox.handle(custom["handle"])["title"] == "Plan the review"


@pytest.mark.parametrize("group", [False, True])
def test_switching_to_mailbox_preserves_slack_conversations(tmp_path, group):
    with started(tmp_path, dots={"group_dm": group}) as (core, world, scans, client):
        old = tool(client, "send", {"to": "dot", "task": "Check the previous figures"})
        assert old["channel"] == (GROUP if group else RELAY)

    with started(tmp_path, dots={"delivery": "mailbox", "dots": {"research-dot": {}}}) as (core, world, scans, client):
        block = core.blocks["dots"]
        before = list(world.calls)
        out = tool(client, "send", {"to": "dot", "task": "Check the new figures"})
        assert out["mode"] == "mailbox" and out["to"] == "chatgpt"
        assert world.calls == before
        assert not scans and not calls(world, "conversations_create")
        assert block.on_directory not in core.slack.directory_listeners
        assert block.next_step() is None

        ts = "1700000700.1"
        if group:
            dot_says(world, old["channel"], ts, "Previous figures match", old["ts"])
            assert client.portal.call(block.poll_dms) == 1
        else:
            client.portal.call(core.slack.ingest, "a2a", dot_event(old["channel"], ts, "Previous figures match", old["ts"]))
        res = tool(client, "result", {"conversation": old["conversation"]})
        assert res["status"] == "answered" and "Previous figures match" in res["final"]["text"]


def test_team_join_and_user_change_of_a_bot_rescan(tmp_path):
    with started(tmp_path) as (core, world, scans, client):
        before = len(scans)
        client.portal.call(core.slack.handle_socket, "a2a", "events_api", {"event": {"type": "team_join", "user": {"id": "UNEW", "is_bot": True}}})
        client.portal.call(core.slack.handle_socket, "a2a", "events_api", {"event": {"type": "user_change", "user": {"id": "UHUMAN001", "is_bot": False}}})
        assert len(scans) == before + 1


def test_the_relay_channel_is_created_as_the_owner_and_nobody_is_invited(tmp_path):
    with started(tmp_path) as (core, world, scans, client):
        (create,) = calls(world, "conversations_create")
        assert create == ("xoxp-a", {"name": "agents", "is_private": True})
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


def dot_event(channel, ts, text, thread_ts=None):
    return {"type": "message", "bot_id": DOT_BOT, "user": DOT, "channel": channel, "ts": ts, "text": text, **({"thread_ts": thread_ts} if thread_ts else {})}


def bot_posts(world):
    return [kw for t, kw in calls(world, "chat_postMessage") if t == "xoxb-a"]


def test_an_agent_mentions_the_dot_in_the_group_dm_as_its_own_persona_and_gets_the_answer(tmp_path):
    with started(tmp_path, relay_exists=True, dots={"group_dm": True}) as (core, world, scans, client):
        out = tool(client, "send", {"to": "workspace-dots", "task": "summarise my week"})
        assert out["posted"] and out["as"] == "swb" and out["mode"] == "group_dm" and out["channel"] == GROUP
        ((token, post),) = calls(world, "chat_postMessage")
        assert token == "xoxb-a" and post["text"] == f"<@{DOT}> summarise my week" and post["username"] == "Capy"
        assert core.slack.store.list_requests() == []
        client.portal.call(core.slack.ingest, "a2a", {**dot_event(GROUP, "1700000700.1", "busy week"), "channel_type": "mpim"})
        res = tool(client, "result", {"conversation": out["conversation"]})
        assert res["status"] == "answered" and res["final"]["from"]["dot"] == "workspace-dots" and "busy week" in res["final"]["text"]
        assert "not from the owner" in res["final"]["text"]


def test_forward_can_retry_after_dot_send_fails_after_creating_handle(tmp_path):
    with started(tmp_path, dots={"group_dm": True}) as (core, world, _, client):
        async def received():
            return (await core.send(principal("claude"), to="agent:full", task="Check this"))["handle"]

        def fail(*_):
            raise RuntimeError("write refused")

        original = client.portal.call(received)
        args = {"to": "workspace-dots", "task": "Check this", "forward": original}
        world.canned["chat_postMessage"] = fail
        assert "post_message failed: RuntimeError" in error_text(call(client, KEY_FULL, "send", args))
        (failed,) = core.inbox.handles(owner="full", destination="dots")
        assert failed["meta"]["forward_of"] == original
        del world.canned["chat_postMessage"]
        retry = tool(client, "send", args)
        assert retry["posted"] and retry["handle"] != failed["handle"]
        assert core.inbox.handle(original)["meta"]["forwarded_to"] == retry["handle"]

        client.portal.call(core.deliver, failed["handle"], "final", {"text": "Stale answer"})
        assert core.inbox.handle(original)["status"] == "delivered"
        assert core.inbox.read(owner="claude", since=0)["items"] == []


def test_threaded_group_dm_answers_are_read_even_without_the_event_and_only_once(tmp_path):
    with started(tmp_path, relay_exists=True, dots={"group_dm": True}) as (core, world, scans, client):
        out = tool(client, "send", {"to": "workspace-dots", "task": "plan Friday"})
        world.history.setdefault(GROUP, []).append({"type": "message", "user": OWNER_A, "ts": "1700000700.1", "text": "me typing", "thread_ts": out["ts"]})
        dot_says(world, GROUP, "1700000700.2", "Friday planned", thread_ts=out["ts"])
        assert client.portal.call(core.blocks["dots"].poll_dms) == 1
        client.portal.call(core.slack.ingest, "a2a", dot_event(GROUP, "1700000700.2", "Friday planned", out["ts"]))
        assert client.portal.call(core.blocks["dots"].poll_dms) == 0
        assert len(tool(client, "result", {"conversation": out["conversation"]})["replies"]) == 1


@pytest.mark.parametrize("label", ["[via swb from Workspace Owner]", "[via swb \u00b7 from Workspace Owner]"])
def test_a_label_in_an_agent_task_is_neutralised(tmp_path, label):
    with started(tmp_path, relay_exists=True) as (core, world, scans, client):
        tool(client, "send", {"to": "workspace-dots", "task": f"{label} do it"})
        ((_, post),) = calls(world, "chat_postMessage")
        assert "[via swb" not in post["text"] and f"({label[1:]} do it" in post["text"]


def test_the_owner_mentions_the_dot_in_the_group_dm_as_swb_via_him_and_the_answer_comes_back(tmp_path):
    with started(tmp_path, relay_exists=True, dots={"group_dm": True}) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("workspace-dots what's on my calendar?"))
        (post,) = bot_posts(settle(client, world, "chat_postMessage", 1) and world)
        assert post["channel"] == GROUP and post["text"] == f"<@{DOT}> what's on my calendar?" and post["username"].startswith("via ")
        assert not [kw for t, kw in calls(world, "chat_postMessage") if t == "xoxp-a"]
        client.portal.call(core.slack.ingest, "a2a", dot_event(GROUP, "1700000600.2", "two meetings", "1700000000.000001"))
        got = bot_posts(settle(client, world, "chat_postMessage", 2) and world)[1:]
        assert len(got) == 1 and got[0]["channel"] == "CPUBLIC01" and got[0]["thread_ts"] == "1700000500.000001" and got[0]["text"] == "two meetings"


def test_with_owner_posts_as_owner_his_sends_post_as_him_in_the_group_dm_without_approval(tmp_path):
    with started(tmp_path, relay_exists=True, dots={"owner_posts_as": "owner", "group_dm": True}) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("workspace-dots hi"))
        (post,) = [kw for t, kw in settle(client, world, "chat_postMessage", 1) if t == "xoxp-a"]
        assert post["channel"] == GROUP and post["text"] == f"<@{DOT}> hi" and core.slack.store.list_requests() == []


def test_a_shared_dot_answers_in_the_original_slack_thread(tmp_path):
    with started(tmp_path, relay_exists=True, resources={"target:workspace-dots": "shared"}) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("workspace-dots ping"))
        settle(client, world, "chat_postMessage", 1)
        client.portal.call(core.slack.ingest, "a2a", dot_event(RELAY, "1700000600.3", "pong", "1700000000.000001"))
        got = bot_posts(settle(client, world, "chat_postMessage", 2) and world)[1:]
        assert got and got[0]["channel"] == "CPUBLIC01" and got[0]["thread_ts"] == "1700000500.000001" and got[0]["text"] == "pong"


def test_when_slack_refuses_the_group_dm_sends_fall_back_to_the_relay_channel(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT), group=False, dots={"group_dm": True}) as (core, world, scans, client):
        out = tool(client, "send", {"to": "workspace-dots", "task": "hello"})
        assert out["mode"] == "relay" and out["channel"] == RELAY and out["as"] == "swb"
        assert any("cannot_dm_bot" in r[1] for r in core.blocks["dots"].page_rows())
        client.portal.call(core.slack.ingest, "a2a", {"type": "message", "user": "USOMEONE", "channel": RELAY, "ts": "1700000600.1", "thread_ts": out["ts"], "text": "me too"})
        client.portal.call(core.slack.ingest, "a2a", dot_event(RELAY, "1700000600.2", "wrong thread", "1699999999.9"))
        client.portal.call(core.slack.ingest, "a2a", dot_event(RELAY, "1700000700.1", "hi", out["ts"]))
        res = tool(client, "result", {"conversation": out["conversation"]})
        assert res["status"] == "answered" and len(res["replies"]) == 1 and "hi" in res["final"]["text"]


@pytest.mark.parametrize("channel", [RELAY, GROUP])
def test_owner_mentions_route_in_dots_channels_but_dot_replies_are_consumed(tmp_path, channel):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT), dots={"group_dm": channel == GROUP},
                 routines={"claude": {"agent": "full"}}) as (core, _, _, client):
        dot = tool(client, "send", {"to": "workspace-dots", "task": "Check this"})
        assert dot["channel"] == channel
        push = client.portal.call(core.slack.ingest, "a2a", mention("claude check uptime", channel=channel))
        assert push is not None and (push["kind"], push["target"], push["trust"]) == ("mention", "claude", "owner")
        (item,) = [i for i in core.inbox.read(owner="full", since=0)["items"] if i["kind"] == "message"]
        assert "check uptime" in item["text"] and core.slack.store.relay(item["handle"])["channel"] == channel

        text = f"<@{BOT_USER}> claude dot answer"
        assert client.portal.call(core.slack.ingest, "a2a", dot_event(channel, "1700000600.1", text, "1699999999.9")) is None
        file_share = {**dot_event(channel, "1700000600.15", text, dot["ts"]), "subtype": "file_share"}
        del file_share["user"]
        assert client.portal.call(core.slack.ingest, "a2a", file_share) is None
        assert client.portal.call(core.slack.ingest, "a2a", dot_event(channel, "1700000600.2", text, dot["ts"])) is None
        result = tool(client, "result", {"conversation": dot["handle"]})
        assert result["status"] == "answered" and len(result["replies"]) == 1 and text in result["final"]["text"]


def test_with_group_dm_off_the_owner_posts_as_himself_in_his_own_dm_and_the_answer_is_polled(tmp_path):
    with started(tmp_path, relay_exists=True, dots={"owner_dm": True}) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("workspace-dots what's on my calendar?"))
        (post,) = [kw for t, kw in settle(client, world, "chat_postMessage", 1) if t == "xoxp-a"]
        assert post["channel"] == OWNER_DM and post["text"] == "what's on my calendar?"
        dot_says(world, OWNER_DM, "1699999999.9", "old answer")
        dot_says(world, OWNER_DM, "1700000600.2", "two meetings")
        assert client.portal.call(core.blocks["dots"].poll_dms) == 1
        assert client.portal.call(core.blocks["dots"].poll_dms) == 0


def test_agents_post_as_him_only_with_agents_as_owner_and_every_such_post_is_audited(tmp_path):
    with started(tmp_path, relay_exists=True, dots={"owner_dm": True, "agents_as_owner": True}) as (core, world, scans, client):
        out = tool(client, "send", {"to": "workspace-dots", "task": "hello"})
        ((token, post),) = calls(world, "chat_postMessage")
        assert out["as"] == "owner" and token == "xoxp-a" and post["channel"] == OWNER_DM and post["text"] == f"[via swb from {core.agents.label('full')}] hello"
        con = sqlite3.connect(tmp_path / "data" / "slack.db")
        con.row_factory = sqlite3.Row
        (row,) = [dict(r) for r in con.execute("SELECT * FROM audit WHERE action = 'dot_post_as_owner'")]
        detail = json.loads(row["detail"])
        assert row["actor"] == "agent:full" and detail["sender"] == "full" and detail["principal"] == "key" and detail["handle"] == out["conversation"]


def test_by_default_an_agent_mentions_the_dot_in_the_channel_as_its_persona_and_the_thread_answer_comes_back(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT)) as (core, world, scans, client):
        out = tool(client, "send", {"to": "workspace-dots", "task": "hello"})
        ((token, post),) = calls(world, "chat_postMessage")
        assert out["mode"] == "relay" and out["channel"] == RELAY and token == "xoxb-a"
        assert post["text"] == f"<@{DOT}> hello" and post["username"] == "Capy" and not post.get("thread_ts")
        assert not [n for _, n, _ in world.calls if n == "conversations_open"]
        client.portal.call(core.slack.ingest, "a2a", dot_event(RELAY, "1700000700.1", "hi there", out["ts"]))
        res = tool(client, "result", {"conversation": out["conversation"]})
        assert res["status"] == "answered" and "hi there" in res["final"]["text"]


def test_by_default_the_owner_mentions_the_dot_in_the_channel_as_swb_via_him(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT)) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("workspace-dots hi"))
        (post,) = bot_posts(settle(client, world, "chat_postMessage", 1) and world)
        assert post["channel"] == RELAY and post["text"] == f"<@{DOT}> hi" and post["username"].startswith("via ")
        assert not [kw for t, kw in calls(world, "chat_postMessage") if t == "xoxp-a"]


@pytest.mark.parametrize("group, channel", [(False, RELAY), (True, GROUP)])
def test_a_non_owner_reaching_a_shared_dot_posts_as_swb_naming_them(tmp_path, group, channel):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT), dots={"group_dm": group},
                 resources={"target:workspace-dots": "shared"}, known={"UKNOWN001": "Joe"}) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("workspace-dots hi", user="UKNOWN001"))
        (post,) = [kw for t, kw in settle(client, world, "chat_postMessage", 1) if t == "xoxb-a"]
        assert post["channel"] == channel and post["text"].startswith(f"<@{DOT}> ") and "via switchboard" in post["text"]
        assert not [kw for t, kw in calls(world, "chat_postMessage") if t == "xoxp-a"]


def test_a_non_owner_never_reaches_a_private_dot(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT), known={"UKNOWN001": "Joe"}) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("workspace-dots hi", user="UKNOWN001"))
        client.portal.call(asyncio.sleep, 0.3)
        assert not [kw for t, kw in calls(world, "chat_postMessage") if kw["channel"] in (RELAY, OWNER_DM, GROUP)]


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


def test_dot_names_the_only_dot_and_config_picks_it_among_several(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT)) as (core, world, scans, client):
        (t,) = [t for t in tool(client, "targets", {})["targets"] if t.get("kind") == "dot"]
        assert t["slugs"] == ["workspace-dots", "dot"] and core.slack.targets["dot"].id == DOT
        out = tool(client, "send", {"to": "dot", "task": "hi"})
        assert out["dot"] == "workspace-dots" and calls(world, "chat_postMessage")[0][1]["text"] == f"<@{DOT}> hi"
        client.portal.call(core.slack.ingest, "a2a", mention("dot what's up?", ts="1700000500.9"))
        assert [kw for t, kw in settle(client, world, "chat_postMessage", 2) if t == "xoxb-a"][1]["text"] == f"<@{DOT}> what's up?"
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    with started(tmp_path / "b", relay_exists=True, dots={"app_ids": ["AHELPER"]}) as (core, world, scans, client):
        assert core.blocks["dots"].dot("dot") is None and "dot" not in core.slack.targets
    with started(tmp_path / "c", relay_exists=True, dots={"app_ids": ["AHELPER"], "default": "helper"}) as (core, world, scans, client):
        assert core.blocks["dots"].dot("dot")["user_id"] == HELPER and core.blocks["dots"].dot("workspace-dots")["user_id"] == DOT


def test_a_dot_answer_relayed_into_another_thread_posts_as_the_dot_with_its_picture(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT), resources={"target:workspace-dots": "shared"}) as (core, world, scans, client):
        client.portal.call(core.slack.ingest, "a2a", mention("dot ping"))
        settle(client, world, "chat_postMessage", 1)
        client.portal.call(core.slack.ingest, "a2a", dot_event(RELAY, "1700000600.3", "pong", "1700000000.000001"))
        got = bot_posts(settle(client, world, "chat_postMessage", 2) and world)[1:]
        assert got[0]["channel"] == "CPUBLIC01" and got[0]["username"] == "Workspace Dots" and got[0]["icon_url"] == "https://img.example/dot.png"


def test_a_persona_without_a_picture_uses_the_bots_own_picture_never_slacks_initials(tmp_path):
    with started(tmp_path, relay_exists=True, in_channel=(BOT_USER, DOT)) as (core, world, scans, client):
        core.slack.runtimes["a2a"].bot_image = "https://img.example/app.png"
        tool(client, "send", {"to": "dot", "task": "hi"})
        ((_, post),) = calls(world, "chat_postMessage")
        assert post["username"] == "Capy" and post["icon_url"] == "https://img.example/capy.png"
        out = client.portal.call(core.slack.runtimes["a2a"].execute, "bot", "post_message", {"channel": RELAY, "text": "x"}, {"id": "bare", "name": "Bare"})
        assert out["ts"] and calls(world, "chat_postMessage")[-1][1]["icon_url"] == "https://img.example/app.png"

def test_posts_without_a_persona_take_the_apps_own_name_and_picture_or_a_neutral_icon(tmp_path):
    with started(tmp_path, relay_exists=True) as (core, world, scans, client):
        rt = core.slack.runtimes["a2a"]
        world.canned["users_info"] = lambda token, kw: {"ok": True, "user": {"id": kw["user"], "name": "swb", "profile": {
            "real_name": "SwitchBoard", "is_custom_image": True, "image_192": "https://img.example/app.png"}}}
        client.portal.call(rt.refresh_identity)
        client.portal.call(rt.execute, "bot", "post_message", {"channel": RELAY, "text": "x"}, None)
        post = calls(world, "chat_postMessage")[-1][1]
        assert post["username"] == "SwitchBoard" and post["icon_url"] == "https://img.example/app.png"
        world.canned["users_info"] = lambda token, kw: {"ok": True, "user": {"id": kw["user"], "name": "swb", "profile": {
            "real_name": "swb", "is_custom_image": False, "image_192": "https://img.example/initials.png"}}}
        client.portal.call(rt.refresh_identity)
        client.portal.call(rt.execute, "bot", "post_message", {"channel": RELAY, "text": "x"}, None)
        post = calls(world, "chat_postMessage")[-1][1]
        assert post["username"] == "swb" and post["icon_emoji"] == ":electric_plug:" and "icon_url" not in post


def test_config_default_identity_overrides_the_apps_own(tmp_path):
    from switchboard.slack.config import parse_config
    cfg = base_config()
    cfg["workspaces"]["a2a"]["default_identity"] = {"name": "SwitchBoard", "icon_url": "https://img.example/mine.png"}
    ws = parse_config(cfg, ENV).workspaces["a2a"]
    assert dict(ws.default_identity) == {"name": "SwitchBoard", "icon_url": "https://img.example/mine.png"}
