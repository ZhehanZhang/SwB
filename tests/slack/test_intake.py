"""Recorded Socket Mode envelopes through the full intake path (fake Slack, mock webhook)."""

from slackfix import OWNER_B, drain, payload
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)
from switchboard.trust import CLOSE, OPEN


def inner(text):
    return text.split(OPEN + "\n", 1)[1].split("\n" + CLOSE, 1)[0]


async def feed(core, label, name):
    p = payload(name)
    await core.handle_socket(label, p["type"], p["payload"])


async def test_workspace_a_intake(make_core, hooks, world):
    core = await make_core()
    for name in [
        "a_app_mention.json",
        "a_watched_message.json",
        "a_dm.json",
        "a_other_agent_bot.json",
        "a_own_bot_message.json",
        "a_message_changed.json",
        "a_unwatched_message.json",
        "a_wrong_team.json",
    ]:
        await feed(core, "a2a", name)
    await drain(core)
    bodies = hooks.bodies("/a")
    assert [(b["kind"], b["channel"]) for b in bodies] == [
        ("mention", "CPUBLIC01"),
        ("watched", "CWATCH001"),
        ("dm", "DDM000001"),
        ("watched", "CWATCH001"),
    ]
    watched = bodies[1]
    assert watched["type"] == "slackgate.event"
    assert watched["thread_ts"] == "1700000150.000150"
    assert watched["user_name"] == "name-upeer0002"
    assert watched["permalink"].startswith("https://a.slack.com/archives/CWATCH001/p1700000200000200")
    assert hooks.requests[0].headers["authorization"] == "Bearer sink-secret"
    assert hooks.requests[0].headers["idempotency-key"] == "slackgate:a2a:TAAAA1111:CPUBLIC01:1700000100.000100"
    reactions = [kw for t, n, kw in world.calls if n == "reactions_add"]
    assert reactions == [
        {"channel": "CPUBLIC01", "timestamp": "1700000100.000100", "name": "eyes"},
        {"channel": "DDM000001", "timestamp": "1700000300.000300", "name": "eyes"},
    ]
    assert all(t == "xoxb-a" for t, n, _ in world.calls if n == "reactions_add")
    assert {e["kind"] for e in core.store.recent_events(["a2a"])} == {"mention", "watched", "dm"}


async def test_workspace_b_user_events(make_core, hooks, world):
    core = await make_core()
    await feed(core, "private", "b_user_event_watched.json")
    await feed(core, "private", "b_user_event_unwatched.json")
    await feed(core, "private", "b_dm_to_owner.json")
    await drain(core)
    bodies = hooks.bodies("/b")
    assert [(b["kind"], b["channel"], inner(b["text"]), b["trust"]) for b in bodies] == [("watched", "CPRIV0001", "dinner at 7?", "external")]
    assert not [n for t, n, _ in world.calls if t == "xoxp-b" and n == "reactions_add"]


async def test_own_user_post_not_pushed(make_core, hooks, world):
    core = await make_core()
    core.store.mark_sent("TBBBB2222", "CPRIV0001", "1700001000.001000")
    p = payload("b_user_event_watched.json")
    p["payload"]["event"]["user"] = OWNER_B
    await core.handle_socket("private", p["type"], p["payload"])
    await drain(core)
    assert hooks.bodies("/b") == []


async def test_poll_fallback_uses_cursor(make_core, hooks, world):
    core = await make_core()
    rt = core.runtimes["private"]
    assert await rt.poll_channel("CPRIV0001") == 0
    cursor = core.store.get_cursor("private", "CPRIV0001")
    world.history["CPRIV0001"] = [
        {"type": "message", "user": "UFAMILY01", "text": "second", "ts": "1700000002.000002"},
        {"type": "message", "user": "UFAMILY01", "text": "first", "ts": "1700000001.000001"},
    ]
    assert await rt.poll_channel("CPRIV0001") == 2
    await drain(core)
    assert [inner(b["text"]) for b in hooks.bodies("/b")] == ["first", "second"]
    assert core.store.get_cursor("private", "CPRIV0001") == "1700000002.000002"
    hist = [kw for t, n, kw in world.calls if n == "conversations_history"]
    assert hist[0]["oldest"] == cursor and all(t == "xoxp-b" for t, n, _ in world.calls if n == "conversations_history")
