import asyncio
import base64
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from switchboard.companion.caps import _harness, opencode
from switchboard.companion.caps.base import CapabilityError, CompanionContext, JobContext
from switchboard.companion.caps.opencode import OpenCodeCapability
from switchboard.companion.harness import Harness


SESSION = "ses_fixture"
PROMPT = {"session": SESSION, "text": "private prompt", "conversation": "thread", "agent": "agent"}


class Stream(httpx.AsyncByteStream):
    def __init__(self):
        self.queue = asyncio.Queue()

    async def __aiter__(self):
        while (chunk := await self.queue.get()) is not None:
            yield chunk

    def send(self, kind, data, *, legacy=False):
        value = {"type": kind, "properties" if legacy else "data": {"sessionID": SESSION, **data}}
        self.queue.put_nowait(f"data: {json.dumps(value)}\n\n".encode())

    def close(self):
        self.queue.put_nowait(None)


class Server:
    def __init__(self, dialect):
        self.legacy = dialect == "legacy"
        prefix = "" if self.legacy else "/api"
        self.sessions = f"{prefix}/session"
        self.one = f"{self.sessions}/{SESSION}"
        template = f"{self.sessions}/{{sessionID}}"
        end = "prompt_async" if self.legacy else "prompt"
        approve = "/permission" if self.legacy else f"{template}/permission"
        form = "/question" if self.legacy else f"{template}/form"
        self.event = f"{prefix}/event"
        self.prompt = f"{self.one}/{end}"
        self.messages = f"{self.one}/message"
        self.wait = f"{self.one}/wait"
        self.approve = approve.replace("{sessionID}", SESSION)
        self.form = form.replace("{sessionID}", SESSION)
        self.permissions = "/permission" if self.legacy else "/api/permission/request"
        self.forms = "/question" if self.legacy else "/api/form"
        self.has_more = False
        self.listed = True
        self.status = f"{self.sessions}/status"
        self.paths = {
            self.sessions: {"get": {}},
            template: {"get": {}},
            self.event: {"get": {}},
            f"{template}/{end}": {"post": {"requestBody": {
                "content": {"application/json": {"schema": {"properties": {dialect: {}}}}},
            }}},
            f"{template}/message": {"get": {}},
            self.status: {"get": {}},
            approve: {"get": {}},
            form: {"get": {}},
            self.permissions: {"get": {}},
            self.forms: {"get": {}},
        }
        if dialect == "prompt":
            self.paths[f"{template}/wait"] = {"post": {}}
        self.info = {"id": SESSION, "title": "private prompt in generated title", "time": {}}
        directory = {"directory": "/private/project/folder/"}
        self.info.update(directory if self.legacy else {"location": directory})
        self.pending = {self.approve: [], self.form: [], self.permissions: [], self.forms: []}
        self.streams = []
        self.requests = []
        self.history = []
        self.posts = []
        self.post_status = 204 if self.legacy else 200
        self.before_ack = None
        self.wait_started = asyncio.Event()
        self.wait_finished = asyncio.Event()
        self.wait_cancelled = asyncio.Event()

    def wrap(self, value):
        return value if self.legacy else {"data": value}

    async def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/doc":
            return httpx.Response(200, json={"openapi": "3.1.0", "paths": self.paths})
        if path == self.sessions:
            if request.url.params.get("cursor"):
                return httpx.Response(200, json={"data": [{**self.info, "id": "ses_page2"}]})
            result = self.wrap([self.info] if self.listed else [])
            if self.has_more:
                result["cursor"] = {"next": "opaque-cursor"}
            return httpx.Response(200, json=result)
        if path == self.one:
            return httpx.Response(200, json=self.wrap(self.info))
        if path == self.status:
            return httpx.Response(200, json={SESSION: {"type": "busy"}})
        if path in self.pending:
            return httpx.Response(200, json=self.wrap(self.pending[path]))
        if path == self.messages:
            return httpx.Response(200, json=self.wrap(self.history))
        if path == self.wait and request.method == "POST":
            self.wait_started.set()
            try:
                await self.wait_finished.wait()
            except asyncio.CancelledError:
                self.wait_cancelled.set()
                raise
            return httpx.Response(204)
        if path == self.prompt and request.method == "POST":
            body = json.loads(request.content)
            self.posts.append(body)
            if self.before_ack:
                await self.before_ack()
            if self.post_status == 204:
                return httpx.Response(204)
            return httpx.Response(self.post_status, json={"data": {"id": body.get("id"), "text": PROMPT["text"]}})
        if path == self.event:
            stream = Stream()
            self.streams.append(stream)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        raise AssertionError(f"Unexpected OpenCode API request {path}")

    def messages_for(self, message_id, *, include_user=True):
        parts = [
            {"type": "text", "text": "only the reply"},
            {"type": "reasoning", "text": "private chain of thought"},
            {"type": "tool", "text": "/private/project/folder/output"},
            {"type": "text", "text": "private synthetic text", "synthetic": True},
        ]
        if self.legacy:
            items = [{"info": {"id": "msg_answer", "role": "assistant", "parentID": message_id}, "parts": parts}]
            if include_user:
                items.insert(0, {"info": {"id": message_id, "role": "user"},
                                 "parts": [{"type": "text", "text": PROMPT["text"]}]})
        else:
            items = [{"id": "msg_answer", "type": "assistant", "content": parts}]
            if include_user:
                items.append({"id": message_id, "type": "user", "text": PROMPT["text"]})
        return items


