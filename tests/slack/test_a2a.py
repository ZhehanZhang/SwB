"""Workspace A as an agent-to-agent room: other agents' posts reach us, ours never loop back."""

import pytest

from slackfix import BOT_ID, BOT_USER, base_config, drain
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)


def a2a_config(**ws):
    cfg = base_config()
    cfg["workspaces"]["a2a"].update({"app_id": "AOURS0001", **ws})
    return cfg


def msg(ts, **fields):
    event = {"type": "message", "channel_type": "channel", "channel": "CWATCH001", "ts": ts, "text": "hi", **fields}
    return {"team_id": "TAAAA1111", "event": event}


async def test_agent_posts_pushed_own_posts_dropped(make_core, hooks):
    core = await make_core(a2a_config())
    events = [
        msg("1700000001.000001", subtype="bot_message", bot_id="BMAIN0001", username="main-agent", app_id="AMAIN0001", text="status update"),
        msg("1700000002.000002", bot_id="BMAIN0001", user="UMAIN0001", app_id="AMAIN0001", text=f"<@{BOT_USER}> can you check?"),
        msg("1700000003.000003", bot_id="BMAIN0001", user="UMAIN0001", thread_ts="1700000000.000009", parent_user_id=BOT_USER, text="done"),
        msg("1700000004.000004", user="UPEER0001", text="general chatter"),
        msg("1700000005.000005", user=BOT_USER, bot_id=BOT_ID, text="our own post"),
        msg("1700000006.000006", subtype="bot_message", bot_id=BOT_ID, username="Capy", text="our persona post"),
        msg("1700000007.000007", subtype="bot_message", bot_id="BOTHERAPP", bot_profile={"app_id": "AOURS0001"}, text="our app, other bot id"),
        msg("1700000008.000008", subtype="bot_message", bot_id="BMAIN0001", app_id="AOURS0001", text="our app id"),
    ]
    for p in events:
        await core.handle_socket("a2a", "events_api", p)
    await drain(core)
    got = [(b["ts"], b["kind"], b["is_bot"], b["addressed_to_us"]) for b in hooks.bodies("/a")]
    assert got == [
        ("1700000001.000001", "watched", True, False),
        ("1700000002.000002", "mention", True, True),
        ("1700000003.000003", "watched", True, True),
        ("1700000004.000004", "watched", False, False),
    ]


async def test_reply_in_thread_we_started_is_addressed(make_core, hooks):
    core = await make_core(a2a_config())
    core.store.mark_sent("TAAAA1111", "CWATCH001", "1700000100.000100")
    await core.handle_socket("a2a", "events_api", msg("1700000101.000101", bot_id="BMAIN0001", user="UMAIN0001", thread_ts="1700000100.000100", parent_user_id="UOWNERA01"))
    await core.handle_socket("a2a", "events_api", msg("1700000102.000102", user="UPEER0001", thread_ts="1700000050.000050", parent_user_id="UPEER0001"))
    await drain(core)
    assert [b["addressed_to_us"] for b in hooks.bodies("/a")] == [True, False]


async def test_bot_thread_loop_guard(make_core):
    core = await make_core(a2a_config(bot_thread_posts_per_hour=3))
    key = core.config.grant("full")
    root = await core.gate.submit(key, "a2a", "bot", "post_message", {"channel": "CWATCH001", "text": "start", "thread_ts": None})
    assert root["status"] == "sent"
    thread = {"channel": "CWATCH001", "text": "reply", "thread_ts": root["ts"]}
    assert [(await core.gate.submit(key, "a2a", "bot", "post_message", thread))["status"] for _ in range(2)] == ["sent", "sent"]
    held = await core.gate.submit(key, "a2a", "bot", "post_message", thread)
    assert held["status"] == "pending_approval" and "loop guard" in held["reason"]
    other = await core.gate.submit(key, "a2a", "bot", "post_message", {**thread, "channel": "CPUBLIC01"})
    assert other["status"] == "sent"
    core.clock.t += 3601
    assert (await core.gate.submit(key, "a2a", "bot", "post_message", thread))["status"] == "sent"
    assert any(a["action"] == "loop_guard" for a in core.store.recent_audit())


def test_a2a_config_validation():
    from slackfix import ENV
    from switchboard.slack.config import ConfigError, parse_config

    cfg = parse_config(a2a_config(), ENV)
    assert cfg.workspaces["a2a"].app_id == "AOURS0001" and cfg.workspaces["a2a"].bot_thread_posts_per_hour == 20
    with pytest.raises(ConfigError, match="app_id"):
        parse_config(a2a_config(app_id="nope"), ENV)
    with pytest.raises(ConfigError, match="bot_thread_posts_per_hour"):
        parse_config(a2a_config(bot_thread_posts_per_hour=0), ENV)
