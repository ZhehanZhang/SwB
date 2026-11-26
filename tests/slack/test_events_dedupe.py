import pytest

from slackfix import drain, payload
from switchboard.slack.events import ClassifyContext, classify, render_payload
from switchboard.slack.store import Store
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)

CTX = ClassifyContext(has_bot=True, bot_user_id="UBOT", bot_id="BBOT", watched=frozenset({"CW"}))


@pytest.mark.parametrize(
    "event, kind",
    [
        ({"type": "app_mention", "user": "U1", "channel": "CX", "ts": "1"}, "mention"),
        ({"type": "message", "user": "U1", "channel": "CX", "ts": "1", "text": "hey <@UBOT>"}, "mention"),
        ({"type": "message", "user": "U1", "channel": "DX", "ts": "1", "channel_type": "im"}, "dm"),
        ({"type": "message", "user": "U1", "channel": "CW", "ts": "1"}, "watched"),
        ({"type": "message", "subtype": "bot_message", "bot_id": "BOTHER", "channel": "CW", "ts": "1"}, "watched"),
        ({"type": "message", "user": "U1", "channel": "CX", "ts": "1"}, None),
        ({"type": "message", "user": "UBOT", "channel": "CW", "ts": "1"}, None),
        ({"type": "message", "bot_id": "BBOT", "channel": "CW", "ts": "1"}, None),
        ({"type": "message", "subtype": "message_changed", "channel": "CW", "ts": "1"}, None),
        ({"type": "message", "subtype": "channel_join", "user": "U1", "channel": "CW", "ts": "1"}, None),
        ({"type": "reaction_added", "user": "U1"}, None),
    ],
)
def test_classify(event, kind):
    assert classify(event, CTX) == kind


def test_store_dedupes_on_team_channel_ts(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    ev = {"id": "T:C:1.0", "workspace": "a", "team": "T", "channel": "C", "ts": "1.0", "kind": "watched"}
    assert store.add_event(ev) is True
    assert store.add_event({**ev, "kind": "mention"}) is False
    assert store.add_event({**ev, "id": "T:C:2.0", "ts": "2.0"}) is True
    assert sorted(e["ts"] for e in store.recent_events(["a"])) == ["1.0", "2.0"]


async def test_mention_and_message_for_same_ts_push_once(make_core, hooks):
    core = await make_core()
    first = await core.handle_socket("a2a", **_unpack(payload("a_app_mention.json")))
    await core.handle_socket("a2a", **_unpack(payload("a_message_mention_same_ts.json")))
    await core.handle_socket("a2a", **_unpack(payload("a_app_mention.json")))
    await drain(core)
    assert first is None
    bodies = hooks.bodies("/a")
    assert len(bodies) == 1 and bodies[0]["kind"] == "mention"


def _unpack(p):
    return {"req_type": p["type"], "payload": p["payload"]}


def test_render_single_and_batch():
    e1 = {"id": "1", "workspace": "a", "kind": "watched"}
    e2 = {"id": "2", "workspace": "a", "kind": "mention"}
    assert render_payload([e1], batched=False) == {"type": "slackgate.event", **e1}
    batch = render_payload([e1, e2], batched=True)
    assert batch["type"] == "slackgate.batch" and batch["count"] == 2 and batch["events"] == [e1, e2]