@pytest.fixture
def context():
    return JobContext("job", AsyncMock(), AsyncMock())


@pytest.fixture
def fixture(monkeypatch):
    monitor = Harness()
    monkeypatch.setattr(opencode, "harness", lambda: monitor)

    def start(dialect="text"):
        monkeypatch.setenv("SWB_OPENCODE_URL", "http://127.0.0.1")
        server = Server(dialect)
        transport = httpx.MockTransport(server.handle)
        client = httpx.AsyncClient
        monkeypatch.setattr(opencode.httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
        return OpenCodeCapability(), server, monitor

    return start


@asynccontextmanager
async def running(cap):
    events = asyncio.Queue()

    async def emit(name, payload):
        events.put_nowait((name, payload))
        return {}

    task = asyncio.create_task(cap.run(CompanionContext("companion", emit)))
    try:
        async with asyncio.timeout(3):
            yield events
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_explicit_opt_in_and_url_validation(monkeypatch):
    monkeypatch.delenv("SWB_OPENCODE_URL", raising=False)
    assert OpenCodeCapability().available() == "Set SWB_OPENCODE_URL to use OpenCode."
    for url in (
        "ftp://localhost", "http://u:p@localhost", "http://@localhost", "http://localhost?x", "http://localhost#",
        "http://", "http://opencode.test", "http://192.0.2.1", "http://[2001:db8::1]",
    ):
        monkeypatch.setenv("SWB_OPENCODE_URL", url)
        assert OpenCodeCapability().available() == "Use a loopback HTTP or HTTPS URL for OpenCode."
    for url in ("http://localhost:8080", "http://127.0.0.1", "https://[::1]:8080"):
        monkeypatch.setenv("SWB_OPENCODE_URL", url)
        assert OpenCodeCapability().available() is None


@pytest.mark.parametrize("dialect", ["legacy", "text", "prompt"])
async def test_discovers_routes_and_replies_only_for_matching_wake(fixture, context, monkeypatch, dialect, caplog):
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "fixture-only-password")
    cap, server, monitor = fixture(dialect)
    assert (await cap.invoke("list", {}, context))["sessions"] == [{
        "harness": "opencode", "session": SESSION, "agent": "opencode", "name": "", "dir": "folder",
        "state": "unknown", "detail": "",
    }]
    async with running(cap) as events:
        assert (await events.get())[1]["state"] == "working"
        stream = server.streams[0]
        stream.send("session.idle", {}, legacy=server.legacy)
        assert (await events.get())[1]["state"] == "idle"
        assert not any(request.url.path == server.messages for request in server.requests)

        assert await cap.invoke("prompt", PROMPT, context) == {"queued": True}
        assert monitor.pending([f"opencode:{SESSION}"]).conversation == "thread"
        body = server.posts[0]
        message_id = body.get("id", body.get("messageID"))
        assert message_id.startswith("msg_")
        if dialect == "legacy":
            assert body["parts"] == [{"type": "text", "text": PROMPT["text"]}]
        else:
            assert body["delivery"] == "queue"
            assert (body["prompt"] if dialect == "prompt" else body)["text"] == PROMPT["text"]
        assert "agent" not in body and "conversation" not in body
        with pytest.raises(CapabilityError, match="pending OpenCode reply"):
            await cap.invoke("prompt", {**PROMPT, "conversation": "second"}, context)

        server.history = server.messages_for("msg_unrelated")
        stream.send("session.idle", {}, legacy=server.legacy)
        stream.send("session.status", {"status": {"type": "busy"}}, legacy=server.legacy)
        assert (await events.get())[1]["state"] == "working"
        assert events.empty()

        server.history = server.messages_for(message_id)
        stream.send("session.status", {"status": {"type": "idle"}}, legacy=server.legacy)
        assert (await events.get())[1]["state"] == "idle"
        assert await events.get() == ("opencode.reply", {
            "session": SESSION, "conversation": "thread", "agent": "agent", "text": "only the reply",
        })
        stream.send("session.idle", {}, legacy=server.legacy)
        stream.send("session.status", {"status": {"type": "retry", "message": "private detail"}}, legacy=server.legacy)
        assert (await events.get())[1]["state"] == "working"
        assert events.empty()

    assert sum(request.url.path == server.messages for request in server.requests) == 2
    assert all(request.headers["authorization"] == "Basic " + base64.b64encode(
        b"opencode:fixture-only-password",
    ).decode() for request in server.requests)
    assert PROMPT["text"] not in caplog.text and "fixture-only-password" not in caplog.text


