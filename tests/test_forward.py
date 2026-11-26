import asyncio

import pytest

from conftest import CAPTAIN, SECOND_KEY, STATIC_KEY, acall, base_config, error_text, eventually, payload, principal, infra
from switchboard.core import CoreError


async def test_forward_uses_original_root_and_turn_cap(amake):
    core, c = await amake(config=base_config(turn_cap=4))
    root = (await core.send(principal("capy"), to="agent:second", task="Check build"))["handle"]
    interim = (await core.verb(principal("second"), "reply", root, text="Checking", final=False))["handle"]
    args = {"task": "Check build", "to": "agent:claude", "forward": interim}
    assert "can only forward a conversation you received" in error_text(await acall(c, SECOND_KEY, "send", args))
    out = payload(await acall(c, STATIC_KEY, "send", args))
    handle = out["handle"]
    meta = core.inbox.handle(handle)["meta"]
    assert meta["forward_of"] == interim and meta["root"] == root
    assert core.inbox.handle(interim)["meta"]["forwarded_to"] == handle
    assert "has already been forwarded" in error_text(await acall(c, STATIC_KEY, "send", args))

    await core.verb(principal("claude"), "reply", handle, text="Build passed")
    result = await core.verb(principal("second"), "result", interim)
    assert result["status"] == "answered" and result["final"]["text"] == "Build passed"
    (item,) = [i for i in core.inbox.read(owner="second", since=0)["items"] if i.get("final")]
    assert item["in_reply_to"] == interim and item["from"]["agent"] == "claude"
    with pytest.raises(CoreError, match=f"conversation {root} is paused"):
        await core.verb(principal("claude"), "reply", handle, text="One more")


async def test_final_bubbles_through_multiple_forwards(amake):
    core, _ = await amake()
    original = (await core.send(principal("capy"), to="agent:second", task="Check build"))["handle"]
    first = (await core.send(principal("second"), to="agent:claude", task="Handed off", forward=original))["handle"]
    second = (await core.send(principal("claude"), to="agent:chatgpt", task="Handed off again", forward=first))["handle"]
    assert core.inbox.handle(second)["meta"]["root"] == original
    await core.verb(principal("chatgpt"), "reply", second, text="Build passed")
    for sender, handle in (("capy", original), ("second", first), ("claude", second)):
        result = await core.verb(principal(sender), "result", handle)
        assert result["status"] == "answered" and result["final"]["text"] == "Build passed"


async def test_forward_to_project_captain_copies_its_final_only(amake, capy):
    capy.threads[CAPTAIN]["status"] = "working"
    core, _ = await amake()
    infra(core)
    core.routes.set(destination="capy", agent=None, project="infra", target={"captain": CAPTAIN}, defaults={},
                    allow_override=False, note="", by="test")
    original = (await core.send(principal("capy"), to="agent:second", task="Check build"))["handle"]
    args = {"project": "infra", "task": "Check build", "forward": original}
    outs = await asyncio.gather(core.send(principal("second"), **args), core.send(principal("second"), **args), return_exceptions=True)
    (out,) = [o for o in outs if isinstance(o, dict)]
    (error,) = [o for o in outs if isinstance(o, CoreError)]
    assert "has already been forwarded" in str(error)
    handle = out["handle"]
    assert out["thread_id"] == CAPTAIN and core.inbox.handle(handle)["meta"]["forward_of"] == original
    await eventually(lambda: capy.streams(CAPTAIN))
    capy.ack(CAPTAIN)
    capy.emit(CAPTAIN, "transcript", {"id": "x", "source": "assistant", "text": "Build passed"}, id="01B")
    await eventually(lambda: core.inbox.handle(handle)["state"].get("last_reply"))
    assert core.inbox.read(owner="capy", since=0)["items"] == []
    capy.emit(CAPTAIN, "status", {"threadId": CAPTAIN, "status": "idle"})
    await eventually(lambda: core.inbox.handle(original)["status"] == "answered")
    (item,) = core.inbox.read(owner="capy", since=0)["items"]
    assert item["in_reply_to"] == original and item["final"] and item["text"] == "Build passed"
    assert item["from"]["thread_id"] == CAPTAIN
