import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from switchboard.companion.caps import codex
from switchboard.companion.caps.base import CapabilityError, CompanionContext, JobContext
from switchboard.companion.harness import Harness

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Unix app-server sockets require POSIX.")

INPUT = {"session": "thread-1", "text": "Quoted 'text'\nwith SwB reply note", "conversation": "c-1", "agent": "agent-1"}
IDLE = {"type": "idle"}
ACTIVE = {"type": "active", "activeFlags": []}
BLOCKED = {"type": "active", "activeFlags": ["waitingOnApproval"]}


class FakeCodex:
    def __init__(self):
        self.threads = {}
        self.turns = {}
        self.items = {}
        self.frames = []
        self.peers = []
        self.page_size = None
        self.deliver = AsyncMock()

    def thread(self, thread, status=IDLE):
        self.threads[thread] = {
            "id": thread,
            "status": status,
            "cwd": "/home/owner/private/project/",
            "name": "private generated title",
            "preview": "private prompt",
            "path": "/home/owner/private/rollout.jsonl",
            "historyMode": "paginated",
        }

    async def event(self, method, **params):
        await self.peers[-1].send_json({"method": method, "params": params})

    async def socket(self, request):
        peer = web.WebSocketResponse()
        await peer.prepare(request)
        self.peers.append(peer)
        async for frame in peer:
            message = json.loads(frame.data)
            self.frames.append(message)
            method, params = message["method"], message.get("params", {})
            if method == "initialized":
                assert "id" not in message
                continue
            if method == "initialize":
                assert params["clientInfo"]["name"] == "swb"
                assert "capabilities" not in params
                result = {"userAgent": "mock"}
            elif method == "thread/loaded/list":
                ids = list(self.threads)
                if self.page_size and "cursor" not in params:
                    result = {"data": ids[:self.page_size], "nextCursor": "ids-next"}
                elif self.page_size:
                    assert params == {"cursor": "ids-next"}
                    result = {"data": ids[self.page_size:], "nextCursor": None}
                else:
                    assert params == {}
                    result = {"data": ids, "nextCursor": None}
            elif method == "thread/read":
                assert params["includeTurns"] is False
                result = {"thread": self.threads[params["threadId"]]}
            elif method == "thread/turns/list":
                data = self.turns.get(params["threadId"], [])
                cursor = params.get("cursor")
                if cursor is not None:
                    mode, turn_id = cursor.split(":", 1)
                    offset = next(index for index, turn in enumerate(data) if turn["id"] == turn_id)
                    if mode == "anchor":
                        assert params["sortDirection"] == "asc"
                        data = list(reversed(data[:offset + 1]))
                    else:
                        data = data[offset + 1:]
                limit = params["limit"]
                next_cursor = f"next:{data[limit - 1]['id']}" if len(data) > limit else None
                data = data[:limit]
                if params["itemsView"] == "notLoaded":
                    data = [{key: value for key, value in turn.items() if key != "items"} for turn in data]
                result = {"data": data, "nextCursor": next_cursor,
                          "backwardsCursor": f"anchor:{data[0]['id']}" if data else None}
            elif method == "thread/items/list":
                assert params["sortDirection"] == "desc"
                key = (params["threadId"], params["turnId"])
                result = {"data": self.items[key], "nextCursor": None}
            elif method in ("turn/start", "turn/steer"):
                result = await self.deliver(method, params)
                if result is None:
                    await peer.send_json({"id": message["id"], "error": {
                        "code": -32600, "message": "/private/path and private prompt"}})
                    continue
            else:
                pytest.fail(f"Unexpected app-server method {method!r}")
            await peer.send_json({"id": message["id"], "result": result})
        return peer

    @asynccontextmanager
    async def listen(self, path: Path):
        app = web.Application()
        app.router.add_get("/rpc", self.socket)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.UnixSite(runner, str(path)).start()
            yield
        finally:
            for peer in self.peers:
                await peer.close()
            await runner.cleanup()


@pytest.fixture
def wake_book(monkeypatch):
    book = Harness()
    monkeypatch.setattr(codex, "harness", lambda: book)
    monkeypatch.setattr(codex, "_POLL_S", 0.04)
    return book


