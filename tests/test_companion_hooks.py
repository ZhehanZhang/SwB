import asyncio
import json
import socket
import stat
import sys
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import pytest

from switchboard.companion.caps import registry
from switchboard.companion.caps.base import CompanionContext
from switchboard.companion.caps.hooks import HooksCapability
from switchboard.companion.harness import REPLY_MAX, Harness
from switchboard.companion.hook import LINE_LIMIT, STDIN_LIMIT, event_payload, event_state, supported


@pytest.mark.parametrize(("agent", "event", "notification", "state"), [
    ("claude-code", "SessionStart", "", "idle"),
    ("claude-code", "UserPromptSubmit", "", "working"),
    ("claude-code", "PreToolUse", "", "working"),
    ("claude-code", "PermissionRequest", "", "blocked"),
    ("claude-code", "Notification", "permission_prompt", "blocked"),
    ("claude-code", "Notification", "elicitation_dialog", "blocked"),
    ("claude-code", "Notification", "agent_needs_input", "blocked"),
    ("claude-code", "Notification", "idle_prompt", "idle"),
    ("claude-code", "Stop", "", "done"),
    ("claude-code", "StopFailure", "", "idle"),
    ("claude-code", "SessionEnd", "", "ended"),
    ("gemini", "SessionStart", "", "idle"),
    ("gemini", "BeforeAgent", "", "working"),
    ("gemini", "AfterAgent", "", "done"),
    ("gemini", "Notification", "ToolPermission", "blocked"),
    ("gemini", "SessionEnd", "", "ended"),
    ("codex", "SessionStart", "", "idle"),
    ("codex", "UserPromptSubmit", "", "working"),
    ("codex", "PreToolUse", "", "working"),
    ("codex", "PermissionRequest", "", "blocked"),
    ("codex", "Stop", "", "done"),
    ("codex", "Interrupt", "", "idle"),
    ("codex", "SessionEnd", "", "ended"),
    ("gemini", "Notification", "permission_prompt", None),
    ("claude-code", "SubagentStop", "", None),
    ("codex", "Notification", "permission_prompt", None),
])
def test_hook_state_mapping(agent, event, notification, state):
    assert event_state(agent, event, notification) == state


@pytest.mark.parametrize(("platform", "available"), [("linux", True), ("darwin", True), ("win32", False), ("freebsd", False)])
def test_hook_registry_supports_linux_and_macos_only(monkeypatch, tmp_path, platform, available):
    monkeypatch.setattr(sys, "platform", platform)
    if not hasattr(socket, "AF_UNIX"):
        monkeypatch.setattr(socket, "AF_UNIX", object(), raising=False)
    caps, unavailable = registry(("hooks",), tmp_path)
    assert ("hooks" in caps) == available
    assert ("hooks" in unavailable) != available
    if available:
        assert caps["hooks"].actions == {}


@pytest.mark.parametrize(("agent", "event", "answer_field"), [
    ("claude-code", "Stop", "last_assistant_message"),
    ("gemini", "AfterAgent", "prompt_response"),
    ("codex", "Stop", "last_assistant_message"),
])
def test_hook_retains_only_documented_final_text(agent, event, answer_field):
    raw = {
        "session_id": "session-123", "hook_event_name": event, "cwd": "/home/user/private/work/",
        "prompt": "original prompt", "text": "untrusted alternate answer", "pane_id": "untrusted:input",
        "notification_type": "not a notification", "transcript_path": "/private/transcript",
        "tool_input": {"path": "/home/user/file"}, answer_field: "final answer\nnext line",
    }
    assert event_payload(agent, raw, from_stdin=True, pane_id="w1:p2") == {
        "harness": agent, "session_id": "session-123", "hook_event_name": event, "dir": "work",
        "pane_id": "w1:p2", "text": "final answer\nnext line",
    }
    del raw[answer_field]
    assert "text" not in event_payload(agent, raw, from_stdin=True)


async def test_shared_wake_reply_has_serialized_byte_limit():
    emitted = []

    async def emit(kind, value):
        emitted.append((kind, value))
        return {}

    shared = Harness()
    shared.expect(["codex:abc"], "abc", "conversation", "codex")
    answer = '😀\x01"\n' * REPLY_MAX
    assert await shared.reply(CompanionContext("companion", emit), "codex", ["codex:abc"], answer)
    payload = emitted[0][1]
    text = payload["text"]
    assert answer.startswith(text) and text != answer
    assert len(json.dumps(text, ensure_ascii=False).encode("utf-8")) - 2 <= REPLY_MAX
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) < 16 * 1024


