import asyncio
import json
from contextlib import contextmanager
from dataclasses import replace
from functools import partial

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from conftest import call, error_text, payload, use_capy
from slackfix import BOT_ID, BOT_USER, ENV, KEY_FULL, OWNER_A, TEAM_A, FakeWorld, Hooks
from switchboard.app import create_app
from switchboard.core import CoreError, Switchboard
from switchboard.slack.config import ConfigError, parse_config

CHANNEL = "CPUBLIC01"
NAME = "chan-cpublic01"
TS = "1700000500.000002"
THREAD = "1700000500.000001"
EXTERNAL = "USTRANGER"


@pytest.fixture
def opened(tmp_path):
    @contextmanager
    def build(*, channels=None, mapped="docs", workspace_project="workspace", shared="shared", turn_cap=200, rate=5, write="free", intake="socket",
              hooks=None, capy=None):
        world, fires = FakeWorld(), []
        ws = {"team_id": TEAM_A, "app_token_env": "A_APP", "identities": {"bot": {"token_env": "A_BOT", "write": write}},
              "sender_rate": {"count": rate}, "known": {"BKNOWN001": "Build bot"}, "ack_reaction": "eyes", "intake": intake}
        slack = {"workspaces": {"team": ws}, "workspace_projects": {"team": workspace_project},
                 "channel_projects": {"team": {} if mapped == "default" else {CHANNEL: mapped}},
                 "listen_channels": {"team": [f"#{NAME}"] if channels is None else channels},
                 "approvals": {"notify_workspace": "team", "approver_user_id": OWNER_A},
                 "personas": {"claude": {"name": "Routine"}, "doorman": {"name": "Doc doorman"}}}
        slack_options = {"client_factory": world.factory, "own_post_grace": 0, "connect": False}
        if hooks is not None:
            ws.update(watched=[CHANNEL], default_target="claude",
                      sinks=[{"name": "feed", "url_env": "SINK_A", "channels": [f"#{NAME}"],
                              "kinds": ["watched", "mention"], "targets": ["claude"]}])
            slack["agents"] = {"claude": {"workspaces": {"team": ["bot"]}}}
            slack_options["http"] = httpx.AsyncClient(transport=httpx.MockTransport(hooks.handler))
        routines = {name: {"routine_id": f"trig_{name.replace('-', '')}", "project": project, "token_env": "ROUTINE_TOKEN", "primary": primary}
                    for name, project, primary in [("claude", None, False), ("docs-backup", "docs", False),
                                                   ("docs-main", "docs", True), ("workspace", "workspace", True)]}
        cfg = {"version": 2, "turn_cap": turn_cap, "blocks": {"mcp": {}, "slack": slack, "claude-routine": {"routines": routines}},
               "visibility": {"defaults": {"channel": shared, "target": "shared"}}}
        path = tmp_path / "config.yaml"

        def fire(request):
            fires.append((request.url.path, json.loads(request.content)["text"]))
            return httpx.Response(200, json={"claude_code_session_id": "session_example"})

        env = {**ENV, "SWITCHBOARD_KEY_CLAUDE": KEY_FULL, "ROUTINE_TOKEN": "example-token"}
        options = {"claude-routine": {"transport": httpx.MockTransport(fire)}}
        use_capy(capy, cfg, env, options)
        path.write_text(yaml.safe_dump(cfg))
        core = Switchboard(path, tmp_path / "data", env=env, slack_options=slack_options, block_options=options)
        for project in ("docs", "workspace", "no-routine"):
            core.projects.create(slug=project, name=project.title())
        with TestClient(create_app(core)) as client:
            client.portal.call(core.blocks["slack"].refresh)
            yield core, client, world, fires

    return build


def ingest(core, client, **changes):
    event = {"type": "message", "user": EXTERNAL, "channel": CHANNEL, "channel_type": "channel", "ts": TS, "text": "Can we fix this?"}
    return client.portal.call(core.slack.ingest, "team", {**event, **changes})


def handle_of(text):
    return text.split('reply(conversation="', 1)[1].split('"', 1)[0]


def posted(client, world, count):
    for _ in range(100):
        got = [kw for _, name, kw in world.calls if name == "chat_postMessage"]
        if len(got) >= count:
            return got
        client.portal.call(asyncio.sleep, 0.01)
    raise AssertionError(f"did not get {count} Slack posts")


def test_listen_config_rejects_unknown_workspace():
    with pytest.raises(ConfigError, match="listen_channels: unknown workspace"):
        parse_config({"listen_channels": {"unknown": ["#docs"]}}, ENV)


