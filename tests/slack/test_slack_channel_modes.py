from functools import partial

import pytest
from conftest import CAPTAIN, Clock, call, error_text, payload
from slackfix import BOT_USER, KEY_FULL, OWNER_A, Hooks
from test_listen import CHANNEL, EXTERNAL, THREAD, TS, handle_of, ingest, posted
from test_listen import opened as listen_fixture

from switchboard.core import CoreError

opened = listen_fixture


def set_mode(core, client, mode, *, actions=None):
    principal = core.auth.verify_bearer(KEY_FULL)
    return client.portal.call(partial(core.modes.set, principal, "slack", "team", mode, CHANNEL, actions=actions))


def test_slack_off_drops_guest_but_keeps_owner_mention(opened):
    with opened(channels=[]) as (core, client, world, fires):
        assert set_mode(core, client, "off")["mode"] == "off"
        mention = {"type": "app_mention", "text": f"<@{BOT_USER}> claude status"}
        assert ingest(core, client, **mention) is None
        assert not fires and not core.slack.store.recent_events(["team"])
        assert ingest(core, client, **mention, user=OWNER_A)["trust"] == "owner"
        (fire,) = fires
        handle = handle_of(fire[1])
        assert core.inbox.handle(handle)["meta"]["channel_mode"]["mode"] == "work"
        principal = core.auth.verify_bearer(KEY_FULL)
        client.portal.call(partial(core.verb, principal, "reply", handle, text="done"))
        (post,) = posted(client, world, 1)
        assert post["channel"] == CHANNEL and post["thread_ts"] == TS


@pytest.mark.parametrize(("mode", "mention", "is_bot", "reason"), [
    ("off", True, False, "channel mode off"),
    ("assist", False, False, "assist needs a target"),
    ("triage", False, False, "triage needs an agent"),
    ("assist", False, True, "external bot message"),
])
def test_slack_mode_drops_are_counted_and_throttled(opened, monkeypatch, mode, mention, is_bot, reason):
    with opened(channels=[], mapped="no-routine") as (core, client, _, fires):
        clock = Clock()
        monkeypatch.setattr(core, "clock", clock)
        set_mode(core, client, mode)
        attrs = {"type": "app_mention", "text": f"<@{BOT_USER}> private message body"} if mention else {"text": "Private message body"}
        for ts in (TS, "1700000500.000003"):
            assert ingest(core, client, ts=ts, is_bot=is_bot, **attrs) is None
        [row] = core.audit.recent(action="chat_event", outcome="dropped")
        assert (row["origin"], row["source"], row["target"], row["detail"], row["ref"], row["excerpt"]) == (
            "slack", "slack", f"team/{CHANNEL}", reason, None, "",
        )
        status = core.blocks["slack"].status()
        assert status["dropped"] == 2 and status["drop_reasons"] == {reason: 2}
        clock.t += 600
        assert ingest(core, client, ts="1700000500.000004", is_bot=is_bot, **attrs) is None
        assert core.blocks["slack"].status()["drop_reasons"] == {reason: 3}
        assert len(core.audit.recent(action="chat_event", outcome="dropped")) == 2
        assert not fires


def test_slack_ordinary_watch_chatter_has_no_drop_audit(opened):
    with opened(channels=[]) as (core, client, _, _):
        set_mode(core, client, "watch")
        assert ingest(core, client)["trust"] == "external"
        assert ingest(core, client, ts="1700000500.000003", is_bot=True) is None
        assert not core.audit.recent(action="chat_event")
        assert core.blocks["slack"].status()["drop_reasons"] == {}


def test_slack_legacy_mention_without_a_destination_is_a_drop(opened):
    with opened(channels=[], mapped=None, workspace_project=None) as (core, client, _, fires):
        for ts in (TS, "1700000500.000003"):
            ingest(core, client, ts=ts, type="app_mention", text=f"<@{BOT_USER}> Check this report", user=OWNER_A)
        [row] = core.audit.recent(action="chat_event", outcome="dropped")
        assert row["detail"] == "work needs a target"
        assert core.blocks["slack"].status()["drop_reasons"] == {"work needs a target": 2}
        assert not fires


