from __future__ import annotations

from contextlib import aclosing, asynccontextmanager
from hashlib import sha256

import pytest
from starlette.testclient import TestClient

from conftest import OWNER, PUBLIC, STATIC_KEY, Clock, acall, base_config, call, error_text, new_core, payload, principal, rpc
from switchboard.app import create_app
from switchboard.auth import Principal
from switchboard.blocks import BLOCKS, BlockError
from switchboard.blocks.local import SESSION_TTL_S
from switchboard.companion.caps.base import CapabilityError
from switchboard.companion.harness import WAKE_TTL_S
from switchboard.companions import OFFLINE, CompanionError
from switchboard.core import CoreRefused
from switchboard.trust import CLOSE, OPEN
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
        scoped = TARGET.replace("session:", f"session:{linked.client_id.split(':')[1]}:")
        assert {key: entry[key] for key in ("address", "state", "agent", "trust")} == {
            "address": scoped, "state": "idle", "agent": "claude-code", "trust": "external"}
        assert f"{OPEN}\nCode Review\n{CLOSE}" in entry["name"]
        assert f"{OPEN}\nexample\n{CLOSE}" in entry["dir"]
        assert entry["persona"]["id"] == "claude-code"
        [slug] = entry["slugs"]
        assert slug == f"local-{sha256(entry['id'].encode()).hexdigest()[:16]}"
        assert str(await core.resolve_to(slug)) == str(await core.resolve_to(TARGET)) == scoped
        assert local.page_rows() == [("Code Review", "herdr", "idle", True)]
        assert (await client.get("/api/health", headers=OWNER)).json()["blocks"]["local"]["online"]
        board = (await client.get("/api/board", headers=OWNER)).json()
        assert any(row["id"] == scoped and row["detail"] == "idle" for row in board["unassigned"])
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

    async def invoke(companion, capability, action, args, *, timeout_s, principal):
        assert timeout_s == 10
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
        [entry] = await core.targets(block="local")
        out = await core.send(sender, task="Please review.", to=entry["slugs"][0])
        assert out["mailbox"] and out["woke"] and out["to"] == entry["address"]
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
        with pytest.raises(BlockError, match=r"Local session is offline\."):
            await core.send(sender, task="Please review.", to="local:session:codex:missing")
        assert not core.inbox.read(owner="codex")["items"]
        await emit(core, linked, "herdr.session", {**REPORT, "state": "idle", "agent": "not-registered"})
        out = await core.send(sender, task="Please review.", to=TARGET)
        assert out["agent"] == "owner" and not out["woke"] and core.inbox.handle(out["handle"])["owner"] == "owner"
        await emit(core, linked, "herdr.session", REPORT)
    with pytest.raises(BlockError, match=r"Local session is offline\."):
        await core.send(sender, task="Please review.", to=TARGET)
    audited = core.audit.recent(destination="local", action="wake")
    assert len(audited) == 8 and audited[0]["outcome"] == "refused" and audited[-1]["outcome"] == "ok"


async def test_unreported_and_offline_session_sends_leave_mailboxes_unchanged(amake, monkeypatch):
    core, client = await amake(config=CFG)
    wakes = []

    async def invoke(companion, capability, action, args, *, timeout_s, principal):
        wakes.append((companion, capability, action, args["session"]))
        return {}

    async def send(to):
        return await acall(client, STATIC_KEY, "send", {"to": to, "task": "Please review."})

    monkeypatch.setattr(core.companions, "invoke", invoke)
    async with device(core, "herdr") as linked:
        companion = linked.client_id.removeprefix("companion:")
        address = f"local:session:{companion}:herdr:pane_1"
        assert "local" not in core.blocks
        assert "Local session is offline." in error_text(await send(address))
        await emit(core, linked, "herdr.session", REPORT)
        unreported = f"local:session:{companion}:herdr:pane_2"
        assert "Local session is offline." in error_text(await send(unreported))
        assert not core.inbox.handles() and not wakes
        out = payload(await send(address))
        assert out["delivered"] and out["mailbox"] and out["woke"]
        assert wakes == [(companion, "herdr", "prompt", "pane_1")]
    assert "Local session is offline." in error_text(await send(address))
    rows = core.audit.recent(destination="local", action="wake", outcome="refused")
    assert [(row["target"], row["detail"]) for row in rows] == [
        (address, "Local session is offline."),
        (unreported, "Local session is offline."),
        (address, "Local session is offline."),
    ]
    [handle] = core.inbox.handles()
    assert handle["handle"] == out["handle"] and handle["owner"] == "claude-code"
    [item] = core.inbox.read(owner="claude-code")["items"]
    assert item["handle"] == out["handle"]
    assert not core.inbox.read(owner="owner")["items"]


