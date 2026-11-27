from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from switchboard.companion import harness as harness_module
from switchboard.companion.caps import _harness, herdr
from switchboard.companion.caps.base import CapabilityError, CompanionContext, JobContext

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Herdr uses a Unix domain socket")

_PROMPT = {"session": "w1:p1", "text": "Private prompt\nwith café", "conversation": "conv", "agent": "local"}
_LIFECYCLE = [{"type": "pane.agent_detected"}, {"type": "pane.exited"}]


def _agent(pane="w1:p1", state="idle", **fields):
    return {
        "pane_id": pane, "agent_status": state, "agent": "claude", "name": "reviewer",
        "cwd": "/private/source/project/", "title": "Private transcript",
        "tokens": {"summary": "Private tool input"}, "agent_session": {"value": "Private native ID"},
        **fields,
    }


async def _send(writer, message):
    writer.write(json.dumps(message).encode() + b"\n")
    await writer.drain()


class Peer:
    def __init__(self):
        self.agents = []
        self.calls = []
        self.subscriptions = asyncio.Queue()
        self.prompt = AsyncMock(return_value={"result": {"type": "agent_prompted", "agent": _agent()}})
        self.list = AsyncMock(side_effect=lambda: self.agents)
        self.handlers = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            if not (line := await reader.readline()):
                return
            request = json.loads(line)
            self.calls.append(request)
            request_id = request["id"]
            method = request["method"]
            if method == "events.subscribe":
                await _send(writer, {"id": request_id, "result": {"type": "subscription_started"}})
                self.subscriptions.put_nowait((request, writer))
                await reader.read()
            elif method == "agent.list":
                await _send(writer, {"id": request_id, "result": {"type": "agent_list", "agents": await self.list()}})
            elif method == "agent.prompt":
                await _send(writer, {"id": request_id, **await self.prompt(request)})
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            self.handlers.remove(task)

    async def subscription(self, *panes):
        request, writer = await asyncio.wait_for(self.subscriptions.get(), 2)
        assert request["params"] == {"subscriptions": [
            *_LIFECYCLE, *({"type": "pane.agent_status_changed", "pane_id": pane} for pane in panes),
        ]}
        return request, writer


@pytest.fixture
async def peer(tmp_path, monkeypatch):
    path = tmp_path / "herdr.sock"
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(path))
    peer = Peer()
    async with await asyncio.start_unix_server(peer.handle, path, limit=1024 * 1024):
        yield peer
        for handler in peer.handlers.copy():
            handler.cancel()
        await asyncio.gather(*peer.handlers, return_exceptions=True)


@pytest.fixture
def runtime(monkeypatch):
    instance = harness_module.Harness()
    monkeypatch.setattr(harness_module, "_HARNESS", instance)
    return instance


@pytest.fixture
def job():
    return JobContext("job", AsyncMock(), AsyncMock())


@asynccontextmanager
async def _watch(cap):
    reports = asyncio.Queue()

    async def emit(kind, payload):
        await reports.put((kind, payload))
        return {"ok": True}

    async def report(pane, state):
        kind, payload = await asyncio.wait_for(reports.get(), 2)
        assert kind == "herdr.session"
        assert payload["session"] == pane
        assert payload["state"] == state
        return payload

    task = asyncio.create_task(cap.run(CompanionContext("device", emit)))
    try:
        yield report
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def test_socket_paths_and_availability(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERDR_SOCKET_PATH", raising=False)
    monkeypatch.delenv("HERDR_SESSION", raising=False)
    monkeypatch.setattr(herdr.shutil, "which", lambda name: None)
    cap = herdr.HerdrCapability()
    root = tmp_path / ".config" / "herdr"
    assert cap.available() == "Herdr is unavailable on this device."
    assert herdr._socket_path() == root / "herdr.sock"
    with monkeypatch.context() as patch:
        patch.setattr(herdr.shutil, "which", lambda name: "/bin/herdr")
        assert cap.available() is None
    monkeypatch.setenv("HERDR_SESSION", "my-work")
    assert herdr._socket_path() == root / "sessions" / "my-work" / "herdr.sock"
    monkeypatch.setenv("HERDR_SESSION", "../outside")
    with pytest.raises(CapabilityError, match="^Herdr is unavailable on this device\\.$"):
        herdr._socket_path()
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "override.sock"))
    assert herdr._socket_path() == tmp_path / "override.sock"
    assert cap.available() is None


