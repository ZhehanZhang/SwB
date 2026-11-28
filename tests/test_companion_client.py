import asyncio
import json
import os
import stat
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from conftest import STATIC_KEY, base_config, new_core, payload
from conftest import call as call_tool
from switchboard import cli
from switchboard.app import create_app
from switchboard.companion import client
from switchboard.companion.caps.base import ActionSpec, Capability
from switchboard.companion.hook import supported
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
    monkeypatch.setattr(client.socket, "gethostname", lambda: "fixture-host")
    mock_client(monkeypatch, handler)
    drop = tmp_path / "my drop folder"
    cli.main(["companion", "connect", URL + "/", "--no-run", "--cap", "drop", "--drop-dir", str(drop)])

    assert prompts == ["Pairing code: "]
    [request] = posts
    assert str(request.url) == URL + "/companion/pair"
    assert "authorization" not in request.headers
    payload = json.loads(request.content)
    assert payload["code"] == CODE and payload["name"] == "fixture-host" and payload["platform"]
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
    assert f"Deployment {URL}" in out and "Connection offline" in out
    assert not err and KEY not in out and CODE not in out


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


@pytest.mark.skipif(not supported(), reason="Unix hook socket requires Linux or macOS")
def test_connect_persists_memory_sync_opt_out(settings, monkeypatch, capsys):
    run = AsyncMock()
    monkeypatch.setattr(client, "run", run)

    async def handler(request):
        return httpx.Response(201, json={"companion_id": COMPANION_ID, "device_key": KEY})

    mock_client(monkeypatch, handler)
    with pytest.raises(SystemExit) as help_exit:
        cli.main(["companion", "connect", "--help"])
    assert help_exit.value.code == 0 and "--no-memory-sync" in capsys.readouterr().out
    cli.main(["companion", "connect", URL, "--code", CODE, "--cap", "hooks", "--no-memory-sync"])
    cli.main(["companion", "run"])
    assert CompanionState.load(settings).memory_sync is False
    assert run.await_count == 2
    for call in run.await_args_list:
        state, _, caps = call.args
        assert not state.memory_sync and not caps["hooks"].memory_sync


def test_connect_requires_opt_in_to_advertise_capabilities(settings, monkeypatch, capsys):
    async def handler(request):
        assert json.loads(request.content)["capabilities"] == []
        return httpx.Response(200, json={"companion_id": COMPANION_ID, "device_key": KEY})

    mock_client(monkeypatch, handler)
    cli.main(["companion", "connect", URL, "--no-run", "--code", CODE])
    assert capsys.readouterr().out == "This device is paired.\n"
    assert CompanionState.load(settings).enabled_caps == ()
    cli.main(["companion", "status"])
    assert capsys.readouterr().out.splitlines() == [
        "Companion stopped", f"This device is paired as {COMPANION_ID}.", f"Deployment {URL}",
        "Connection offline",
    ]


def test_connect_persists_device_name_for_hello(settings, monkeypatch):
    async def handler(request):
        assert json.loads(request.content)["name"] == "Desk companion"
        if request.url.path.endswith("/pair"):
            return httpx.Response(200, json={"companion_id": COMPANION_ID, "device_key": KEY})
        return httpx.Response(401)

    mock_client(monkeypatch, handler)
    cli.main(["companion", "connect", URL, "--code", CODE, "--no-run", "--name", "  Desk companion  "])
    assert CompanionState.load(settings).name == "Desk companion"
    cli.main(["companion", "run"])


def test_connect_registers_distinct_device_names_on_one_host(make, tmp_path, monkeypatch):
    _, server = make(config=base_config(version=2, blocks={"mcp": {}}, public_url=URL))
    factory = client._client

    def make_client(url, key=None):
        connection = factory(url, key)
        connection._transport = httpx.ASGITransport(app=server.app)
        return connection

    monkeypatch.setattr(client, "_client", make_client)
    monkeypatch.setattr(client.socket, "gethostname", lambda: "fixture-host")
    expected = {}
    for name in ("Desk companion", "Studio companion", None):
        code = payload(call_tool(server, STATIC_KEY, "companion_code"))["code"]
        monkeypatch.setenv("SWB_COMPANION_HOME", str(tmp_path / (name or "default")))
        args = ["companion", "connect", URL, "--code", code, "--no-run"]
        cli.main(args + (["--name", name] if name else []))
        state = CompanionState.load(state_path())
        expected[state.companion_id] = name or "fixture-host"

    rows = payload(call_tool(server, STATIC_KEY, "companion_list"))["companions"]
    assert len(rows) == len(expected) == 3
    assert {row["id"] for row in rows} == set(expected)
    for row in rows:
        assert f'\n{expected[row["id"]]}\nswb-untrusted>>>' in row["name"]