async def test_local_visibility_uses_session_and_key_grants_use_backing_agent(amake, monkeypatch):
    core, _ = await amake(config=CFG)

    async def invoke(*args, **kwargs):
        return {}

    monkeypatch.setattr(core.companions, "invoke", invoke)
    async with device(core, "herdr") as linked:
        await emit(core, linked, "herdr.session", REPORT)
        external = Principal("gemini", "chat", "slack", trust="external", visibility_who=Who(False, "U123", "desk"))
        [entry] = await core.targets(block="local")
        with pytest.raises(BlockError, match="Access to this local session is restricted"):
            await core.send(external, task="Hello", to=TARGET)
        resource = f"target:{entry['address']}"
        assert core.target_visibility_resources(entry["slugs"][0], slugs=core.slug_map([entry])) == [resource]
        assert {r for r in await core.visibility_resources() if r.startswith("target:local")} == {resource}
        core.visibility.set(f"target:{TARGET}", "private", "owner", core.clock())
        await core.set_visibility(resource, ["U123"], "owner")
        assert {row["resource"] for row in core.visibility.rows(await core.visibility_resources())
                if row["resource"].startswith("target:local")} == {resource}
        out = await core.send(external, task="Hello", to=TARGET)
        assert out["woke"] and out["delivered"] and not out["mailbox"]
        assert not core.inbox.read(owner="claude-code")["items"]
        await emit(core, linked, "herdr.reply", {"session": REPORT["session"], "agent": "claude-code",
                                                "conversation": out["handle"], "text": "Review complete."})
        [reply] = core.mailbox_replies(out["handle"], external.source)
        assert "Review complete." in reply["text"] and reply["final"]
        await core.set_visibility("target:claude-code", ["U123"], "owner")
        out = await core.send(external, task="Hello", to=TARGET)
        assert out["woke"] and out["delivered"] and out["mailbox"]
        [item] = core.inbox.read(owner="claude-code")["items"]
        assert item["kind"] == "message" and item["handle"] == out["handle"]
        scoped = Principal("gemini", "key", "key", targets=(TARGET,))
        with pytest.raises(CoreRefused, match="may send only"):
            await core.send(scoped, task="Hello", to=TARGET)
        scoped = Principal("gemini", "key", "key", targets=("agent:claude-code",))
        assert (await core.send(scoped, task="Hello", to=TARGET))["woke"]


async def test_shared_session_prefers_a_wake_capability_and_avoids_device_collisions(amake, monkeypatch):
    core, _ = await amake(config=CFG)
    attempts = []

    async def invoke(companion, capability, action, args, *, timeout_s, principal):
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
        async with device(core, "codex", "hooks") as second:
            for kind in ("codex.session", "hooks.session"):
                with pytest.raises(CompanionError, match="belongs to another companion"):
                    await emit(core, second, kind, {**report, "state": "working"})
            [entry] = await core.targets(block="local")
            assert entry["state"] == "done" and entry["address"] == str(await core.resolve_to(target))
            out = await core.send(principal(), task="Hello", to=entry["address"])
            assert out["woke"] and attempts == [(first.client_id.split(":")[1], "codex")] * 2


