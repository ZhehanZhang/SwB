from __future__ import annotations

from contextlib import aclosing, asynccontextmanager

import pytest
from starlette.testclient import TestClient

from conftest import OWNER, PUBLIC, STATIC_KEY, Clock, acall, base_config, call, new_core, payload, principal, rpc
from switchboard.app import create_app
from switchboard.auth import Principal
from switchboard.blocks import BLOCKS, BlockError
from switchboard.blocks.local import SESSION_TTL_S
from switchboard.companion.caps.base import CapabilityError
from switchboard.companion.harness import WAKE_TTL_S
from switchboard.companions import OFFLINE, CompanionError
from switchboard.core import CoreRefused
from switchboard.visibility import Who

CFG = base_config(version=2, blocks={"mcp": {}})
TARGET = "local:session:herdr:pane_1"
REPORT = {"harness": "herdr", "session": "pane_1", "agent": "claude", "name": "Code Review",
          "dir": "/private/example", "state": "idle"}


@asynccontextmanager
async def device(core, *capabilities):
    companions = core.companions
    code = companions.mint(PUBLIC, origin="test", source="owner", client="local")["code"]
    paired = companions.pair({"code": code, "name": "Device", "platform": "linux", "capabilities": [
        {"id": cap, "version": "1", "title": cap, "actions": [] if cap == "hooks" else ["prompt"]} for cap in capabilities
    ]}, client="local")
    async with aclosing(companions.events(paired["companion_id"], 3600)) as stream:
        assert await anext(stream) == ("ping", "{}")
        yield companions.authenticate(paired["device_key"])


async def emit(core, linked, kind, data, capability=None):
    return await core.companions.emit(linked, {"capability": capability or kind.partition(".")[0], "kind": kind, "payload": data})


async def test_session_registry_targets_console_and_expiry(amake):
    clock = Clock()
    core, client = await amake(config=CFG, clock=clock)
    assert "local" not in core.blocks
    async with device(core, "herdr", "hooks") as linked:
        with pytest.raises(CompanionError, match="Unknown companion event"):
            await emit(core, linked, "herdr.session", REPORT, capability="hooks")
        await emit(core, linked, "herdr.session", REPORT)
        assert core.status()["destinations"]["local"]["online"]
        local = core.blocks["local"]
        [entry] = await core.targets(block="local")
        assert {key: entry[key] for key in ("address", "name", "dir", "state", "slugs", "agent")} == {
            "address": TARGET, "name": "Code Review", "dir": "example", "state": "idle", "slugs": ["code-review"], "agent": "claude-code"}
        assert entry["persona"]["id"] == "claude-code"
        assert str(await core.resolve_to("code-review")) == TARGET
        assert local.page_rows() == [("Code Review", "herdr", "idle", True)]
        assert (await client.get("/api/health", headers=OWNER)).json()["blocks"]["local"]["online"]
        board = (await client.get("/api/board", headers=OWNER)).json()
        assert any(row["id"] == TARGET and row["detail"] == "idle" for row in board["unassigned"])
        assert any(row["name"] == "local" for row in (await client.get("/api/platforms", headers=OWNER)).json()["platforms"])
        assert any(row["name"] == "local" for row in (await client.get("/api/about", headers=OWNER)).json()["blocks"])
        await emit(core, linked, "herdr.session", {**REPORT, "state": "blocked"})
        assert (await core.targets(block="local"))[0]["state"] == "blocked"
        await emit(core, linked, "herdr.session", {**REPORT, "state": "ended"})
        assert not await core.targets(block="local") and not local.sessions and not local.page_rows()
        await emit(core, linked, "herdr.session", REPORT)
        clock.t += SESSION_TTL_S
        assert not await core.targets(block="local") and "local" not in core.status()["blocks"]
        await emit(core, linked, "herdr.session", REPORT)
    assert not await core.targets(block="local") and not local.sessions and not local.page_rows()
    assert "local" not in core.status()["destinations"]