@pytest.mark.parametrize(("channels", "mapped", "project", "trigger"), [
    ([f"#{NAME}"], "docs", "docs", "/trig_docsmain/fire"),
    ([CHANNEL], "default", "workspace", "/trig_workspace/fire"),
])
def test_unaddressed_message_wakes_primary(opened, channels, mapped, project, trigger):
    with opened(channels=channels, mapped=mapped) as (core, client, world, fires):
        assert CHANNEL not in core.slack.runtime("team").watched_ids
        push = ingest(core, client)
        assert push["addressed_to_us"] is False
        (fire,) = fires
        assert fire[0].endswith(trigger)
        assert fire[1].splitlines()[0] == f"name-ustranger (external) in Slack #{NAME} asks: Can we fix this?"
        assert "<<<swb-untrusted\nCan we fix this?\nswb-untrusted>>>" in fire[1]
        assert "You may stay silent" in fire[1] and "to_owner=true" in fire[1] and "message the coordinator" not in fire[1]
        handle = core.inbox.handle(handle_of(fire[1]))
        assert handle["project"] == project
        assert {key: handle["meta"][key] for key in ("listen", "boundary", "slack_trust", "permalink")} == {
            "listen": True, "boundary": project, "slack_trust": "external", "permalink": push["permalink"],
        }
        assert not {"chat_postMessage", "chat_postEphemeral", "reactions_add"}.intersection(world.names())


def test_listen_wake_keeps_the_original_wrapper_and_the_optional_reply_tail(opened):
    with opened() as (core, client, world, fires):
        world.canned["users_info"] = lambda *_: {"user": {"name": "Dana"}}
        push = ingest(core, client, text="> Can we check the **canary**?")
        (_, text), = fires
        title = f"Dana (external) in Slack #{NAME} asks: Can we check the canary?"
        handle = core.inbox.handle(handle_of(text))
        assert handle["title"] == title and text.startswith(title + "\n\n")
        task, tail = text.split("\n\n---\n", 1)
        assert task == (
            f"{title}\n\n{push['text']}\n\n{push['permalink']}\n\n"
            "A reply is optional. If anything needs action, send only a short note to the owner."
        )
        assert tail.endswith("Don't use another handle for this message.")


@pytest.mark.parametrize(("options", "audit", "fires_general"), [
    ({"channels": ["#other"]}, False, True),
    ({"mapped": None}, True, True),
    ({"mapped": "missing"}, True, True),
    ({"shared": "private"}, True, False),
    ({"shared": ["UALLOWED"]}, True, False),
])
def test_inactive_listen_channels_keep_legacy_routing(opened, options, audit, fires_general):
    with opened(**options) as (core, client, world, fires):
        assert ingest(core, client) is None
        push = ingest(core, client, type="app_mention", text=f"<@{BOT_USER}> claude hello")
        assert any(row["action"] == "listen_ignored" for row in core.slack.store.recent_audit()) == audit
        if fires_general:
            (fire,) = fires
            assert fire[0].endswith("/trig_claude/fire")
            assert "listen-mode" not in fire[1]
            assert not core.inbox.handle(handle_of(fire[1]))["meta"].get("listen")
        else:
            assert not fires and push["context"]
            (ack,) = [kw for _, name, kw in world.calls if name == "chat_postEphemeral"]
            assert ack["text"] == "Noted."


def test_listen_bots_and_own_posts(opened):
    with opened() as (core, client, world, fires):
        ingest(core, client, bot_id="BUNKNOWN", type="app_mention", text=f"<@{BOT_USER}> claude hello")
        ingest(core, client, ts="1700000500.000003", is_bot=True)
        ingest(core, client, ts="1700000500.000004", user=BOT_USER, bot_id=BOT_ID)
        assert not fires
        ingest(core, client, ts="1700000500.000005", user="UBUILD001", bot_id="BKNOWN001")
        (fire,) = fires
        assert fire[1].splitlines()[0] == f"Build bot (known) in Slack #{NAME} asks: Can we fix this?"
        assert not {"chat_postMessage", "chat_postEphemeral"}.intersection(world.names())


def test_listen_rate_limits_addressed_messages_without_ack_or_context(opened):
    with opened(rate=1) as (core, client, world, fires):
        core.visibility.set("target:claude", "private", "test", core.clock())
        ingest(core, client, type="app_mention", text=f"<@{BOT_USER}> claude hello")
        ingest(core, client, type="app_mention", ts="1700000500.000003", text=f"<@{BOT_USER}> claude again")
        ingest(core, client, user="UANOTHER", ts="1700000500.000004")
        assert len(fires) == 2 and all(p.endswith("/trig_docsmain/fire") for p, _ in fires)
        assert any(row["action"] == "listen_rate_limited" for row in core.slack.store.recent_audit())
        assert len(payload(call(client, KEY_FULL, "inbox", {"since_cursor": 0}))["items"]) == 2
        assert not {"chat_postEphemeral", "reactions_add", "chat_postMessage"}.intersection(world.names())