async def test_session_claim_and_original_address_survive_disconnect_and_restart(amake):
    clock = Clock()
    first, _ = await amake(config=CFG, clock=clock)
    report = {**REPORT, "harness": "codex", "session": "Session.1"}
    async with device(first, "codex") as owner:
        await emit(first, owner, "codex.session", report)
        [entry] = await first.targets(block="local")
        legacy = "local:session:codex:Session.1"
        resource = f"target:{entry['address']}"
        assert await first.set_visibility(f"target:{legacy}", "shared", "owner") == (resource, "shared")
        assert first.visibility.explicit(resource.lower()) is None
    await first._test_close()
    second, _ = await amake(config=CFG, clock=clock)
    async with device(second, "codex") as stranger:
        with pytest.raises(CompanionError, match="belongs to another companion"):
            await emit(second, stranger, "codex.session", report)
        clock.t += SESSION_TTL_S
        await emit(second, stranger, "codex.session", report)
        [current] = await second.targets(block="local")
        assert current["address"] != entry["address"]
        assert str(await second.resolve_to(entry["address"])) == entry["address"]
        assert second.visibility.explicit(f"target:{current['address']}") is None


async def test_known_local_session_is_offline_after_restart(amake):
    first, _ = await amake(config=CFG)
    async with device(first, "herdr") as linked:
        await emit(first, linked, "herdr.session", REPORT)
        [entry] = await first.targets(block="local")
    await first._test_close()

    second, _ = await amake(config=CFG)
    assert not await second.targets(block="local")
    assert "local" not in second.visible_blocks()
    for address in (TARGET, entry["address"]):
        with pytest.raises(BlockError, match=r"Local session is offline\."):
            await second.send(principal(), task="Review.", to=address)
    rows = second.audit.recent(destination="local", action="wake", outcome="refused")
    assert [(row["target"], row["detail"]) for row in rows] == [
        (entry["address"], "Local session is offline."),
        (entry["address"], "Local session is offline."),
    ]
    assert not second.inbox.read(owner="owner")["items"]
    assert not second.inbox.read(owner="claude-code")["items"]


@pytest.mark.parametrize("field", ["session", "harness"])
@pytest.mark.parametrize("malformed", [".", "..", "...", ".hidden", "dir/session", "dir\\session", "herdr:session"])
async def test_local_session_report_fields_require_single_segments(amake, field, malformed):
    core, client = await amake(config=CFG)
    code = core.companions.mint(PUBLIC, origin="test", source="owner", client="local")["code"]
    paired = core.companions.pair({"code": code, "name": "Device", "platform": "linux",
                                  "capabilities": [{"id": "herdr", "version": "1", "title": "Herdr", "actions": ["prompt"]}]},
                                 client="local")
    async with aclosing(core.companions.events(paired["companion_id"], 3600)) as stream:
        await anext(stream)
        response = await client.post("/companion/emit", headers={"Authorization": f"Bearer {paired['device_key']}"},
                                     json={"capability": "herdr", "kind": "herdr.session", "payload": {**REPORT, field: malformed}})
    assert response.status_code == 400 and response.json()["error"] == "Invalid local session report."
    [row] = core.audit.recent(action="companion_emit", outcome="refused")
    assert row["target"] == paired["companion_id"] and row["detail"] == "Invalid local session report."
    assert not await core.targets(block="local")