async def test_bootstraps_pending_and_clears_only_resolved_requests(fixture, context):
    cap, server, _ = fixture()
    server.pending[server.permissions] = [{"id": "per_1", "sessionID": SESSION, "message": "private"}]
    async with running(cap) as events:
        assert (await events.get())[1]["state"] == "blocked"
        stream = server.streams[0]
        stream.send("form.created", {"form": {"sessionID": SESSION, "id": "form_1", "title": "private"}})
        stream.send("permission.replied", {"requestID": "per_1"})
        stream.send("session.status", {"status": {"type": "idle"}})
        stream.send("form.cancelled", {"id": "form_1"})
        assert (await events.get())[1]["state"] == "idle"

        server.pending[server.form] = [{"id": "form_2"}]
        with pytest.raises(CapabilityError, match="Resolve the pending approval"):
            await cap.invoke("prompt", PROMPT, context)
        assert server.posts == []
        server.pending[server.form] = []
        stream.send("permission.asked", {"id": "per_2", "resources": ["/private"]})
        assert (await events.get())[1]["state"] == "blocked"
        with pytest.raises(CapabilityError, match="Resolve the pending approval"):
            await cap.invoke("prompt", PROMPT, context)
        assert server.posts == []
        stream.send("permission.replied", {"requestID": "per_2"})
        assert (await events.get())[1]["state"] == "idle"
        assert await cap.invoke("prompt", PROMPT, context) == {"queued": True}


async def test_idle_arriving_before_prompt_ack_still_uses_new_wake(fixture, context):
    cap, server, _ = fixture()
    async with running(cap) as events:
        await events.get()

        async def before_ack():
            server.history = server.messages_for(server.posts[0]["id"])
            server.streams[0].send("session.idle", {})
            assert (await events.get())[1]["state"] == "idle"

        server.before_ack = before_ack
        assert await cap.invoke("prompt", PROMPT, context) == {"queued": True}
        assert (await events.get())[0] == "opencode.reply"