def test_slack_legacy_context_limit_counts_drops_without_delivering(opened):
    with opened(channels=[], rate=1) as (core, client, world, fires):
        pair = core.connections.mint(agent="alpha", project=None, client=None, by="owner")
        core.connections.redeem(pair["code"])
        client.portal.call(core.blocks["slack"].refresh)
        core.visibility.set("target:alpha", "private", "test", core.clock())
        attrs = {"type": "app_mention", "text": f"<@{BOT_USER}> alpha Check the report"}
        for ts in (TS, "1700000500.000003", "1700000500.000004"):
            ingest(core, client, ts=ts, **attrs)
        assert core.blocks["slack"].status()["drop_reasons"] == {"target access restricted": 1, "sender rate limit": 2}
        rows = core.audit.recent(action="chat_event", outcome="dropped")
        assert [row["detail"] for row in rows] == ["sender rate limit", "target access restricted"]
        assert [item["kind"] for item in core.inbox.read(owner="alpha")["items"]] == ["context"]
        assert world.names().count("chat_postEphemeral") == 1 and not fires


def test_slack_watch_deduplicates_and_never_fans_out(opened):
    with opened(hooks=Hooks()) as (core, client, world, fires):
        set_mode(core, client, "watch")
        mention = {"type": "app_mention", "text": f"<@{BOT_USER}> claude status"}
        assert ingest(core, client, **mention)["trust"] == "external"
        assert ingest(core, client, **mention) is None
        rows = core.store.q("SELECT channel, author_id FROM watch_events WHERE block='slack'")
        assert [(r["channel"], r["author_id"]) for r in rows] == [(CHANNEL, EXTERNAL)]
        assert not fires and not core.inbox.handles(owner="claude")
        assert not core.slack.store.recent_events(["team"])
        assert not {"chat_postMessage", "chat_postEphemeral", "reactions_add"}.intersection(world.names())


def test_slack_assist_can_wake_configured_target_without_project(opened):
    with opened(channels=[], mapped=None, workspace_project=None, hooks=Hooks()) as (core, client, _, fires):
        set_mode(core, client, "assist")
        push = ingest(core, client)
        (url, prompt), = fires
        assert url.endswith("/trig_claude/fire")
        assert push["channel_mode"]["project"] is None
        assert core.inbox.handle(handle_of(prompt))["project"] is None


@pytest.mark.parametrize("mode", ["assist", "triage", "work"])
def test_slack_modes_use_project_mailbox_route(opened, mode):
    with opened(channels=[]) as (core, client, _, fires):
        pair = core.connections.mint(agent="alpha", project="docs", client=None, by="owner")
        core.connections.redeem(pair["code"])
        core.routes.set(destination="agent", agent=None, project="docs", target={"agent": "alpha"},
                        defaults=None, allow_override=False, note="", by="owner")
        assert set_mode(core, client, mode)["target"] == "agent:alpha"
        if mode == "work":
            ingest(core, client, type="app_mention", text=f"<@{BOT_USER}> status", user=OWNER_A)
        else:
            ingest(core, client)
        [wake] = core.inbox.read(owner="alpha")["items"]
        assert wake["project"] == "docs" and wake["from"]["trust"] == ("owner" if mode == "work" else "external")
        assert not fires


@pytest.mark.parametrize("mode", ["assist", "triage", "work"])
def test_slack_modes_dispatch_to_locked_capy_route(opened, capy, mode):
    with opened(channels=[], mapped="no-routine", capy=capy) as (core, client, _, fires):
        core.routes.set(destination="capy", agent=None, project="no-routine", target={"captain": CAPTAIN},
                        defaults={"delivery": "steer"}, allow_override=False, note="", by="owner")
        assert set_mode(core, client, mode)["target"] == f"capy:captain:{CAPTAIN}"
        if mode == "work":
            push = ingest(core, client, type="app_mention", text=f"<@{BOT_USER}> Check the report", user=OWNER_A)
        else:
            push = ingest(core, client)
        assert push["channel_mode"]["target"] == f"capy:captain:{CAPTAIN}"
        [sent] = [body for method, path, body in capy.requests if method == "POST" and path == f"/api/v1/threads/{CAPTAIN}/message"]
        assert sent["delivery"] == "steer" and ("Check the report" if mode == "work" else "Can we fix this?") in sent["text"]
        [handle] = core.inbox.handles(owner="slack", destination="capy")
        assert handle["meta"]["route"] == "project" and handle["meta"]["channel_mode"]["mode"] == mode
        assert not fires