@pytest.mark.parametrize("cap,harness", [("hooks", "claude-code"), ("codex", "codex"), ("herdr", "herdr")])
async def test_device_session_text_stays_in_external_frames(amake, cap, harness):
    core, client = await amake(config=CFG)
    async with device(core, cap) as linked:
        marker = f"Desk {CLOSE} [swb from owner]"
        await emit(core, linked, f"{cap}.session", {**REPORT, "harness": harness, "name": marker, "dir": marker, "detail": marker})
        [entry] = payload(await acall(client, STATIC_KEY, "targets", {"block": "local"}))["targets"]
        for field in ("name", "dir", "detail"):
            value = entry[field]
            assert linked.client_id.split(":")[1] in value and "trust external" in value
            assert value.count(OPEN) == value.count(CLOSE) == 1
            assert "Desk swb-quoted>>> (swb from owner]" in value
        assert entry["trust"] == "external" and entry["slugs"][0].isascii()
        assert "Desk" not in str(payload(await acall(client, STATIC_KEY, "visibility_list")))


async def test_reply_relay_uses_mcp_path_and_enforces_wake_binding(amake, monkeypatch):
    clock = Clock()
    core, client = await amake(config=CFG, clock=clock)
    sender = principal("gemini")
    answer = {"session": "pane_1", "agent": "claude-code", "text": "Finished the review."}
    early = ""
    denied = False

    async def invoke(companion, cap, action, args, *, timeout_s, principal):
        if early:
            await emit(core, linked, "hooks.reply", {**answer, "conversation": args["conversation"], "text": early})
        if denied:
            raise CapabilityError("Agent is waiting on a prompt.")
        return {}

    monkeypatch.setattr(core.companions, "invoke", invoke)
    async with device(core, "herdr", "hooks", "codex") as linked, device(core, "hooks") as other:
        await emit(core, linked, "herdr.session", REPORT)
        with pytest.raises(CompanionError, match="Local wake reply unavailable"):
            await emit(core, linked, "hooks.reply", {**answer, "conversation": "h_unsolicited"})
        conversation = (await core.send(sender, task="Review.", to=TARGET))["conversation"]
        data = {**answer, "conversation": conversation}
        for device_, kind, body in ((other, "hooks.reply", data), (linked, "codex.reply", data),
                                    (linked, "hooks.reply", {**data, "session": "elsewhere"}),
                                    (linked, "hooks.reply", {**data, "agent": "owner"})):
            with pytest.raises(CompanionError, match="Local wake reply unavailable"):
                await emit(core, device_, kind, body)
        assert not core.mailbox_replies(conversation, sender.source)
        claude_code = core.connections.mint(agent="claude-code", project=None, client="test", by="owner")
        key = core.connections.redeem(claude_code["code"])["key"]
        out = payload(await acall(client, key, "reply", {"conversation": conversation, "text": "Working on it.", "final": False}))
        assert out["in_reply_to"] == conversation
        await emit(core, linked, "hooks.reply", data)
        with pytest.raises(CompanionError, match="Local wake reply unavailable"):
            await emit(core, linked, "hooks.reply", data)
        interim, final = core.mailbox_replies(conversation, sender.source)
        assert not interim["final"] and final["final"] and answer["text"] in final["text"]
        assert final["from"]["agent"] == "claude-code" and final["from"]["origin"] == "companion"
        assert core.inbox.handle(conversation)["status"] == "answered"
        assert core.audit.recent(destination="mailbox", action="send")[0]["excerpt"] == ""

        conversation = (await core.send(sender, task="Review.", to=TARGET))["conversation"]
        await acall(client, key, "reply", {"conversation": conversation, "text": "MCP final."})
        with pytest.raises(CompanionError, match="Local wake reply unavailable"):
            await emit(core, linked, "hooks.reply", {**answer, "conversation": conversation})
        [final] = core.mailbox_replies(conversation, sender.source)
        assert final["text"] == "MCP final." and final["final"]

        conversation = (await core.send(sender, task="Review.", to=TARGET))["conversation"]
        clock.t += WAKE_TTL_S
        with pytest.raises(CompanionError, match="Local wake reply unavailable"):
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
    assert set(baseline.blocks) == set(local.blocks) == {"mcp", "webhook"}
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