async def eventually(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(0.005)


@asynccontextmanager
async def connected(fake, tmp_path, monkeypatch):
    path = tmp_path / "c.sock"
    monkeypatch.setenv("SWB_CODEX_SOCKET", str(path))
    capability = codex.CodexCapability()
    assert not path.exists() and capability.available() is None
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))
        return {}

    async with fake.listen(path):
        assert capability.available() is None
        task = asyncio.create_task(capability.run(CompanionContext("device", emit)))
        try:
            await eventually(lambda: capability._rpc is not None and any(
                frame["method"] == "thread/loaded/list" for frame in fake.frames))
            yield capability, events, JobContext("job", AsyncMock(), AsyncMock())
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def test_socket_uses_codex_home_and_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("SWB_CODEX_SOCKET", raising=False)
    suffix = Path("app-server-control/app-server-control.sock")
    assert codex.CodexCapability().path == tmp_path / ".codex" / suffix
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "alternate"))
    assert codex.CodexCapability().path == tmp_path / "alternate" / suffix
    monkeypatch.setenv("SWB_CODEX_SOCKET", "~/override.sock")
    assert codex.CodexCapability().path == tmp_path / "override.sock"


def test_availability_accepts_installed_cli_and_rejects_invalid_socket(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("SWB_CODEX_SOCKET", raising=False)
    monkeypatch.setattr(codex.shutil, "which", lambda name: None)
    assert codex.CodexCapability().available() == "Codex app-server is unavailable."
    monkeypatch.setattr(codex.shutil, "which", lambda name: "/bin/codex")
    assert codex.CodexCapability().available() is None
    monkeypatch.setenv("SWB_CODEX_SOCKET", "~swb-nonexistent-user/c.sock")
    assert codex.CodexCapability().available() == "Codex app-server is unavailable."


async def test_lists_loaded_sessions_and_reports_status_without_content(tmp_path, monkeypatch, wake_book):
    fake = FakeCodex()
    fake.thread("thread-1")
    fake.thread("thread-2", ACTIVE)
    fake.thread("thread-3", BLOCKED)
    fake.thread("thread-4", {"type": "active", "activeFlags": ["waitingOnUserInput"]})
    fake.thread("thread-5", {"type": "notLoaded"})
    fake.page_size = 2
    async with connected(fake, tmp_path, monkeypatch) as (capability, events, ctx):
        result = await capability.invoke("list", {}, ctx)
        assert [(s["session"], s["state"]) for s in result["sessions"]] == [
            ("thread-1", "idle"), ("thread-2", "working"), ("thread-3", "blocked"), ("thread-4", "blocked"),
        ]
        assert all(s["dir"] == "project" and s["name"] == "" and s["agent"] == "codex" for s in result["sessions"])
        await eventually(lambda: len(events) == 4)
        fake.threads["thread-2"]["status"] = BLOCKED
        await fake.event("thread/status/changed", threadId="thread-2", status=BLOCKED)
        await eventually(lambda: any(p["session"] == "thread-2" and p["state"] == "blocked" for _, p in events))
        del fake.threads["thread-2"]
        await fake.event("thread/closed", threadId="thread-2")
        await eventually(lambda: any(p["session"] == "thread-2" and p["state"] == "ended" for _, p in events))
        assert {kind for kind, _ in events} == {"codex.session"}
        assert "private" not in json.dumps([result, events])
        assert sum(p["state"] == "ended" for _, p in events) == 1
        assert set(frame["method"] for frame in fake.frames) == {
            "initialize", "initialized", "thread/loaded/list", "thread/read",
        }


async def test_idle_prompt_relays_only_final_answer_to_acknowledged_wake(tmp_path, monkeypatch, wake_book):
    fake = FakeCodex()
    fake.thread("thread-1")
    fake.thread("thread-2", ACTIVE)
    permission = {"method": "item/commandExecution/requestApproval", "id": 7, "params": {"threadId": "thread-1"}}

    async def deliver(method, params):
        assert method == "turn/start"
        assert params == {"threadId": "thread-1", "input": [{
            "type": "text", "text": INPUT["text"], "text_elements": []}]}
        assert not wake_book.wakes
        await fake.peers[-1].send_json(permission)
        await fake.event("turn/completed", threadId="thread-1", turn={
            "id": "wrong-turn", "status": "completed", "items": [{"type": "agentMessage", "text": "foreign turn"}]})
        for item in (
            {"type": "userMessage", "text": "private user message"},
            {"type": "agentMessage", "phase": "commentary", "text": "private commentary"},
            {"type": "agentMessage", "phase": "partial_answer", "text": "private partial answer"},
            {"type": "agentMessage", "phase": "final_answer", "text": "the answer"},
        ):
            await fake.event("item/completed", threadId="thread-1", turnId="woken", item=item)
        await fake.event("turn/completed", threadId="thread-1", turn={"id": "woken", "status": "completed", "items": []})
        return {"turn": {"id": "woken"}}

    fake.deliver.side_effect = deliver
    async with connected(fake, tmp_path, monkeypatch) as (capability, events, ctx):
        await fake.event("turn/completed", threadId="thread-2", turn={
            "id": "woken", "status": "completed", "items": [{"type": "agentMessage", "text": "private other thread"}]})
        assert await capability.invoke("prompt", INPUT, ctx) == {"delivered": True}
        await eventually(lambda: any(kind == "codex.reply" for kind, _ in events))
        await fake.event("turn/completed", threadId="thread-1", turn={
            "id": "woken", "status": "completed", "items": [{"type": "agentMessage", "text": "private duplicate"}]})
        await eventually(lambda: capability._rpc is not None and capability._rpc.events.empty())
        assert [payload for kind, payload in events if kind == "codex.reply"] == [
            {"session": "thread-1", "conversation": "c-1", "agent": "agent-1", "text": "the answer"},
        ]
        assert not wake_book.wakes
        assert "private" not in json.dumps(events)
        assert not any("method" not in frame for frame in fake.frames)
        assert not any(frame["method"] == "thread/items/list" for frame in fake.frames)


async def test_steer_polls_exact_accepted_turn_without_subscription(tmp_path, monkeypatch, wake_book):
    fake = FakeCodex()
    fake.thread("thread-1", ACTIVE)
    fake.turns["thread-1"] = [{"id": "active", "status": "inProgress"}]
    fake.deliver.return_value = {"turnId": "active"}
    async with connected(fake, tmp_path, monkeypatch) as (capability, events, ctx):
        assert await capability.invoke("prompt", INPUT, ctx) == {"delivered": True}
        fake.deliver.assert_awaited_once_with("turn/steer", {
            "threadId": "thread-1", "expectedTurnId": "active",
            "input": [{"type": "text", "text": INPUT["text"], "text_elements": []}],
        })
        with pytest.raises(CapabilityError, match="pending wake"):
            await capability.invoke("prompt", {**INPUT, "conversation": "would-clobber"}, ctx)
        fake.items["thread-1", "active"] = [
            {"turnId": "other", "item": {"type": "agentMessage", "phase": "final_answer", "text": "private other turn"}},
            {"turnId": "active", "item": {"type": "agentMessage", "phase": "final_answer", "text": "polled answer"}},
        ]
        fake.turns["thread-1"] = [{"id": "other", "status": "inProgress"}, {"id": "active", "status": "completed"}]
        await eventually(lambda: any(kind == "codex.reply" for kind, _ in events))
        assert [p["text"] for kind, p in events if kind == "codex.reply"] == ["polled answer"]
        assert all(frame["params"]["turnId"] == "active" for frame in fake.frames
                   if frame["method"] == "thread/items/list")
        assert "private" not in json.dumps(events)
        assert not {"thread/resume", "thread/start", "thread/fork", "thread/unsubscribe"}.intersection(
            frame["method"] for frame in fake.frames)


async def test_legacy_history_reads_full_items_only_at_the_woken_turn(tmp_path, monkeypatch, wake_book):
    fake = FakeCodex()
    fake.thread("thread-1")
    fake.threads["thread-1"]["historyMode"] = "legacy"
    fake.deliver.return_value = {"turn": {"id": "woken"}}
    async with connected(fake, tmp_path, monkeypatch) as (capability, events, ctx):
        await capability.invoke("prompt", INPUT, ctx)
        fake.turns["thread-1"] = [
            {"id": "other", "status": "inProgress", "items": [{"type": "agentMessage", "text": "private other"}]},
            {"id": "woken", "status": "completed", "items": [{"type": "agentMessage", "text": "legacy answer"}]},
        ]
        await eventually(lambda: any(kind == "codex.reply" for kind, _ in events))
        assert [p["text"] for kind, p in events if kind == "codex.reply"] == ["legacy answer"]
        full = [frame["params"] for frame in fake.frames
                if frame["method"] == "thread/turns/list" and frame["params"]["itemsView"] == "full"]
        assert full == [{"threadId": "thread-1", "limit": 1, "itemsView": "full",
                         "cursor": "anchor:woken", "sortDirection": "asc"}]


async def test_fixed_errors_validate_and_never_retry_turn_on_steer_rejection(tmp_path, monkeypatch, wake_book):
    fake = FakeCodex()
    fake.thread("thread-1", ACTIVE)
    fake.thread("thread-2", BLOCKED)
    fake.thread("thread-3", {"type": "notLoaded"})
    fake.turns["thread-1"] = [{"id": "old", "status": "completed"}]
    fake.deliver.return_value = None
    async with connected(fake, tmp_path, monkeypatch) as (capability, _, ctx):
        for args in ({}, {**INPUT, "text": "x" * 60001}, {**INPUT, "text": 10}, {**INPUT, "extra": ""}):
            with pytest.raises(CapabilityError):
                await capability.invoke("prompt", args, ctx)
        with pytest.raises(CapabilityError, match="no arguments"):
            await capability.invoke("list", {"extra": True}, ctx)
        with pytest.raises(CapabilityError, match="Choose a loaded Codex thread"):
            await capability.invoke("prompt", {**INPUT, "session": "unloaded-private-thread"}, ctx)
        with pytest.raises(CapabilityError, match="Choose a loaded Codex thread"):
            await capability.invoke("prompt", {**INPUT, "session": "thread-3"}, ctx)
        with pytest.raises(CapabilityError, match="is blocked"):
            await capability.invoke("prompt", {**INPUT, "session": "thread-2"}, ctx)
        with pytest.raises(CapabilityError, match="active Codex turn is unavailable"):
            await capability.invoke("prompt", INPUT, ctx)
        fake.deliver.assert_not_called()
        fake.turns["thread-1"] = [{"id": "now-active", "status": "inProgress"}]
        with pytest.raises(CapabilityError, match=r"^Codex rejected the request\.$"):
            await capability.invoke("prompt", INPUT, ctx)
        fake.deliver.assert_awaited_once()
        assert fake.deliver.call_args.args[0] == "turn/steer"
        assert not wake_book.wakes


async def test_rejects_private_paths_as_thread_ids(tmp_path, monkeypatch, wake_book):
    fake = FakeCodex()
    async with connected(fake, tmp_path, monkeypatch) as (capability, events, ctx):
        fake.thread("/home/owner/private/thread.jsonl")
        with pytest.raises(CapabilityError, match=r"^Codex rejected the request\.$"):
            await capability.invoke("list", {}, ctx)
        assert events == []
        assert not any(frame["method"] == "thread/read" for frame in fake.frames)


async def test_interrupted_turn_discards_its_answer_and_releases_wake(tmp_path, monkeypatch, wake_book):
    fake = FakeCodex()
    fake.thread("thread-1")
    fake.deliver.return_value = {"turn": {"id": "woken"}}
    async with connected(fake, tmp_path, monkeypatch) as (capability, events, ctx):
        await capability.invoke("prompt", INPUT, ctx)
        await fake.event("item/completed", threadId="thread-1", turnId="woken",
                         item={"type": "agentMessage", "text": "private interrupted output"})
        await fake.event("turn/completed", threadId="thread-1", turn={"id": "woken", "status": "interrupted"})
        await eventually(lambda: not wake_book.wakes)
        assert not any(kind == "codex.reply" for kind, _ in events)
        assert await capability.invoke("prompt", {**INPUT, "conversation": "next"}, ctx) == {"delivered": True}


async def test_reconnect_preserves_wake_and_reconciles_closed_thread(tmp_path, monkeypatch, wake_book):
    fake = FakeCodex()
    fake.thread("thread-1")
    fake.deliver.return_value = {"turn": {"id": "woken"}}
    async with connected(fake, tmp_path, monkeypatch) as (capability, events, ctx):
        await eventually(lambda: any(kind == "codex.session" for kind, _ in events))
        await capability.invoke("prompt", INPUT, ctx)
        old = fake.peers[-1]
        await old.close()
        await eventually(lambda: capability._rpc is None)
        with pytest.raises(CapabilityError, match=r"^Codex app-server is unavailable\.$"):
            await capability.invoke("list", {}, ctx)
        assert not any(p.get("state") == "ended" for _, p in events)
        del fake.threads["thread-1"]
        fake.thread("thread-2", ACTIVE)
        fake.items["thread-1", "woken"] = [
            {"turnId": "woken", "item": {"type": "agentMessage", "text": "answer after reconnect"}},
        ]
        fake.turns["thread-1"] = [{"id": "woken", "status": "completed"}]
        await eventually(lambda: any(kind == "codex.reply" for kind, _ in events))
        assert [p["text"] for kind, p in events if kind == "codex.reply"] == ["answer after reconnect"]
        assert [p["state"] for kind, p in events if kind == "codex.session" and p["session"] == "thread-1"] == [
            "idle", "ended",
        ]
        assert len(fake.peers) == 2
        assert not wake_book.wakes