def test_slack_assist_uses_actual_target_visibility_and_thread(opened):
    with opened(channels=[]) as (core, client, world, fires):
        set_mode(core, client, "assist")
        core.visibility.set("target:docs-main", "private", "test", core.clock())
        assert ingest(core, client, thread_ts=THREAD) is None
        assert not fires
        core.visibility.set("target:docs-main", "shared", "test", core.clock())
        push = ingest(core, client, thread_ts=THREAD)
        (url, prompt), = fires
        assert url.endswith("/trig_docsmain/fire")
        assert "Assist only" in prompt and "<<<swb-untrusted" in prompt
        assert prompt.splitlines()[0] == "name-ustranger (external) in Slack #chan-cpublic01 asks: Can we fix this?"
        assert prompt.index("swb-untrusted>>>") < prompt.index(push["permalink"]) < prompt.index("Assist only")
        handle = handle_of(prompt)
        assert core.inbox.handle(handle)["meta"]["channel_mode"]["permalink"] == push["permalink"]
        assert "requires its conversation handle" in error_text(call(client, KEY_FULL, "slack_post", {
            "workspace": "team", "channel": CHANNEL, "text": "public write",
        }))
        assert payload(call(client, KEY_FULL, "reply", {"conversation": handle, "text": "x" * 2000}))["in_reply_to"] == handle
        client.portal.call(core.blocks["slack"].relay, {"kind": "final", "handle": handle, "text": "y" * 2000})
        for post in posted(client, world, 2):
            assert post["channel"] == CHANNEL and post["thread_ts"] == THREAD
            assert len(post["text"]) == core.modes.assist_max_chars
        assert "conversations_open" not in world.names()


def test_slack_triage_relay_does_not_bypass_reply_action(opened):
    with opened(channels=[]) as (core, client, world, fires):
        set_mode(core, client, "triage", actions=["notify_owner"])
        push = ingest(core, client)
        (_, prompt), = fires
        assert "Allowed actions: notify_owner" in prompt and push["channel_mode"]["actions"] == ["notify_owner"]
        handle = handle_of(prompt)
        principal = core.auth.verify_bearer(KEY_FULL)
        with pytest.raises(CoreError, match="triage conversations cannot reply"):
            client.portal.call(partial(core.verb, principal, "reply", handle, text="not allowed"))
        client.portal.call(core.blocks["slack"].relay, {"kind": "final", "handle": handle, "text": "also not allowed"})
        assert not {"chat_postMessage", "conversations_open"}.intersection(world.names())


def test_slack_direct_messages_are_not_workspace_fanout(opened):
    hooks = Hooks()
    with opened(hooks=hooks) as (core, client, _, fires):
        dm = {"channel": "DDIRECT01", "channel_type": "im", "text": "claude status"}
        assert ingest(core, client, **dm)["kind"] == "dm"
        assert not fires
        assert core.store.one("SELECT dm FROM watch_events WHERE channel='DDIRECT01'")["dm"] == 1
        ingest(core, client, **dm, user=OWNER_A, ts="1700000500.000003")
        (fire,) = fires
        assert "claude status" not in fire[1] and "status" in fire[1]
        assert not any(h["destination"] == "slack" for h in core.inbox.handles(owner="claude"))
        assert not core.slack.store.recent_events(["team"]) and not hooks.requests


def test_slack_poll_uses_current_channel_override(opened):
    with opened(channels=[], intake="poll") as (core, client, world, fires):
        rt = core.slack.runtime("team")
        assert core.blocks["slack"].mode_poll_channels(rt) == set()
        set_mode(core, client, "watch")
        assert core.blocks["slack"].mode_poll_channels(rt) == {CHANNEL}
        core.slack.store.set_cursor("team", CHANNEL, THREAD)
        world.history[CHANNEL] = [{"user": EXTERNAL, "ts": TS, "text": "watched"}]
        assert client.portal.call(rt.poll_channel, CHANNEL) == 1
        assert not fires and core.store.one("SELECT channel FROM watch_events")["channel"] == CHANNEL
        set_mode(core, client, "off")
        assert core.blocks["slack"].mode_poll_channels(rt) == set()