async def test_send_mailbox_first_and_fallbacks(amake, monkeypatch):
    core, _ = await amake(config=CFG)
    sender = principal("gemini")
    attempts = []
    failure = None

    async def invoke(companion, capability, action, args, *, principal):
        attempts.append((companion, capability, action, args, principal))
        item = core.inbox.handle(args["conversation"])
        assert item["owner"] == "claude-code" and item["destination"] == "mailbox"
        assert core.inbox.read(owner="claude-code", advance=False)["items"][-1]["text"] == "Please review."
        if failure:
            raise failure
        return {}

    monkeypatch.setattr(core.companions, "invoke", invoke)
    async with device(core, "herdr", "hooks") as linked:
        await emit(core, linked, "herdr.session", REPORT)
        out = await core.send(sender, task="Please review.", to="code-review")
        assert out["mailbox"] and out["woke"] and out["to"] == TARGET
        [(companion, cap, action, args, from_)] = attempts
        assert (companion, cap, action, from_) == (linked.client_id.split(":")[1], "herdr", "prompt", sender)
        assert args["session"] == "pane_1" and args["agent"] == "claude-code" and args["conversation"] == out["conversation"]
        assert "Please review." in args["text"] and f'reply(conversation="{out["conversation"]}"' in args["text"]
        for failure in (CompanionError(OFFLINE), CapabilityError("Agent is waiting on a prompt")):
            out = await core.send(sender, task="Please review.", to=TARGET)
            assert out["mailbox"] and not out["woke"] and str(failure) in out["note"]
            assert out["conversation"] not in core.blocks["local"].wakes
        await emit(core, linked, "herdr.session", {**REPORT, "state": "blocked"})
        out = await core.send(sender, task="Please review.", to=TARGET)
        assert not out["woke"] and "waiting on a prompt" in out["note"] and len(attempts) == 3
        await emit(core, linked, "hooks.session", {**REPORT, "harness": "gemini"})
        out = await core.send(sender, task="Please review.", to="local:session:gemini:pane_1")
        assert out["agent"] == "gemini" and not out["woke"] and "mailbox" in out["note"] and len(attempts) == 3
        out = await core.send(sender, task="Please review.", to="local:session:codex:missing")
        assert out["agent"] == "codex" and not out["woke"] and "session unavailable" in out["note"]
        await emit(core, linked, "herdr.session", {**REPORT, "state": "idle", "agent": "not-registered"})
        out = await core.send(sender, task="Please review.", to=TARGET)
        assert out["agent"] == "owner" and not out["woke"] and core.inbox.handle(out["handle"])["owner"] == "owner"
        await emit(core, linked, "herdr.session", REPORT)
    out = await core.send(sender, task="Please review.", to=TARGET)
    assert out["agent"] == "claude-code" and not out["woke"] and OFFLINE in out["note"]
    audited = core.audit.recent(destination="local", action="wake")
    assert len(audited) == 8 and audited[0]["outcome"] == "mailbox" and audited[-1]["outcome"] == "ok"


async def test_local_access_requires_access_to_the_backing_agent(amake, monkeypatch):
    core, _ = await amake(config=CFG)

    async def invoke(*args, **kwargs):
        return {}

    monkeypatch.setattr(core.companions, "invoke", invoke)
    async with device(core, "herdr") as linked:
        await emit(core, linked, "herdr.session", REPORT)
        external = Principal("gemini", "chat", "slack", trust="external", visibility_who=Who(False, "U123", "desk"))
        core.visibility.set(f"target:{TARGET}", "shared", "owner", core.clock())
        with pytest.raises(BlockError, match="Access to this local session is restricted"):
            await core.send(external, task="Hello", to=TARGET)
        core.visibility.set("target:claude-code", ["U123"], "owner", core.clock())
        assert (await core.send(external, task="Hello", to=TARGET))["woke"]
        scoped = Principal("gemini", "key", "key", targets=(TARGET,))
        with pytest.raises(CoreRefused, match="may send only"):
            await core.send(scoped, task="Hello", to=TARGET)
        scoped = Principal("gemini", "key", "key", targets=("agent:claude-code",))
        assert (await core.send(scoped, task="Hello", to=TARGET))["woke"]


async def test_shared_session_prefers_a_wake_capability_and_avoids_device_collisions(amake, monkeypatch):
    core, _ = await amake(config=CFG)
    attempts = []

    async def invoke(companion, capability, action, args, *, principal):
        attempts.append((companion, capability))
        return {}

    monkeypatch.setattr(core.companions, "invoke", invoke)
    target = "local:session:codex:thread_1"
    report = {"harness": "codex", "session": "thread_1", "state": "idle"}
    async with device(core, "hooks", "codex") as first:
        await emit(core, first, "codex.session", report)
        await emit(core, first, "hooks.session", {**report, "state": "done"})
        assert (await core.targets(block="local"))[0]["state"] == "done"
        assert (await core.send(principal(), task="Hello", to=target))["woke"]
        assert attempts == [(first.client_id.split(":")[1], "codex")]
        async with device(core, "codex") as second:
            await emit(core, second, "codex.session", report)
            assert len(core.blocks["local"].sessions) == 2
            out = await core.send(principal(), task="Hello", to=target)
            assert out["mailbox"] and not out["woke"] and "Multiple companions" in out["note"]
            assert len(attempts) == 1