@pytest.mark.parametrize(("event", "kind"), [("message", "watched"), ("app_mention", "mention")])
def test_listen_preserves_sink_and_granted_inbox_without_double_waking(opened, event, kind):
    hooks = Hooks()
    text = "Can we fix this?"
    original = f"<@{BOT_USER}> claude {text}" if event == "app_mention" else text
    with opened(hooks=hooks) as (core, client, world, fires):
        ingest(core, client, type=event, text=original)
        ingest(core, client, type=event, text=original)
        for _ in range(100):
            if hooks.requests:
                break
            client.portal.call(asyncio.sleep, 0.01)
        (delivered,) = hooks.bodies("/a")
        assert delivered["kind"] == kind and delivered["target"] == "claude"
        assert f"<<<swb-untrusted\n{text}\nswb-untrusted>>>" in delivered["text"]
        assert "_skip_delegate" not in delivered
        (fire,) = fires
        assert fire[0].endswith("/trig_docsmain/fire")
        assert len(core.slack.store.recent_events(["team"])) == 1
        items = payload(call(client, KEY_FULL, "inbox", {"since_cursor": 0}))["items"]
        assert sorted(i["kind"] for i in items) == ["message", "slack_event"]
        (item,) = [i for i in items if i["kind"] == "slack_event"]
        assert item["target"] == "claude" and item["text"] == delivered["text"]
        assert "chat_postEphemeral" not in world.names()


def test_listen_still_skips_the_ordinary_delegate_without_a_sink(opened):
    with opened() as (core, client, _, fires):
        ingest(core, client, type="app_mention", text=f"<@{BOT_USER}> claude hello")
        (fire,) = fires
        assert fire[0].endswith("/trig_docsmain/fire")


def test_owner_in_listening_channel_unchanged(opened):
    with opened() as (core, client, world, fires):
        assert ingest(core, client, user=OWNER_A) is None
        ingest(core, client, type="app_mention", user=OWNER_A, text=f"<@{BOT_USER}> claude hello")
        (fire,) = fires
        assert fire[0].endswith("/trig_docsmain/fire")
        assert "<<<swb-untrusted" not in fire[1] and "listen-mode" not in fire[1]
        assert not core.inbox.handle(handle_of(fire[1]))["meta"].get("listen")
        assert "reactions_add" in world.names()


def test_missing_primary_does_not_fall_back_to_general(opened):
    with opened(mapped="no-routine") as (core, client, world, fires):
        ingest(core, client, type="app_mention", text=f"<@{BOT_USER}> claude hello")
        assert not fires and "chat_postEphemeral" not in world.names()
        assert any(row["detail"].get("reason") == "no primary routine" for row in core.slack.store.recent_audit())


@pytest.mark.parametrize("thread", [None, THREAD])
def test_listen_reply_stays_in_origin_thread_with_target_persona(opened, thread):
    with opened() as (core, client, world, fires):
        for slug, target in core.slack.targets.items():
            if target.id == "docs-main":
                core.slack.targets[slug] = replace(target, persona="doorman")
        core.visibility.set("target:docs-main", "private", "test", core.clock())
        ingest(core, client, thread_ts=thread)
        handle = handle_of(fires[0][1])
        res = payload(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "x" * 2000}))
        assert res["in_reply_to"] == handle
        (post,) = posted(client, world, 1)
        assert post["channel"] == CHANNEL and post["thread_ts"] == (thread or TS)
        assert post["text"] == "x" * 1499 + "…" and post["username"] == "Doc doorman"
        assert "conversations_open" not in world.names()


def test_listen_reply_needing_slack_approval_is_withheld_without_dm(opened):
    with opened(write="approval") as (core, client, world, fires):
        ingest(core, client)
        handle = handle_of(fires[0][1])
        payload(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "no fallback"}))
        for _ in range(100):
            if any(row["action"] == "reply_withheld" for row in core.slack.store.recent_audit()):
                break
            client.portal.call(asyncio.sleep, 0.01)
        else:
            pytest.fail("listen reply was not withheld")
        assert not {"conversations_open", "chat_postMessage"}.intersection(world.names())
        assert core.slack.store.list_requests(status="pending") == []


def test_listen_can_note_owner_without_posting_to_thread(opened):
    with opened() as (core, client, world, fires):
        push = ingest(core, client, thread_ts=THREAD)
        handle = handle_of(fires[0][1])
        reply = payload(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "x" * 700, "to_owner": True}))
        assert reply["delivered"] and reply["to_owner"] and reply["conversation"] == handle
        (post,) = posted(client, world, 1)
        assert post == {"channel": "DAPPROVER", "text": f"Note from docs about name-ustranger in #{NAME}: " + "x" * 500 + f"\n{push['permalink']}"}
        assert core.inbox.handle(handle)["status"] == "answered"