async def test_list_returns_only_session_metadata(peer, job):
    cap = herdr.HerdrCapability()
    assert cap.available() is None
    assert list(cap.actions) == ["list", "prompt"]
    peer.agents = [
        _agent(state="working"),
        _agent("wA:pZ", "future", name="/private/name", agent="C:\\private\\agent", foreground_cwd="C:\\private\\nested"),
    ]
    assert await cap.invoke("list", {}, job) == {"sessions": [
        {"harness": "herdr", "session": "w1:p1", "agent": "claude", "name": "reviewer",
         "dir": "project", "state": "working", "detail": ""},
        {"harness": "herdr", "session": "wA:pZ", "agent": "", "name": "",
         "dir": "nested", "state": "unknown", "detail": ""},
    ]}
    with pytest.raises(CapabilityError, match="^List takes no arguments\\.$"):
        await cap.invoke("list", {"unexpected": True}, job)
    assert [request["method"] for request in peer.calls] == ["agent.list"]


async def test_method_allowlist_blocks_other_rpc_calls(peer, job):
    assert herdr.HERDR_METHODS == {
        "ping", "agent.list", "agent.get", "agent.prompt", "events.subscribe", "session.snapshot",
    }
    for method in ("agent.start", "agent.send_keys", "agent.read", "pane.send_keys", "server.stop"):
        with pytest.raises(CapabilityError):
            async with herdr._exchange(herdr._socket_path(), method, {}, "ignored"):
                pytest.fail("Disallowed method reached the socket")
    with pytest.raises(CapabilityError):
        await herdr.HerdrCapability().invoke("agent.start", {}, job)
    assert not peer.calls


async def test_prompt_delivers_before_recording_wake(peer, runtime, job, caplog, capsys):
    async def delivery(request):
        assert request["params"] == {"target": "w1:p1", "text": _PROMPT["text"]}
        assert not runtime.wakes
        return {"result": {"type": "agent_prompted", "agent": _agent()}}

    peer.prompt.side_effect = delivery
    assert await herdr.HerdrCapability().invoke("prompt", _PROMPT, job) == {"delivered": True}
    wake = runtime.take(["herdr:w1:p1"])
    assert (wake.session, wake.conversation, wake.agent) == ("w1:p1", "conv", "local")
    assert [request["method"] for request in peer.calls] == ["agent.prompt"]
    assert "Private" not in caplog.text
    assert capsys.readouterr() == ("", "")


async def test_prompt_accepts_the_full_character_limit(peer, runtime, job):
    text = "☃" * 60_000
    assert await herdr.HerdrCapability().invoke("prompt", {**_PROMPT, "text": text}, job) == {"delivered": True}
    assert peer.prompt.call_args.args[0]["params"]["text"] == text


async def test_in_flight_wake_cannot_be_overwritten(peer, runtime, job):
    entered, accept = asyncio.Event(), asyncio.Event()

    async def delivery(request):
        entered.set()
        await accept.wait()
        return {"result": {"type": "agent_prompted", "agent": _agent()}}

    peer.prompt.side_effect = delivery
    cap = herdr.HerdrCapability()
    first = asyncio.create_task(cap.invoke("prompt", _PROMPT, job))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(CapabilityError, match="pending wake"):
            await cap.invoke("prompt", {**_PROMPT, "conversation": "other"}, job)
        accept.set()
        assert await first == {"delivered": True}
        with pytest.raises(CapabilityError, match="pending wake"):
            await cap.invoke("prompt", {**_PROMPT, "conversation": "other"}, job)
        assert runtime.pending(["herdr:w1:p1"]).conversation == _PROMPT["conversation"]
        assert [request["method"] for request in peer.calls] == ["agent.prompt"]
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("agent_blocked", "Agent is waiting on a prompt."),
        ("agent_not_running", "Agent is not running."),
        ("agent_not_found", "Agent is not running."),
        ("unexpected", "Herdr could not complete the request."),
    ],
)
async def test_prompt_errors_are_fixed_and_quiet(peer, runtime, job, caplog, capsys, code, message):
    peer.prompt.return_value = {"error": {"code": code, "message": "/private/path\n" + _PROMPT["text"]}}
    with pytest.raises(CapabilityError) as error:
        await herdr.HerdrCapability().invoke("prompt", _PROMPT, job)
    assert str(error.value) == message
    assert not runtime.wakes
    assert "private" not in caplog.text.lower()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("changes", [
    {"session": "reviewer"}, {"text": "x" * 60_001}, {"agent": 42},
    {"text": "\x1b[A"}, {"text": "yes\r"}, {"text": "verify\t"},
])
async def test_invalid_prompts_are_never_sent(peer, runtime, job, changes):
    with pytest.raises(CapabilityError):
        await herdr.HerdrCapability().invoke("prompt", {**_PROMPT, **changes}, job)
    assert not peer.calls and not runtime.wakes


async def test_prompt_requires_the_acknowledged_pane_to_match(peer, runtime, job):
    peer.prompt.return_value = {"result": {"type": "agent_prompted", "agent": _agent("w2:p2")}}
    with pytest.raises(CapabilityError, match="^Herdr could not complete the request\\.$"):
        await herdr.HerdrCapability().invoke("prompt", _PROMPT, job)
    assert not runtime.wakes