@pytest.mark.parametrize("name", [" ", "a" * 121, "device\nname"])
def test_connect_validates_device_name_before_pairing(settings, monkeypatch, capsys, name):
    pair = AsyncMock()
    monkeypatch.setattr(client, "pair", pair)
    with pytest.raises(SystemExit) as error:
        cli.main(["companion", "connect", URL, "--no-run", "--code", CODE, "--name", name])
    assert error.value.code == 1 and "Enter a device name" in capsys.readouterr().err
    pair.assert_not_awaited()


def test_connect_replaces_pairing_revoked_while_stopped(settings, tmp_path, capy, monkeypatch, capsys):
    core = new_core(tmp_path, capy, config=base_config(public_url=URL))
    app = create_app(core)
    factory = client._client

    def make(url, key=None):
        connection = factory(url, key)
        connection._transport = httpx.ASGITransport(app=app)
        return connection

    def connect(code):
        cli.main(["companion", "connect", URL, "--code", code, "--no-run"])

    monkeypatch.setattr(client, "_client", make)
    owner = {"origin": "core", "source": "owner", "client": "owner"}
    connect(core.companions.mint(URL, **owner)["code"])
    first = CompanionState.load(settings)
    second_code = core.companions.mint(URL, **owner)["code"]
    with pytest.raises(SystemExit) as error:
        connect(second_code)
    assert error.value.code == 1 and CompanionState.load(settings) == first
    assert core.companions.list()[0]["last_seen"] is None
    assert core.companions.revoke(first.companion_id, **owner)
    connect(second_code)
    assert CompanionState.load(settings).companion_id != first.companion_id
    assert capsys.readouterr().out.splitlines() == [
        "This device is paired.", "This device was disconnected.", "This device is paired.",
    ]


@pytest.mark.parametrize("response", [httpx.Response(401, text="Sign in."), httpx.Response(503)])
def test_connect_preserves_pairing_when_server_does_not_confirm_revoke(settings, paired, monkeypatch, response):
    async def handler(request):
        assert request.method == "GET" and request.url.path == "/companion/hello"
        return response

    mock_client(monkeypatch, handler)
    with pytest.raises(SystemExit) as error:
        cli.main(["companion", "connect", URL, "--code", CODE, "--no-run"])
    assert error.value.code == 1
    assert CompanionState.load(settings) == paired


@pytest.mark.parametrize("with_drop", [False, True])
def test_connect_reports_linux_notification_package(settings, monkeypatch, capsys, with_drop):
    monkeypatch.setattr("switchboard.companion.caps.notify.sys.platform", "linux")
    monkeypatch.setattr("switchboard.companion.caps.notify.shutil.which", lambda program: None)

    async def handler(request):
        assert [cap["id"] for cap in json.loads(request.content)["capabilities"]] == ["drop"]
        return httpx.Response(200, json={"companion_id": COMPANION_ID, "device_key": KEY})

    mock_client(monkeypatch, handler)
    flags = ["companion", "connect", URL, "--no-run", "--code", CODE, "--cap", "notify"]
    if with_drop:
        cli.main([*flags, "--cap", "drop"])
        assert "Install libnotify-bin for desktop notifications." in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as error:
            cli.main(flags)
        assert error.value.code == 1 and "Install libnotify-bin for desktop notifications." in capsys.readouterr().err


def test_connect_rejects_removed_muse_capability(settings, monkeypatch, capsys):
    pair = AsyncMock()
    monkeypatch.setattr(client, "pair", pair)
    with pytest.raises(SystemExit) as error:
        cli.main(["companion", "connect", URL, "--no-run", "--code", CODE, "--cap", "muse"])
    assert error.value.code == 2 and "invalid choice: 'muse'" in capsys.readouterr().err
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