def test_cancelled_listen_conversation_cannot_note_owner(opened):
    with opened() as (core, client, world, fires):
        ingest(core, client, thread_ts=THREAD)
        handle = handle_of(fires[0][1])
        args = {"conversation": handle}
        sender = core.blocks["slack"].principal
        cancel = client.portal.call(core.verb, sender, "cancel", handle)
        assert cancel["status"] == "cancelled"
        reply = payload(call(client, KEY_FULL, "reply", {**args, "text": "Check the report", "to_owner": True}))
        assert reply["status"] == "cancelled" and not reply["delivered"]
        assert core.inbox.handle(handle)["status"] == "cancelled"
        assert not {"conversations_open", "chat_postMessage"}.intersection(world.names())
        assert not core.audit.recent(action="listen_note")


def test_owner_note_in_flight_cannot_reopen_cancelled_listen_conversation(opened, monkeypatch):
    with opened() as (core, client, _, fires):
        ingest(core, client)
        handle = handle_of(fires[0][1])
        sender = core.blocks["slack"].principal

        async def cancel_during_dm(_text):
            assert (await core.verb(sender, "cancel", handle))["status"] == "cancelled"
            return True

        monkeypatch.setattr(core.slack, "dm_owner", cancel_during_dm)
        reply = payload(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "Check the report", "to_owner": True}))
        assert reply["delivered"]
        assert core.inbox.handle(handle)["status"] == "cancelled"


def test_to_owner_refused_for_non_listen_conversation(opened):
    with opened() as (core, client, world, fires):
        ingest(core, client, type="app_mention", user=OWNER_A, text=f"<@{BOT_USER}> claude hello")
        handle = handle_of(fires[0][1])
        assert "only allowed" in error_text(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "no", "to_owner": True}))
        assert "chat_postMessage" not in world.names()


def test_listen_handle_cannot_be_reused_to_send_or_forward(opened):
    with opened() as (core, client, _, fires):
        ingest(core, client)
        handle = handle_of(fires[0][1])
        assert "cannot be forwarded" in error_text(call(client, KEY_FULL, "send", {"task": "handoff", "to": "agent:chatgpt", "forward": handle}))
        assert "use reply" in error_text(call(client, KEY_FULL, "send", {"task": "handoff", "to": "agent:chatgpt", "in_reply_to": handle}))
        for field in ("in_reply_to", "forward"):
            assert "use reply" in error_text(call(client, KEY_FULL, "agent_send", {"to": "chatgpt", "text": "handoff", field: handle}))
        principal = core.auth.verify_bearer(KEY_FULL)
        for to in ("chatgpt", "slack"):
            with pytest.raises(CoreError, match="only be answered with reply"):
                client.portal.call(partial(core.agent_send, principal, to=to, text="handoff", in_reply_to=handle))
        reply = payload(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "answer"}))
        with pytest.raises(CoreError, match="only be answered with reply"):
            client.portal.call(partial(core.agent_send, principal, to="chatgpt", text="reuse", in_reply_to=reply["handle"]))
        assert core.inbox.handles(owner="chatgpt") == []


def test_listen_thread_reply_and_owner_note_share_turn_cap(opened):
    with opened(turn_cap=3) as (core, client, world, fires):
        ingest(core, client)
        handle = handle_of(fires[0][1])
        payload(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "one"}))
        payload(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "two", "to_owner": True}))
        rows = core.store.q("SELECT root, turns FROM conversation_turns")
        assert [(row["root"], row["turns"]) for row in rows] == [(handle, 3)]
        error = error_text(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "never delivered"}))
        assert f"{handle} is paused" in error
        assert all(p["text"] != "never delivered" for p in posted(client, world, 3))


def test_poll_resolves_listen_names_without_watching_owner_messages(opened):
    with opened(channels=["#agents"], intake="poll") as (core, client, world, fires):
        rt = core.slack.runtime("team")
        assert rt.listen_ids == {"CWATCH001"} and not rt.watched_ids
        core.slack.store.set_cursor("team", "CWATCH001", "1700000000.1")
        world.history["CWATCH001"] = [{"user": OWNER_A, "ts": THREAD, "text": "Owner"}, {"user": EXTERNAL, "ts": TS, "text": "Guest"}]
        assert client.portal.call(rt.poll_channel, "CWATCH001") == 2
        (fire,) = fires
        assert "Guest" in fire[1] and "Owner" not in fire[1]
