import asyncio
import json
import os
import stat
import sys
from unittest.mock import AsyncMock

import httpx
import pytest

from switchboard import cli
from switchboard.companion import client
from switchboard.companion.caps.base import ActionSpec, Capability
from switchboard.companion.state import CompanionError, CompanionState, state_path


URL = "https://swb.test"
CODE = "test-pair-code"
KEY = "swbd_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
COMPANION_ID = "cmp_0123456789abcdef01234567"
JOB_ID = "cj_0123456789abcdef0123456789abcdef"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SWB_COMPANION_HOME", str(tmp_path / "settings"))
    return state_path()


@pytest.fixture
def paired(settings, tmp_path):
    state = CompanionState(URL, COMPANION_ID, KEY, ("drop",), str(tmp_path / "drop"))
    state.save(settings)
    return state


def mock_client(monkeypatch, handler):
    factory = client._client

    def make(url, key=None):
        connection = factory(url, key)
        connection._transport = httpx.MockTransport(handler)
        return connection

    monkeypatch.setattr(client, "_client", make)


class Stream(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, asyncio.Event):
                await chunk.wait()
            else:
                yield chunk


def event_stream(*chunks):
    return httpx.Response(200, headers={"Content-Type": "text/event-stream; charset=utf-8"}, stream=Stream(*chunks))


def job_event():
    return (f'event: job\r\ndata: {{"job_id":"{JOB_ID}","capability":"test",\r\n'
            'data: "action":"go","args":{"value":2}}\r\n\r\n').encode()


def test_connect_pairs_with_hidden_code_and_persists_options(settings, tmp_path, monkeypatch, capsys):
    prompts = []
    posts = []

    def hidden(prompt):
        prompts.append(prompt)
        return CODE

    async def handler(request):
        posts.append(request)
        return httpx.Response(200, json={"companion_id": COMPANION_ID, "device_key": KEY})

    monkeypatch.setattr(cli.getpass, "getpass", hidden)
    mock_client(monkeypatch, handler)
    drop = tmp_path / "my drop folder"
    cli.main(["companion", "connect", URL + "/", "--no-run", "--cap", "drop", "--drop-dir", str(drop)])

    assert prompts == ["Pairing code: "]
    [request] = posts
    assert str(request.url) == URL + "/companion/pair"
    assert "authorization" not in request.headers
    payload = json.loads(request.content)
    assert payload["code"] == CODE and payload["name"] and payload["platform"]
    assert [cap["id"] for cap in payload["capabilities"]] == ["drop"]
    state = CompanionState.load(settings)
    assert state == CompanionState(URL, COMPANION_ID, KEY, ("drop",), str(drop))
    assert KEY not in repr(state)
    if os.name != "nt":
        assert stat.S_IMODE(settings.stat().st_mode) == 0o600
        assert stat.S_IMODE(settings.parent.stat().st_mode) == 0o700
    cli.main(["companion", "status"])
    out, err = capsys.readouterr()
    assert f"This device is paired as {COMPANION_ID}." in out and "Drop folder" in out
    assert not err and KEY not in out and CODE not in out and URL not in out


