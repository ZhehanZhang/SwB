from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from switchboard.companion import harness as harness_module
from switchboard.companion.caps import _harness, dsh
from switchboard.companion.caps.base import CapabilityError, CompanionContext, JobContext

pytestmark = pytest.mark.skipif(os.name != "posix", reason="DeepSeek Harness uses a Unix domain socket")

_PROMPT = {"session": "session-1", "text": "Private prompt\nwith café", "conversation": "conv", "agent": "dsh"}
_RECEIPT = "receipt-1"
_ACK = {"result": {"receipt": {"messageId": _RECEIPT}}}
_KEYS = ["dsh:session-1"]


def _session(session="session-1", state="idle", **fields):
    return {"id": session, "state": state, "name": "reviewer", "dir": "/private/source/project/", **fields}


async def _send(writer, message):
    writer.write(json.dumps(message).encode() + b"\n")
    await writer.drain()


class Peer:
    def __init__(self):
        self.sessions = [_session()]
        self.calls = []
        self.subscriptions = asyncio.Queue()
        self.prompt = AsyncMock(return_value=_ACK)
        self.handlers = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            if not (line := await reader.readline()):
                return
            request = json.loads(line)
            self.calls.append(request)
            match request["method"]:
                case "list":
                    await _send(writer, {"result": {"sessions": self.sessions}})
                case "prompt":
                    await _send(writer, await self.prompt(request))
                case "subscribe":
                    await _send(writer, {"result": {"subscribed": True}})
                    self.subscriptions.put_nowait(writer)
                    await reader.read()
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            self.handlers.remove(task)

    async def subscribe(self):
        return await asyncio.wait_for(self.subscriptions.get(), 3)


@pytest.fixture
async def peer(tmp_path, monkeypatch):
    path = tmp_path / "dsh.sock"
    monkeypatch.setenv("SWB_COMPANION_HOME", str(tmp_path))
    peer = Peer()
    async with await asyncio.start_unix_server(peer.handle, path, limit=1 << 20):
        try:
            yield peer
        finally:
            handlers = peer.handlers.copy()
            for handler in handlers:
                handler.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    runtime = harness_module.Harness()
    monkeypatch.setattr(harness_module, "_HARNESS", runtime)
    return runtime


@pytest.fixture
def job():
    return JobContext("job", AsyncMock(), AsyncMock())


@asynccontextmanager
async def _watch(cap):
    events = asyncio.Queue()

    async def emit(kind, payload):
        events.put_nowait((kind, payload))
        return {"ok": True}

    task = asyncio.create_task(cap.run(CompanionContext("device", emit)))
    try:
        yield events
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _reported(events, session, state):
    kind, payload = await asyncio.wait_for(events.get(), 3)
    assert kind == "dsh.session"
    assert (payload["session"], payload["state"]) == (session, state)
    return payload


