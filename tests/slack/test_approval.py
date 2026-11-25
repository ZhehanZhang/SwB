import pytest

from slackfix import OWNER_A, payload
from switchboard.slack.writes import Actor, WriteError
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)


def key(core, kid="full"):
    return core.config.grant(kid)


async def submit_b(core, text="hello family"):
    return await core.gate.submit(key(core), "private", "user", "post_message", {"channel": "CPRIV0001", "text": text, "thread_ts": None})


async def test_user_only_write_is_queued_and_owner_notified(make_core, world):
    core = await make_core()
    res = await submit_b(core)
    assert res["status"] == "pending_approval"
    assert not [n for t, n, _ in world.calls if t == "xoxp-b" and n == "chat_postMessage"]
    notice = [(t, kw) for t, n, kw in world.calls if n == "chat_postMessage"]
    assert notice[0][0] == "xoxb-a" and notice[0][1]["channel"] == "DAPPROVER"
    assert "hello family" in str(notice[0][1]["blocks"])
    req = core.store.get_request(res["request_id"])
    assert req["status"] == "pending" and req["notice"]["channel"] == "DAPPROVER"


@pytest.mark.parametrize("actor", [Actor("agent", "full"), Actor("key", "full"), Actor("mcp", "x")])
async def test_agent_cannot_approve(make_core, world, actor):
    core = await make_core()
    rid = (await submit_b(core))["request_id"]
    with pytest.raises(WriteError, match="only by the owner"):
        await core.gate.decide(rid, True, actor)
    assert core.store.get_request(rid)["status"] == "pending"
    assert any(a["action"] == "decision_refused" for a in core.store.recent_audit())


async def test_slack_click_from_non_approver_refused(make_core):
    core = await make_core()
    rid = (await submit_b(core))["request_id"]
    with pytest.raises(WriteError, match="approver"):
        await core.gate.decide(rid, True, Actor("slack", "USOMEONE1"))
    assert core.store.get_request(rid)["status"] == "pending"


async def test_owner_approves_via_slack_button(make_core, world, hooks):
    core = await make_core()
    rid = (await submit_b(core, "on my way"))["request_id"]
    p = payload("a_block_actions_approve.json")
    p["payload"]["actions"][0]["value"] = rid
    await core.handle_socket("a2a", p["type"], p["payload"])
    req = core.store.get_request(rid)
    assert req["status"] == "sent" and req["decided_by"] == f"slack:{OWNER_A}"
    sent = [kw for t, n, kw in world.calls if t == "xoxp-b" and n == "chat_postMessage"]
    assert sent == [{"channel": "CPRIV0001", "text": "on my way"}]
    assert any(n == "chat_update" for t, n, _ in world.calls)
    actions = [a["action"] for a in core.store.recent_audit()]
    assert {"write_requested", "write_approved", "write_sent"} <= set(actions)
    with pytest.raises(WriteError, match="already sent"):
        await core.gate.decide(rid, True, Actor("admin", "owner"))


async def test_button_in_wrong_workspace_ignored(make_core, world):
    core = await make_core()
    rid = (await submit_b(core))["request_id"]
    p = payload("a_block_actions_approve.json")
    p["payload"]["actions"][0]["value"] = rid
    p["payload"]["team"]["id"] = "TBBBB2222"
    await core.handle_socket("private", p["type"], p["payload"])
    assert core.store.get_request(rid)["status"] == "pending"


async def test_reject(make_core, world):
    core = await make_core()
    rid = (await submit_b(core))["request_id"]
    req = await core.gate.decide(rid, False, Actor("admin", "owner@example.com"))
    assert req["status"] == "rejected"
    assert not [n for t, n, _ in world.calls if t == "xoxp-b" and n == "chat_postMessage"]


async def test_expiry(make_core, clock, world):
    core = await make_core()
    rid = (await submit_b(core))["request_id"]
    clock.t += 10 * 60 + 1
    with pytest.raises(WriteError, match="already expired"):
        await core.gate.decide(rid, True, Actor("admin", "owner"))
    assert core.store.get_request(rid)["status"] == "expired"
    assert not [n for t, n, _ in world.calls if t == "xoxp-b" and n == "chat_postMessage"]
    assert any(a["action"] == "write_expired" for a in core.store.recent_audit())


async def test_sweep_expires(make_core, clock):
    core = await make_core()
    rid = (await submit_b(core))["request_id"]
    clock.t += 3600
    expired = await core.gate.sweep()
    assert [r["id"] for r in expired] == [rid]


async def test_free_bot_post_sends_now_and_request_write_queues(make_core, world):
    core = await make_core()
    res = await core.gate.submit(key(core), "a2a", "bot", "post_message", {"channel": "CWATCH001", "text": "hi", "thread_ts": "1.2"})
    assert res["status"] == "sent" and res["permalink"].startswith("https://a.slack.com/archives/CWATCH001/")
    queued = await core.gate.submit(key(core), "a2a", "bot", "post_message", {"channel": "CWATCH001", "text": "check me"}, force_approval=True)
    assert queued["status"] == "pending_approval"
    assert core.store.is_sent("TAAAA1111", "CWATCH001", res["ts"])


async def test_never_policy(make_core):
    from slackfix import base_config

    cfg = base_config()
    cfg["workspaces"]["a2a"]["identities"]["user"]["write"] = "never"
    core = await make_core(cfg)
    with pytest.raises(WriteError, match="policy: never"):
        await core.gate.submit(key(core), "a2a", "user", "post_message", {"channel": "C", "text": "x"})


async def test_key_without_identity_cannot_write(make_core):
    core = await make_core()
    with pytest.raises(WriteError, match="may not use"):
        await core.gate.submit(key(core, "fixed"), "private", "user", "post_message", {"channel": "C", "text": "x"})