def test_connect_runs_and_restart_reuses_enabled_caps(settings, tmp_path, monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(client, "run", run)

    async def handler(request):
        assert json.loads(request.content)["code"] == CODE
        return httpx.Response(201, json={"companion_id": COMPANION_ID, "device_key": KEY})

    mock_client(monkeypatch, handler)
    cli.main(["companion", "connect", URL, "--code", CODE, "--cap", "drop", "--cap", "drop",
              "--drop-dir", str(tmp_path / "my drop folder")])
    cli.main(["companion", "run"])
    assert run.await_count == 2
    for call in run.await_args_list:
        state, path, caps = call.args
        assert state.enabled_caps == ("drop",) and path == settings
        assert list(caps) == ["drop"] and caps["drop"].path == tmp_path / "my drop folder"


def test_connect_requires_opt_in_to_advertise_capabilities(settings, monkeypatch, capsys):
    async def handler(request):
        assert json.loads(request.content)["capabilities"] == []
        return httpx.Response(200, json={"companion_id": COMPANION_ID, "device_key": KEY})

    mock_client(monkeypatch, handler)
    cli.main(["companion", "connect", URL, "--no-run", "--code", CODE])
    assert capsys.readouterr().out == "This device is paired.\n"
    assert CompanionState.load(settings).enabled_caps == ()
    cli.main(["companion", "status"])
    assert capsys.readouterr().out == f"This device is paired as {COMPANION_ID}.\n"


def test_connect_reports_explicitly_enabled_unavailable_capability(settings, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "switchboard.companion.caps.muse", None)
    pair = AsyncMock()
    monkeypatch.setattr(client, "pair", pair)
    with pytest.raises(SystemExit) as error:
        cli.main(["companion", "connect", URL, "--no-run", "--code", CODE, "--cap", "muse"])
    assert error.value.code == 1 and "Install swb-one[companion] to use Muse." in capsys.readouterr().err
    pair.assert_not_awaited()
    assert not settings.exists()


@pytest.mark.parametrize("value", [
    {"companion_id": "not-a-companion", "device_key": KEY},
    {"companion_id": COMPANION_ID, "device_key": "not-a-device-key"},
])
def test_connect_rejects_unexpected_credentials(settings, monkeypatch, value):
    async def handler(request):
        return httpx.Response(200, json=value)

    mock_client(monkeypatch, handler)
    with pytest.raises(SystemExit) as error:
        cli.main(["companion", "connect", URL, "--no-run", "--code", CODE])
    assert error.value.code == 1 and not settings.exists()


def test_pairing_errors_do_not_print_server_body(settings, monkeypatch, capsys):
    async def handler(request):
        return httpx.Response(401, json={"error": f"{CODE} {KEY}"})

    mock_client(monkeypatch, handler)
    with pytest.raises(SystemExit) as error:
        cli.main(["companion", "connect", URL, "--code", CODE, "--cap", "drop"])
    assert error.value.code == 1
    out, err = capsys.readouterr()
    assert "pairing code" in err and CODE not in out + err and KEY not in out + err
    assert not settings.exists()


@pytest.mark.parametrize("url", ["http://example.test", "https://user:password@swb.test", f"{URL}?code={CODE}"])
def test_connect_rejects_insecure_urls_without_echo(settings, monkeypatch, capsys, url):
    monkeypatch.setattr(client, "pair", AsyncMock())
    with pytest.raises(SystemExit) as error:
        cli.main(["companion", "connect", url, "--code", CODE, "--cap", "drop"])
    assert error.value.code == 1
    assert url not in capsys.readouterr().err
    client.pair.assert_not_awaited()
    assert not settings.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_state_rejects_symlink_and_public_file(settings, paired):
    settings.chmod(0o644)
    with pytest.raises(CompanionError):
        CompanionState.load(settings)
    previous = settings.rename(settings.with_name("previous"))
    settings.symlink_to(previous)
    with pytest.raises(CompanionError):
        CompanionState.load(settings)


@pytest.mark.parametrize("status", [200, 401])
def test_disconnect_revokes_and_clears_state(settings, paired, monkeypatch, capsys, status):
    async def handler(request):
        assert str(request.url) == URL + "/companion/revoke"
        assert request.method == "POST" and request.headers["Authorization"] == f"Bearer {KEY}"
        return httpx.Response(status)

    mock_client(monkeypatch, handler)
    cli.main(["companion", "disconnect"])
    assert capsys.readouterr().out == "This device was disconnected.\n"
    assert not settings.exists()


class Work(Capability):
    id = "test"
    title = "Test"
    version = "1"
    actions = {"go": ActionSpec("Run", {"type": "object"})}

    def __init__(self, invoke, run=None):
        self.invoke = invoke
        if run is not None:
            self.run = run


async def test_run_sends_hello_progress_claim_and_result(settings, paired, monkeypatch, capsys):
    received = []
    finished = asyncio.Event()

    async def handler(request):
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        endpoint = request.url.path.removeprefix("/companion/")
        if endpoint == "events":
            assert request.headers["Accept"] == "text/event-stream"
            return event_stream(job_event(), b"event: ping\ndata: {}\n\n", finished, b"event: revoked\ndata: {}\n\n")
        body = json.loads(request.content)
        received.append((endpoint, body))
        if endpoint == "claims":
            return httpx.Response(200, json={"claimed": True})
        if endpoint == "results" and "ok" in body:
            finished.set()
        return httpx.Response(204)

    async def invoke(action, args, ctx):
        assert action == "go" and args == {"value": 2}
        assert ctx.job_id == JOB_ID
        await ctx.progress({"stage": "working"})
        return await ctx.claim("test.claim", args)

    mock_client(monkeypatch, handler)
    await asyncio.wait_for(client.run(paired, settings, {"test": Work(invoke)}), 2)

    (hello, *action_requests) = received
    assert hello[0] == "hello"
    assert hello[1]["name"] and hello[1]["platform"] and hello[1]["version"]
    assert hello[1]["capabilities"][0]["actions"] == [{"name": "go", "description": "Run", "schema": {"type": "object"}}]
    assert action_requests == [
        ("results", {"job_id": JOB_ID, "progress": {"stage": "working"}}),
        ("claims", {"job_id": JOB_ID, "kind": "test.claim", "payload": {"value": 2}}),
        ("results", {"job_id": JOB_ID, "ok": True, "result": {"claimed": True}}),
    ]
    assert not settings.exists() and capsys.readouterr().out == "This device was disconnected.\n"


async def test_revoke_cancels_running_job(settings, paired, monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def handler(request):
        if request.url.path.endswith("/events"):
            return event_stream(job_event(), started, b"event: revoked\n\n")
        assert request.url.path.endswith("/hello")
        return httpx.Response(204)

    async def invoke(_action, _args, _ctx):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    mock_client(monkeypatch, handler)
    await asyncio.wait_for(client.run(paired, settings, {"test": Work(invoke)}), 2)

    assert cancelled.is_set() and not settings.exists()


@pytest.mark.parametrize("where", ["hello", "events", "results", "claims"])
async def test_authenticated_401_disconnects(settings, paired, monkeypatch, where):
    async def handler(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if endpoint == where:
            return httpx.Response(401)
        if endpoint == "events":
            return event_stream(job_event(), asyncio.Event())
        return httpx.Response(204)

    async def invoke(_action, _args, ctx):
        if where == "claims":
            await ctx.claim("test", {})
        return {}

    mock_client(monkeypatch, handler)
    await asyncio.wait_for(client.run(paired, settings, {"test": Work(invoke)}), 2)
    assert not settings.exists()


async def test_run_reconnects_with_bounded_backoff(settings, paired, monkeypatch):
    calls = []
    retry = asyncio.Event()
    started, cancelled = asyncio.Event(), asyncio.Event()
    hook_started, hook_cancelled = asyncio.Event(), asyncio.Event()
    second_hook_started = asyncio.Event()

    async def handler(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        calls.append(endpoint)
        if endpoint == "events":
            if retry.is_set():
                return event_stream(second_hook_started, b"event: revoked\ndata: {}\n\n")
            return event_stream(b"event: ping\ndata: {}\n\n", job_event(), started, hook_started)
        return httpx.Response(204)

    async def invoke(_action, _args, _ctx):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def hook(_ctx):
        if retry.is_set():
            second_hook_started.set()
            return
        hook_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            hook_cancelled.set()

    async def sleep(delay):
        assert cancelled.is_set() and hook_cancelled.is_set() and settings.exists()
        assert 1 <= delay <= 60
        retry.set()

    mock_client(monkeypatch, handler)
    monkeypatch.setattr(client.asyncio, "sleep", sleep)
    await asyncio.wait_for(client.run(paired, settings, {"test": Work(invoke, hook)}), 2)
    assert calls == ["hello", "events", "hello", "events"]
    assert not settings.exists()


async def test_capability_hook_can_emit_and_fail_without_ending_stream(settings, paired, monkeypatch):
    finished, job_finished = asyncio.Event(), asyncio.Event()
    received = []

    async def handler(request):
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if endpoint == "events":
            return event_stream(finished, job_event(), job_finished, b"event: revoked\n\n")
        if endpoint == "emit":
            received.append(json.loads(request.content))
            return httpx.Response(200, json={"accepted": True})
        if endpoint == "results":
            job_finished.set()
        return httpx.Response(204)

    async def hook(ctx):
        assert ctx.companion_id == COMPANION_ID
        assert await ctx.emit("test.event", {"state": "ready"}) == {"accepted": True}
        finished.set()
        raise RuntimeError("The hook stopped.")

    invoke = AsyncMock(return_value={})
    mock_client(monkeypatch, handler)
    await asyncio.wait_for(client.run(paired, settings, {"test": Work(invoke, hook)}), 2)

    assert received == [{"capability": "test", "kind": "test.event", "payload": {"state": "ready"}}]
    invoke.assert_awaited_once()
    assert not settings.exists()


async def test_emit_401_disconnects(settings, paired, monkeypatch, capsys):
    async def handler(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if endpoint == "events":
            return event_stream(asyncio.Event())
        return httpx.Response(401 if endpoint == "emit" else 204)

    async def hook(ctx):
        await ctx.emit("test.event", {})

    mock_client(monkeypatch, handler)
    await asyncio.wait_for(client.run(paired, settings, {"test": Work(AsyncMock(), hook)}), 2)
    assert not settings.exists()
    assert capsys.readouterr().out == "This device was disconnected.\n"