@pytest.fixture
def hook_home(monkeypatch):
    if not supported():
        pytest.skip("Unix hook socket requires Linux or macOS")
    with tempfile.TemporaryDirectory(prefix="swb-hook-", dir="/tmp") as temporary:
        home = Path(temporary) / "state"
        monkeypatch.setenv("SWB_COMPANION_HOME", str(home))
        monkeypatch.delenv("HERDR_PANE_ID", raising=False)
        yield home


@asynccontextmanager
async def receiver(home, ctx):
    path = home / "hooks.sock"
    task = asyncio.create_task(HooksCapability().run(ctx))
    try:
        async with asyncio.timeout(2):
            while True:
                await asyncio.sleep(0.01)
                if task.done():
                    task.result()
                if path.exists() and stat.S_IMODE(path.stat().st_mode) == 0o600:
                    break
        yield path
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)


async def send(path, wire):
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(wire)
    await writer.drain()
    writer.write_eof()
    try:
        await asyncio.wait_for(reader.read(), 2)
    except ConnectionResetError:
        pass
    finally:
        writer.close()
        with suppress(ConnectionResetError):
            await writer.wait_closed()


async def run_cli(agent, data):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "switchboard", "hook", agent,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    assert await asyncio.wait_for(proc.communicate(data), 5) == (b"", b"")
    assert proc.returncode == 0


@pytest.mark.parametrize("flag", ["-h", "--help"])
async def test_hook_help_prints_usage(flag):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "switchboard", "hook", flag,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), 5)
    assert proc.returncode == 0 and not err
    assert b"Usage" in out and b"swb hook {claude-code,gemini,codex}" in out