@pytest.mark.parametrize("finish", [True, False])
async def test_installed_v2_wait_only_for_our_prompt_and_cancels_on_stop(fixture, context, finish):
    cap, server, _ = fixture("prompt")
    async with running(cap) as events:
        await events.get()
        stream = server.streams[0]
        stream.send("session.next.prompted", {"messageID": "msg_manual", "prompt": "private"})
        stream.send("session.status", {"status": {"type": "idle"}})
        assert (await events.get())[1]["state"] == "idle"
        assert not server.wait_started.is_set()

        assert await cap.invoke("prompt", PROMPT, context) == {"queued": True}
        message_id = server.posts[0]["id"]
        stream.send("session.next.step.started", {})
        assert (await events.get())[1]["state"] == "working"
        stream.send("session.next.prompted", {"messageID": message_id})
        await server.wait_started.wait()
        assert not any(request.url.path == server.messages for request in server.requests)

        stream.send("question.v2.asked", {"id": "que_pending"})
        assert (await events.get())[1]["state"] == "blocked"
        stream.send("question.v2.replied", {"requestID": "que_pending"})
        assert (await events.get())[1]["state"] == "working"
        if finish:
            server.history = server.messages_for(message_id)
            server.wait_finished.set()
            assert (await events.get())[1]["state"] == "idle"
            assert (await events.get())[0] == "opencode.reply"
    assert server.wait_cancelled.is_set() != finish


async def test_discovery_paginates_and_does_not_retry_by_guessing(fixture, context):
    cap, server, _ = fixture()
    server.has_more = True
    assert [item["session"] for item in (await cap.invoke("list", {}, context))["sessions"]] == [SESSION, "ses_page2"]
    assert server.requests[-1].url.params["cursor"] == "opaque-cursor"
    del server.paths[server.event]
    server.requests.clear()
    with pytest.raises(CapabilityError, match="^OpenCode API routes are unavailable\\.$"):
        await cap.invoke("list", {}, context)
    assert [request.url.path for request in server.requests] == ["/doc"]


async def test_prompt_failures_are_fixed_and_never_create_a_wake(fixture, context):
    cap, server, monitor = fixture()
    for args in (
        {**PROMPT, "text": "x" * 60_001},
        {**PROMPT, "conversation": 3},
        {**PROMPT, "session": "../../private"},
        {**PROMPT, "extra": "private"},
    ):
        with pytest.raises(CapabilityError) as error:
            await cap.invoke("prompt", args, context)
        assert "private" not in str(error.value)
    assert not server.requests
    server.info["time"]["archived"] = 1
    with pytest.raises(CapabilityError, match="session is unavailable"):
        await cap.invoke("prompt", PROMPT, context)
    assert not server.posts
    server.info["time"].clear()
    server.post_status = 502
    with pytest.raises(CapabilityError, match="^OpenCode did not respond as expected\\.$"):
        await cap.invoke("prompt", PROMPT, context)
    assert not monitor.pending([f"opencode:{SESSION}"])
    del server.paths["/api/session/{sessionID}/form"]
    with pytest.raises(CapabilityError, match="OpenCode API routes are unavailable"):
        await cap.invoke("prompt", PROMPT, context)
    assert len(server.posts) == 1


async def test_reconnect_rediscovers_and_caps_retry(fixture, monkeypatch):
    cap, server, _ = fixture()
    delays = []
    sleep = asyncio.sleep

    async def pause(delay):
        delays.append(delay)
        if len(delays) == 9:
            raise asyncio.CancelledError()

    monkeypatch.setattr(_harness.asyncio, "sleep", pause)
    async with running(cap) as events:
        assert (await events.get())[1]["state"] == "working"
        server.streams[0].queue.put_nowait(b"data: not JSON\n\n")
        server.streams[0].close()
        del server.paths[server.event]
        while len(delays) < 9:
            await sleep(0)
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30, 30]
    assert sum(request.url.path == "/doc" for request in server.requests) == 9
    assert cap._routes is None


async def test_reconnect_reports_sessions_removed_while_offline(fixture, context, monkeypatch):
    cap, server, monitor = fixture()
    monkeypatch.setattr(_harness.asyncio, "sleep", AsyncMock())
    async with running(cap) as events:
        assert (await events.get())[1]["state"] == "working"
        assert await cap.invoke("prompt", PROMPT, context) == {"queued": True}
        server.listed = False
        server.streams[0].close()
        kind, payload = await events.get()
        assert kind == "opencode.session"
        assert (payload["session"], payload["state"]) == (SESSION, "ended")
        assert not monitor.pending([f"opencode:{SESSION}"])
        assert len(server.streams) == 2