async def test_reply_relay_uses_mcp_path_and_enforces_wake_binding(amake, monkeypatch):
    clock = Clock()
    core, client = await amake(config=CFG, clock=clock)
    sender = principal("gemini")
    answer = {"session": "pane_1", "agent": "claude-code", "text": "Finished the review."}
    early = ""
    denied = False

    async def invoke(companion, cap, action, args, *, principal):
        if early:
            await emit(core, linked, "hooks.reply", {**answer, "conversation": args["conversation"], "text": early})
        if denied:
            raise CapabilityError("Agent is waiting on a prompt.")
        return {}

    monkeypatch.setattr(core.companions, "invoke", invoke)
    async with device(core, "herdr", "hooks", "codex") as linked, device(core, "hooks") as other:
        await emit(core, linked, "herdr.session", REPORT)
        await emit(core, linked, "hooks.reply", {**answer, "conversation": "h_unsolicited"})
        conversation = (await core.send(sender, task="Review.", to=TARGET))["conversation"]
        data = {**answer, "conversation": conversation}
        await emit(core, other, "hooks.reply", data)
        await emit(core, linked, "codex.reply", data)
        await emit(core, linked, "hooks.reply", {**data, "session": "elsewhere"})
        await emit(core, linked, "hooks.reply", {**data, "agent": "owner"})
        assert not core.mailbox_replies(conversation, sender.source)
        claude_code = core.connections.mint(agent="claude-code", project=None, client="test", by="owner")
        key = core.connections.redeem(claude_code["code"])["key"]
        out = payload(await acall(client, key, "reply", {"conversation": conversation, "text": "Working on it.", "final": False}))
        assert out["in_reply_to"] == conversation
        await emit(core, linked, "hooks.reply", data)
        await emit(core, linked, "hooks.reply", data)
        interim, final = core.mailbox_replies(conversation, sender.source)
        assert not interim["final"] and final["final"] and answer["text"] in final["text"]
        assert final["from"]["agent"] == "claude-code" and final["from"]["origin"] == "companion"
        assert core.inbox.handle(conversation)["status"] == "answered"
        assert core.audit.recent(destination="mailbox", action="send")[0]["excerpt"] == ""

        conversation = (await core.send(sender, task="Review.", to=TARGET))["conversation"]
        await acall(client, key, "reply", {"conversation": conversation, "text": "MCP final."})
        await emit(core, linked, "hooks.reply", {**answer, "conversation": conversation})
        [final] = core.mailbox_replies(conversation, sender.source)
        assert final["text"] == "MCP final." and final["final"]

        conversation = (await core.send(sender, task="Review.", to=TARGET))["conversation"]
        clock.t += WAKE_TTL_S
        await emit(core, linked, "hooks.reply", {**answer, "conversation": conversation})
        assert not core.mailbox_replies(conversation, sender.source)

        early = "Fast answer."
        conversation = (await core.send(sender, task="Review.", to=TARGET))["conversation"]
        [final] = core.mailbox_replies(conversation, sender.source)
        assert early in final["text"]
        denied = True
        conversation = (await core.send(sender, task="Review.", to=TARGET))["conversation"]
        assert not core.mailbox_replies(conversation, sender.source)


def test_no_companion_deployment_parity(tmp_path, capy, monkeypatch):
    baseline_dir, local_dir = tmp_path / "baseline", tmp_path / "local"
    baseline_dir.mkdir()
    local_dir.mkdir()
    with monkeypatch.context() as patch:
        patch.delitem(BLOCKS, "local")
        baseline = new_core(baseline_dir, capy, config=CFG)
    local = new_core(local_dir, capy, config=CFG)
    assert set(baseline.blocks) == set(local.blocks) == {"mcp"}
    with TestClient(create_app(baseline), base_url=PUBLIC) as before, TestClient(create_app(local), base_url=PUBLIC) as after:
        for path, headers in (("/healthz", {}), ("/healthz", OWNER), ("/api/platforms", OWNER), ("/api/about", OWNER)):
            a, b = before.get(path, headers=headers), after.get(path, headers=headers)
            assert a.status_code == b.status_code == 200 and a.json() == b.json()
        assert payload(call(before, STATIC_KEY, "switchboard_status")) == payload(call(after, STATIC_KEY, "switchboard_status"))
        assert payload(call(before, STATIC_KEY, "targets")) == payload(call(after, STATIC_KEY, "targets"))
        assert rpc(before, STATIC_KEY, "tools/list").json() == rpc(after, STATIC_KEY, "tools/list").json()
        init = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}
        assert rpc(before, STATIC_KEY, "initialize", init).json() == rpc(after, STATIC_KEY, "initialize", init).json()


async def test_reply_relay_failure_keeps_the_wake_for_a_retry(amake, monkeypatch):
    core, _ = await amake(config=CFG)

    async def invoke(*args, **kwargs):
        return {}

    monkeypatch.setattr(core.companions, "invoke", invoke)
    async with device(core, "herdr") as linked:
        await emit(core, linked, "herdr.session", REPORT)
        out = await core.send(principal("gemini"), task="Please review.", to=TARGET)
        local = core.blocks["local"]
        relay = local._relay

        async def failing(*args):
            raise RuntimeError("relay failed")

        monkeypatch.setattr(local, "_relay", failing)
        answer = {"session": "pane_1", "conversation": out["conversation"], "agent": "claude-code", "text": "Done."}
        with pytest.raises(CompanionError):
            await emit(core, linked, "herdr.reply", answer)
        assert out["conversation"] in local.wakes
        monkeypatch.setattr(local, "_relay", relay)
        await emit(core, linked, "herdr.reply", answer)
        assert out["conversation"] not in local.wakes