async def test_hook_cli_is_silent_and_receiver_enforces_permissions(hook_home, monkeypatch):
    await run_cli("claude-code", b"{}")
    assert not hook_home.exists()
    hook_home.mkdir(mode=0o755)
    path = hook_home / "hooks.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
        stale.bind(str(path))

    emitted = asyncio.Queue()

    async def emit(kind, value):
        emitted.put_nowait((kind, value))
        return {}

    ctx = CompanionContext("companion", emit)
    async with receiver(hook_home, ctx):
        assert stat.S_IMODE(hook_home.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        with pytest.raises(BlockingIOError):
            await HooksCapability().run(ctx)
        event = {"session_id": "abc", "hook_event_name": "StopFailure", "cwd": "/private/work",
                 "error_details": "/private", "last_assistant_message": "API secret"}
        raw = json.dumps(event).encode()
        await asyncio.gather(
            run_cli("claude-code", b"{bad input"),
            run_cli("claude-code", b"\xff"),
            run_cli("claude-code", raw.ljust(STDIN_LIMIT + 1, b" ")),
            run_cli("invalid", raw),
        )
        monkeypatch.setenv("HERDR_PANE_ID", "w1:p2")
        await run_cli("claude-code", raw.ljust(STDIN_LIMIT, b" "))
        assert await asyncio.wait_for(emitted.get(), 2) == (
            "hooks.session",
            {"harness": "claude-code", "session": "abc", "agent": "claude-code", "name": "",
             "dir": "work", "state": "idle", "detail": "API error"},
        )
        assert emitted.empty()
    assert not path.exists()
    path.write_text("keep this file")
    with pytest.raises(PermissionError):
        await HooksCapability().run(ctx)
    assert path.read_text() == "keep this file"


async def test_hook_receiver_does_not_follow_state_directory_symlink(hook_home):
    private = hook_home.with_name("private")
    private.mkdir(mode=0o755)
    private.chmod(0o755)
    hook_home.symlink_to(private, target_is_directory=True)
    with pytest.raises(OSError):
        await HooksCapability().run(CompanionContext("companion", None))
    assert stat.S_IMODE(private.stat().st_mode) == 0o755
    assert not list(private.iterdir())


async def test_hook_cli_wire_discards_sensitive_fields_and_uses_env_pane(hook_home, monkeypatch):
    hook_home.mkdir(mode=0o700)
    path = hook_home / "hooks.sock"
    frames = asyncio.Queue()

    async def receive(reader, writer):
        frames.put_nowait(await reader.read())
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_unix_server(receive, path=path):
        path.chmod(0o600)
        monkeypatch.setenv("HERDR_PANE_ID", "w1:p2")
        await run_cli("gemini", json.dumps({
            "session_id": "123", "hook_event_name": "BeforeAgent", "cwd": "/private/work",
            "prompt": "private", "transcript_path": "/private/log.json",
            "prompt_response": "not a final answer", "pane_id": "untrusted:pane", "model": "ignored",
        }).encode())
        message = await asyncio.wait_for(frames.get(), 2)
        assert message.count(b"\n") == 1
        assert json.loads(message) == {"harness": "gemini", "session_id": "123", "hook_event_name": "BeforeAgent",
                                       "dir": "work", "pane_id": "w1:p2"}
        path.chmod(0o666)
        await run_cli("gemini", b'{"session_id":"123","hook_event_name":"BeforeAgent"}')
        assert frames.empty()


async def test_hook_receiver_limits_frames_and_relays_only_one_answer(hook_home, monkeypatch):
    shared = Harness()
    monkeypatch.setattr("switchboard.companion.caps.hooks.harness", lambda: shared)
    emitted = asyncio.Queue()

    async def emit(kind, value):
        emitted.put_nowait((kind, value))
        return {}

    async with receiver(hook_home, CompanionContext("companion", emit)) as path:
        event = {"harness": "codex", "session_id": "abc", "hook_event_name": "Stop", "dir": "/private/work",
                 "text": "answer", "prompt": "private", "pane_id": "w1:p2"}
        line = json.dumps(event).encode()
        await send(path, b"{bad input\n")
        await send(path, line)
        await send(path, line.ljust(LINE_LIMIT, b" ") + b"\n")
        await send(path, line.ljust(LINE_LIMIT - 1, b" ") + b"\n" + b'{"harness":"codex"}\n')
        kind, payload = await asyncio.wait_for(emitted.get(), 2)
        assert kind == "hooks.session"
        assert payload == {"harness": "codex", "session": "abc", "agent": "codex", "name": "",
                           "dir": "work", "state": "done", "detail": ""}
        assert emitted.empty()

        shared.expect(["herdr:w1:p2", "codex:abc"], "w1:p2", "conversation", "codex")
        await send(path, line.replace(b'"abc"', b'"def"') + b"\n")
        assert (await asyncio.wait_for(emitted.get(), 2))[0] == "hooks.session"
        assert await asyncio.wait_for(emitted.get(), 2) == (
            "hooks.reply", {"session": "w1:p2", "conversation": "conversation", "agent": "codex", "text": "answer"},
        )
        await send(path, line + b"\n")
        assert not shared.wakes
        assert emitted.empty()


async def test_hook_completion_must_follow_a_wake_for_the_matching_agent(hook_home, monkeypatch):
    shared = Harness()
    monkeypatch.setattr("switchboard.companion.caps.hooks.harness", lambda: shared)
    emitted = asyncio.Queue()
    release = asyncio.Event()

    async def emit(kind, value):
        emitted.put_nowait((kind, value))
        if value["session"] == "before-wake":
            await release.wait()
        return {}

    async with receiver(hook_home, CompanionContext("companion", emit)) as path:
        pane = "w1:p2"
        line = json.dumps({"harness": "codex", "session_id": "before-wake", "hook_event_name": "Stop",
                           "text": "earlier answer", "pane_id": pane}).encode()
        await send(path, line + b"\n")
        assert (await asyncio.wait_for(emitted.get(), 2))[0] == "hooks.session"
        shared.expect([f"herdr:{pane}"], pane, "conversation", "codex")
        release.set()
        await send(path, line.replace(b'"codex"', b'"claude-code"').replace(b'before-wake', b'wrong-agent') + b"\n")
        assert (await asyncio.wait_for(emitted.get(), 2))[0] == "hooks.session"
        correct = line.replace(b'before-wake', b'after-wake').replace(b'earlier answer', b'requested answer')
        await send(path, correct + b"\n")
        assert (await asyncio.wait_for(emitted.get(), 2))[0] == "hooks.session"
        assert await asyncio.wait_for(emitted.get(), 2) == (
            "hooks.reply", {"session": pane, "conversation": "conversation", "agent": "codex", "text": "requested answer"},
        )
        assert not shared.wakes
        assert emitted.empty()


@pytest.mark.parametrize(("harness", "filename", "events"), [
    ("claude-code", "channel/hooks/hooks.json",
     "SessionStart UserPromptSubmit PreToolUse PermissionRequest Notification Stop StopFailure SessionEnd"),
    ("gemini", "integrations/gemini/hooks/hooks.json", "SessionStart BeforeAgent AfterAgent Notification SessionEnd"),
    ("codex", "integrations/codex/hooks.json",
     "SessionStart UserPromptSubmit PreToolUse PermissionRequest Stop Interrupt SessionEnd"),
])
def test_hook_package_commands(harness, filename, events):
    root = Path(__file__).resolve().parents[1]
    hooks = json.loads((root / filename).read_text())["hooks"]
    assert set(hooks) == set(events.split())
    for groups in hooks.values():
        assert groups[0]["hooks"] == [{
            "type": "command", "command": f"sh -c 'test -S \"${{SWB_COMPANION_HOME:-$HOME/.config/swb}}/hooks.sock\" && swb hook {harness}; exit 0'",
            "timeout": 1000 if harness == "gemini" else 1,
        }]
    if harness == "gemini":
        assert json.loads((root / "integrations/gemini/gemini-extension.json").read_text())["name"] == "swb"