async def test_watches_new_panes_states_and_departures(peer, runtime):
    peer.agents = [_agent()]
    async with _watch(herdr.HerdrCapability()) as report:
        await peer.subscription()
        initial = await report("w1:p1", "idle")
        assert initial == {"harness": "herdr", "session": "w1:p1", "agent": "claude", "name": "reviewer",
                           "dir": "project", "state": "idle", "detail": ""}
        _, stream = await peer.subscription("w1:p1")
        for state in ("working", "blocked", "done", "unknown"):
            peer.agents = [_agent(state=state)]
            await _send(stream, {"event": "pane.agent_status_changed",
                                 "data": {"pane_id": "w1:p1", "agent_status": "stale", "title": "Private"}})
            await report("w1:p1", state)
        peer.agents.append(_agent("w1:p2", "working"))
        await _send(stream, {"event": "pane.agent_detected", "data": {"pane_id": "w1:p2"}})
        await report("w1:p2", "working")
        _, stream = await peer.subscription("w1:p1", "w1:p2")
        peer.agents.pop(0)
        runtime.expect(["herdr:w1:p1"], "w1:p1", "conversation", "claude")
        await _send(stream, {"event": "pane.exited", "data": {"pane_id": "w1:p1"}})
        await report("w1:p1", "ended")
        assert runtime.pending(["herdr:w1:p1"]) is None
        _, stream = await peer.subscription("w1:p2")
        peer.agents.clear()
        await _send(stream, {"event": "pane.agent_detected", "data": {"pane_id": "w1:p2", "released": True}})
        await report("w1:p2", "ended")


async def test_events_lost_resubscribes_before_reconciling(peer, runtime, monkeypatch, caplog, capsys):
    pause = AsyncMock()
    monkeypatch.setattr(_harness.asyncio, "sleep", pause)
    peer.agents = [_agent()]
    async with _watch(herdr.HerdrCapability()) as report:
        await peer.subscription()
        await report("w1:p1", "idle")
        request, stream = await peer.subscription("w1:p1")
        peer.agents = [_agent("w2:p1", "blocked")]
        await _send(stream, {"id": request["id"], "error": {"code": "events_lost", "message": "Private /path"}})
        stream.close()
        await peer.subscription()
        await report("w2:p1", "blocked")
        await report("w1:p1", "ended")
        await peer.subscription("w2:p1")
    assert pause.await_args_list[0].args == (1,)
    assert "Private" not in caplog.text
    assert capsys.readouterr() == ("", "")


async def test_events_during_an_authoritative_read_trigger_another_read(peer, runtime):
    peer.agents = [_agent()]
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def list_agents():
        if peer.list.call_count == 2:
            entered.set()
            await finish.wait()
            return [_agent()]
        return peer.agents

    peer.list.side_effect = list_agents
    async with _watch(herdr.HerdrCapability()) as report:
        await peer.subscription()
        await report("w1:p1", "idle")
        _, stream = await peer.subscription("w1:p1")
        await asyncio.wait_for(entered.wait(), 2)
        peer.agents = [_agent(state="working")]
        await _send(stream, {"event": "pane.agent_status_changed",
                             "data": {"pane_id": "w1:p1", "agent_status": "blocked"}})
        finish.set()
        await report("w1:p1", "working")
        assert peer.list.call_count >= 3


async def test_discovers_agents_when_herdr_does_not_send_a_detection_event(peer, runtime, monkeypatch):
    monkeypatch.setattr(herdr, "_RECONCILE_S", 0.01)
    initial = asyncio.Event()

    async def list_agents():
        agents = peer.agents.copy()
        initial.set()
        return agents

    peer.list.side_effect = list_agents
    async with _watch(herdr.HerdrCapability()) as report:
        await peer.subscription()
        await asyncio.wait_for(initial.wait(), 2)
        peer.agents = [_agent(state="unknown")]
        await report("w1:p1", "unknown")
        await peer.subscription("w1:p1")


async def test_local_failures_back_off_and_stay_quiet(monkeypatch, caplog, capsys):
    connect = AsyncMock(side_effect=OSError("Private /path"))
    monkeypatch.setattr(herdr.asyncio, "open_unix_connection", connect)
    pauses = []

    async def sleep(delay):
        pauses.append(delay)
        if len(pauses) == 8:
            raise asyncio.CancelledError

    monkeypatch.setattr(_harness.asyncio, "sleep", sleep)
    emit = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await herdr.HerdrCapability().run(CompanionContext("device", emit))
    assert pauses == [1, 2, 4, 8, 16, 30, 30, 30]
    assert connect.await_count == 8
    emit.assert_not_called()
    assert "Private" not in caplog.text
    assert capsys.readouterr() == ("", "")