def test_socket_path_and_availability(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SWB_COMPANION_HOME", raising=False)
    monkeypatch.setattr(dsh.shutil, "which", lambda name: None)
    cap = dsh.DshCapability()
    assert dsh._socket_path() == tmp_path / ".config" / "swb" / "dsh.sock"
    monkeypatch.setenv("SWB_COMPANION_HOME", "~/custom")
    assert dsh._socket_path() == tmp_path / "custom" / "dsh.sock"
    assert cap.available() == "DeepSeek Harness is unavailable on this device."
    monkeypatch.setattr(dsh.shutil, "which", lambda name: "/bin/dsh" if name == "dsh" else None)
    assert cap.available() is None


async def test_list_returns_only_sanitized_session_metadata(peer, job, monkeypatch):
    monkeypatch.setattr(dsh.shutil, "which", lambda name: None)
    cap = dsh.DshCapability()
    assert cap.available() is None
    assert cap.actions is _harness.HARNESS_ACTIONS
    peer.sessions = [
        _session(state="working", detail="Private tool output", agent="another-agent"),
        _session("session-2", {"bad": []}, name="/private/name", dir="C:\\private\\nested"),
        _session("../private/id"),
        _session("session-3", "ended"),
    ]
    assert await cap.invoke("list", {}, job) == {"sessions": [
        {"harness": "dsh", "session": "session-1", "agent": "dsh", "name": "reviewer",
         "dir": "project", "state": "working", "detail": ""},
        {"harness": "dsh", "session": "session-2", "agent": "dsh", "name": "",
         "dir": "nested", "state": "unknown", "detail": ""},
    ]}
    with pytest.raises(CapabilityError, match="List takes no arguments"):
        await cap.invoke("list", {"unexpected": True}, job)
    assert peer.calls == [{"method": "list"}]


@pytest.mark.parametrize("changes", [
    {"session": "../secret"}, {"session": "s" * 161}, {"session": "s\n2"}, {"session": ".."},
    {"text": "x" * 60_001}, {"text": "\x1b[A"}, {"agent": 42},
])
async def test_invalid_prompts_do_not_reach_the_socket(peer, runtime, job, changes):
    cap = dsh.DshCapability()
    with pytest.raises(CapabilityError):
        await cap.invoke("prompt", {**_PROMPT, **changes}, job)
    with pytest.raises(CapabilityError):
        await cap.invoke("start", {}, job)
    assert not peer.calls and not runtime.wakes


@pytest.mark.parametrize(("result", "message"), [
    ({"error": {"code": "agent_blocked", "message": "Private /folder"}}, "Agent is waiting on a prompt."),
    ({"error": {"code": "agent_not_running", "message": "Private /folder"}}, "Agent is not running."),
    ({"error": {"code": "Private code"}}, "DeepSeek Harness could not complete the request."),
    ({"result": {"receipt": "Private receipt"}}, "DeepSeek Harness could not complete the request."),
    ({"result": {"receipt": {}}}, "DeepSeek Harness could not complete the request."),
])
async def test_prompt_rejections_are_fixed_and_quiet(peer, runtime, job, caplog, capsys, result, message):
    peer.prompt.return_value = result
    with pytest.raises(CapabilityError) as error:
        await dsh.DshCapability().invoke("prompt", _PROMPT, job)
    assert str(error.value) == message
    assert not runtime.wakes
    assert "Private" not in caplog.text
    assert capsys.readouterr() == ("", "")


async def test_watches_state_changes_discovery_and_departures(peer, runtime):
    async with _watch(dsh.DshCapability()) as events:
        stream = await peer.subscribe()
        initial = await _reported(events, "session-1", "idle")
        assert initial == {"harness": "dsh", "session": "session-1", "agent": "dsh", "name": "reviewer",
                           "dir": "project", "state": "idle", "detail": ""}
        for state in ("working", "blocked", "done", "future"):
            await _send(stream, {"event": "state", "session": "session-1", "state": state, "detail": "Private"})
            assert (await _reported(events, "session-1", state if state != "future" else "unknown"))["detail"] == ""
        peer.sessions = [_session(state="unknown"), _session("session-2", "working")]
        await _send(stream, {"event": "state", "session": "session-2", "state": "working"})
        await _reported(events, "session-2", "working")
        runtime.expect(_KEYS, "session-1", "conv", "dsh")
        peer.sessions.pop(0)
        await _send(stream, {"event": "state", "session": "session-1", "state": "ended"})
        await _reported(events, "session-1", "ended")
        assert runtime.pending(_KEYS) is None
        assert events.empty()
    assert peer.calls == [{"method": "subscribe"}, {"method": "list"}, {"method": "list"}]


async def test_resubscribes_before_reconciling_missed_changes(peer, runtime):
    async with _watch(dsh.DshCapability()) as events:
        stream = await peer.subscribe()
        await _reported(events, "session-1", "idle")
        runtime.expect(_KEYS, "session-1", "conv", "dsh")
        peer.sessions = [_session("session-2", "blocked")]
        stream.close()
        await peer.subscribe()
        await _reported(events, "session-1", "ended")
        await _reported(events, "session-2", "blocked")
        assert runtime.pending(_KEYS) is None
    assert peer.calls == [{"method": method} for method in ("subscribe", "list", "subscribe", "list")]


async def test_periodically_reconciles_without_a_state_event(peer, monkeypatch):
    monkeypatch.setattr(dsh, "_RECONCILE_S", 0.05)
    async with _watch(dsh.DshCapability()) as events:
        await peer.subscribe()
        await _reported(events, "session-1", "idle")
        peer.sessions = [_session("session-2", "done")]
        await _reported(events, "session-1", "ended")
        await _reported(events, "session-2", "done")
    assert len(peer.calls) >= 3


async def test_final_is_forwarded_once_after_accepted_wake(peer, runtime, job):
    cap = dsh.DshCapability()
    async with _watch(cap) as events:
        stream = await peer.subscribe()
        await _reported(events, "session-1", "idle")
        await _send(stream, {"event": "final", "session": "session-1", "text": "Unsolicited final", "messageIds": [_RECEIPT]})
        await _send(stream, {"event": "state", "session": "session-1", "state": "working"})
        await _reported(events, "session-1", "working")
        assert await cap.invoke("prompt", _PROMPT, job) == {"delivered": True}
        assert peer.prompt.call_args.args[0] == {"method": "prompt", "session": "session-1", "text": _PROMPT["text"]}
        assert runtime.pending(_KEYS).conversation == "conv"
        with pytest.raises(CapabilityError, match="pending wake"):
            await cap.invoke("prompt", {**_PROMPT, "conversation": "other"}, job)
        await _send(stream, {"event": "final", "session": "../session-1", "text": "Invalid session final", "messageIds": [_RECEIPT]})
        await _send(stream, {"event": "final", "session": "session-1", "text": "  ", "messageIds": [_RECEIPT]})
        await _send(stream, {"event": "final", "session": "session-1", "text": "Manual turn"})
        await _send(stream, {"event": "final", "session": "session-1", "text": "Other receipt", "messageIds": ["older"]})
        final = {"event": "final", "session": "session-1", "text": "Requested final", "messageIds": [_RECEIPT]}
        await _send(stream, final)
        assert await asyncio.wait_for(events.get(), 3) == (
            "dsh.reply", {"session": "session-1", "conversation": "conv", "agent": "dsh", "text": "Requested final"},
        )
        await _send(stream, final)
        await _send(stream, {"event": "state", "session": "session-1", "state": "done"})
        await _reported(events, "session-1", "done")
        assert events.empty()
        assert runtime.pending(_KEYS) is None
        assert peer.prompt.await_count == 1


@pytest.mark.parametrize("accepted", [True, False])
async def test_final_before_receipt_waits_for_acceptance(peer, runtime, job, accepted):
    cap = dsh.DshCapability()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def prompt(_):
        entered.set()
        await finish.wait()
        return _ACK if accepted else {"error": {"code": "agent_not_running"}}

    peer.prompt.side_effect = prompt
    async with _watch(cap) as events:
        stream = await peer.subscribe()
        await _reported(events, "session-1", "idle")
        task = asyncio.create_task(cap.invoke("prompt", _PROMPT, job))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            with pytest.raises(CapabilityError, match="pending wake"):
                await cap.invoke("prompt", {**_PROMPT, "conversation": "other"}, job)
            await _send(stream, {"event": "final", "session": "session-1", "text": "Other receipt", "messageIds": ["older"]})
            await _send(stream, {"event": "final", "session": "session-1", "text": "Early final", "messageIds": [_RECEIPT]})
            await _send(stream, {"event": "state", "session": "session-1", "state": "done"})
            await _reported(events, "session-1", "done")
            assert events.empty()
            assert not runtime.wakes
            finish.set()
            if accepted:
                assert await task == {"delivered": True}
                assert await asyncio.wait_for(events.get(), 3) == (
                    "dsh.reply", {"session": "session-1", "conversation": "conv", "agent": "dsh", "text": "Early final"},
                )
            else:
                with pytest.raises(CapabilityError, match="Agent is not running"):
                    await task
                peer.prompt.side_effect = None
                assert await cap.invoke("prompt", {**_PROMPT, "conversation": "fresh"}, job) == {"delivered": True}
                assert runtime.pending(_KEYS).conversation == "fresh"
            assert events.empty()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_connect_failures_back_off_and_stay_quiet(monkeypatch, caplog, capsys):
    connect = AsyncMock(side_effect=OSError("Private /path"))
    monkeypatch.setattr(dsh.asyncio, "open_unix_connection", connect)
    delays = []

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 8:
            raise asyncio.CancelledError

    monkeypatch.setattr(_harness.asyncio, "sleep", sleep)
    emit = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await dsh.DshCapability().run(CompanionContext("device", emit))
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]
    assert connect.await_count == 8
    emit.assert_not_called()
    assert "Private" not in caplog.text
    assert capsys.readouterr() == ("", "")