@pytest.mark.parametrize(("legacy", "retained"), [
    (("muse", "drop", "muse"), ("drop",)),
    (("muse",), ()),
])
def test_legacy_muse_state_reuses_pairing_without_muse(settings, tmp_path, monkeypatch, legacy, retained):
    state = CompanionState(URL, COMPANION_ID, KEY, legacy, str(tmp_path / "drop"),
                           name="Desk companion", memory_sync=False)
    state.save(settings)
    run = AsyncMock()
    monkeypatch.setattr(client, "run", run)

    cli.main(["companion", "run"])

    run.assert_awaited_once()
    loaded, path, caps = run.await_args.args
    assert loaded == replace(state, enabled_caps=retained)
    assert path == settings and tuple(caps) == retained


def test_state_preserves_other_capability_ids(settings, tmp_path):
    state = CompanionState(URL, COMPANION_ID, KEY, ("muse", "future-cap", "Muse"), str(tmp_path / "drop"))
    state.save(settings)

    assert CompanionState.load(settings) == replace(state, enabled_caps=("future-cap", "Muse"))


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


def test_run_explains_hello_capability_refusal(settings, paired, monkeypatch, capsys):
    async def handler(request):
        assert request.url.path == "/companion/hello"
        return httpx.Response(403, json={"error": "Fixture message"})

    mock_client(monkeypatch, handler)
    with pytest.raises(SystemExit) as error:
        cli.main(["companion", "run"])
    assert error.value.code == 1
    assert "Disconnect and pair again to add capabilities." in capsys.readouterr().err
    assert CompanionState.load(settings) == paired


class Work(Capability):
    id = "test"
    title = "Test"
    version = "1"
    actions = {"go": ActionSpec("Run", {"type": "object"})}

    def __init__(self, invoke, run=None):
        self.invoke = invoke
        if run is not None:
            self.run = run


async def test_companion_requests_extra_body_room_only_for_memory_events():
    received = []

    async def run(ctx):
        for kind in ("memory.sync", "hooks.session"):
            await ctx.emit(kind, {})

    async def handler(request):
        received.append((request.url.query, json.loads(request.content)["kind"]))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(base_url=URL + "/companion/", transport=httpx.MockTransport(handler)) as http:
        await client._run_capability(http, COMPANION_ID, Work(None, run))
    assert received == [(b"kind=memory.sync", "memory.sync"), (b"", "hooks.session")]


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


@pytest.mark.parametrize("revocation", ["event", "401"])
@pytest.mark.parametrize("same_pairing", [True, False])
async def test_revocation_clears_same_pairing_even_when_preferences_change(
    settings, paired, monkeypatch, revocation, same_pairing,
):
    updated = replace(paired, enabled_caps=(), name="Updated device")
    if not same_pairing:
        updated = replace(updated, companion_id="cmp_" + "2" * 24, device_key="swbd_" + "B" * 43)
    settings.unlink()
    updated.save(settings)

    async def handler(request):
        if revocation == "401":
            return httpx.Response(401)
        if request.url.path.endswith("/events"):
            return event_stream(b"event: revoked\ndata: {}\n\n")
        return httpx.Response(204)

    mock_client(monkeypatch, handler)
    await asyncio.wait_for(client.run(paired, settings, {}), 2)
    assert CompanionState.load(settings) == (None if same_pairing else updated)


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


async def test_short_lived_streams_back_off_even_with_initial_ping(settings, paired, monkeypatch, capsys):
    delays = []

    async def handler(request):
        if request.url.path.endswith("/hello"):
            return httpx.Response(204)
        if len(delays) == 8:
            return event_stream(b"event: revoked\ndata: {}\n\n")
        return event_stream(b"event: ping\ndata: {}\n\n")

    async def sleep(delay):
        cli.main(["companion", "status"])
        status = capsys.readouterr().out
        assert "Companion running" in status and "Connection reconnecting" in status
        delays.append(delay)

    mock_client(monkeypatch, handler)
    monkeypatch.setattr(client.random, "uniform", lambda *_: 1)
    monkeypatch.setattr(client.asyncio, "sleep", sleep)
    await client.run(paired, settings, {})
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


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
