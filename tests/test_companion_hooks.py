import asyncio
import json
import os
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
from switchboard.companion.memory import MEMORY_CONTENT_LIMIT, MEMORY_SYNC_SOURCE, sync_item
from switchboard.companion.state import CompanionState


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
async def receiver(home, ctx, **options):
    path = home / "hooks.sock"
    task = asyncio.create_task(HooksCapability(**options).run(ctx))
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
     "SessionStart UserPromptSubmit PreToolUse PostToolUse PermissionRequest Notification Stop StopFailure SessionEnd"),
    ("gemini", "integrations/gemini/hooks/hooks.json", "SessionStart BeforeAgent AfterAgent Notification SessionEnd"),
    ("codex", "integrations/codex/hooks.json",
     "SessionStart UserPromptSubmit PreToolUse PermissionRequest Stop Interrupt SessionEnd"),
])
def test_hook_package_commands(harness, filename, events):
    root = Path(__file__).resolve().parents[1]
    hooks = json.loads((root / filename).read_text())["hooks"]
    assert set(hooks) == set(events.split())
    for event, groups in hooks.items():
        assert groups[0]["hooks"] == [{
            "type": "command", "command": f"sh -c 'test -S \"${{SWB_COMPANION_HOME:-$HOME/.config/swb}}/hooks.sock\" && swb hook {harness}; exit 0'",
            "timeout": 1000 if harness == "gemini" else 1,
            **({"async": True} if event == "PostToolUse" else {}),
        }]
    if harness == "gemini":
        assert json.loads((root / "integrations/gemini/gemini-extension.json").read_text())["name"] == "swb"
    if harness == "claude-code":
        assert hooks["PostToolUse"][0]["matcher"] == "Write|Edit|MultiEdit"


@pytest.fixture
def claude_home(hook_home, monkeypatch):
    home = hook_home.with_name("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = home / ".claude" / "projects" / "-workspace-test" / "memory"
    path.mkdir(parents=True)
    return path


def memory_event(path, tool="Write"):
    return {"session_id": "test-123", "hook_event_name": "PostToolUse", "cwd": "/private/SwB",
            "tool_name": tool, "tool_input": {"file_path": str(path), "content": "not the file"},
            "transcript_path": "/private/transcript"}


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_auto_memory_uses_resolved_file_and_sends_only_relative_path(claude_home, tool):
    file = claude_home / "detail.md"
    file.write_text("Read the current file.", encoding="utf-8")
    alias = claude_home / "alias.md"
    alias.symlink_to(file)
    assert event_payload("claude-code", memory_event(alias, tool), from_stdin=True) == {
        "harness": "claude-code", "session_id": "test-123", "hook_event_name": "PostToolUse", "dir": "SwB",
        "memory": {"path": "-workspace-test/detail.md", "content": "Read the current file.", "kind": "auto-memory"},
    }
    assert event_payload("gemini", memory_event(file, tool), from_stdin=True) is None
    assert event_payload("claude-code", memory_event(file, "Read"), from_stdin=True) is None


def test_auto_memory_accepts_config_override_and_custom_setting(claude_home, monkeypatch):
    home = claude_home.parents[3]
    config = home / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    file = config / "projects" / "repo" / "memory" / "fact.md"
    file.parent.mkdir(parents=True)
    file.write_text("Override.", encoding="utf-8")
    payload = event_payload("claude-code", memory_event(file), from_stdin=True)
    assert payload["memory"]["path"] == "repo/fact.md"

    notes = home / "custom-notes"
    notes.mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"autoMemoryDirectory": "~/custom-notes"}), encoding="utf-8")
    custom = notes / "fact.md"
    custom.write_text("Custom.", encoding="utf-8")
    assert event_payload("claude-code", memory_event(custom), from_stdin=True)["memory"] == {
        "path": "custom-notes/fact.md", "content": "Custom.", "kind": "auto-memory",
    }
    alias = notes / "escape.md"
    outside = home / "outside.md"
    outside.write_text("Outside.", encoding="utf-8")
    alias.symlink_to(outside)
    assert event_payload("claude-code", memory_event(alias), from_stdin=True) is None


def test_auto_memory_ignores_excluded_files_and_symlink_escapes(claude_home):
    outside = claude_home.parents[3] / "outside"
    outside.mkdir()
    external = outside / "external.md"
    external.write_text("Outside.", encoding="utf-8")
    alias = claude_home / "escape.md"
    alias.symlink_to(external)
    sibling = claude_home.parents[1] / "symlinked-project"
    sibling.mkdir()
    (sibling / "memory").symlink_to(outside, target_is_directory=True)
    (claude_home / "folder.md").mkdir()
    os.mkfifo(claude_home / "pipe.md")
    for name in ("MEMORY.md", "CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".hidden.md", "note.txt"):
        (claude_home / name).write_text("Excluded.", encoding="utf-8")
    (claude_home / "oversize.md").write_text("x" * (MEMORY_CONTENT_LIMIT + 1), encoding="utf-8")
    for path in (external, sibling / "memory" / "external.md", *claude_home.iterdir()):
        assert event_payload("claude-code", memory_event(path), from_stdin=True) is None


def test_auto_memory_requires_the_local_users_file(claude_home, monkeypatch):
    file = claude_home / "fact.md"
    file.write_text("Owned by another user.", encoding="utf-8")
    monkeypatch.setattr(os, "getuid", lambda: file.stat().st_uid + 1)
    assert event_payload("claude-code", memory_event(file), from_stdin=True) is None


@pytest.mark.parametrize(("text", "title", "body", "tags"), [
    ("---\nname: 'Title'\ndescription: Other\ntype: feedback\n---\n\n# Heading\nText", "Title", "\n# Heading\nText", ["feedback"]),
    ('---\r\ndescription: "A: B"\r\n---\r\nText', "A: B", "Text", []),
    ("intro\n## Heading\nText", "Heading", "intro\n## Heading\nText", []),
    ("---\nname: Broken\nText", "file", "---\nname: Broken\nText", []),
])
def test_auto_memory_frontmatter_and_title_fallback(text, title, body, tags):
    assert sync_item("project/file.md", text) == {"source_key": "project/file.md", "title": title, "body": body, "tags": tags}


async def test_auto_memory_hook_emits_large_utf8_payload(claude_home, hook_home):
    text = "😀" * (MEMORY_CONTENT_LIMIT - len("# Title\n"))
    file = claude_home / "fact.md"
    file.write_text("# Title\n" + text, encoding="utf-8")
    emitted = asyncio.Queue()

    async def emit(kind, payload):
        emitted.put_nowait((kind, payload))
        return {}

    async with receiver(hook_home, CompanionContext("companion", emit)):
        await run_cli("claude-code", json.dumps(memory_event(file)).encode())
        assert await asyncio.wait_for(emitted.get(), 2) == ("memory.sync", {
            "source": MEMORY_SYNC_SOURCE, "dir": "SwB", "items": [
                {"source_key": "-workspace-test/fact.md", "title": "Title", "body": "# Title\n" + text, "tags": []},
            ],
        })
        assert emitted.empty()


async def test_auto_memory_opt_out_and_hook_failure_are_silent(claude_home, hook_home):
    file = claude_home / "fact.md"
    file.write_bytes(b"\xff")
    event = memory_event(file)
    raw = json.dumps(event).encode()
    async with asyncio.timeout(2):
        await run_cli("claude-code", raw)
    emitted = asyncio.Queue()

    async def emit(kind, payload):
        emitted.put_nowait(kind)
        return {}

    async with receiver(hook_home, CompanionContext("companion", emit), memory_sync=False) as socket_path:
        async with asyncio.timeout(2):
            await run_cli("claude-code", raw)
        file.write_text("Private.", encoding="utf-8")
        wire = event_payload("claude-code", event, from_stdin=True)
        await send(socket_path, json.dumps(wire).encode() + b"\n")
        state = CompanionState("https://swb.test", "cmp_" + "a" * 24, "swbd_" + "A" * 43,
                               ("hooks",), str(hook_home / "drop"), memory_sync=False)
        state.save(hook_home / "companion.json")
        assert event_payload("claude-code", event, from_stdin=True) is None
        await run_cli("claude-code", b'{"session_id":"test-123","hook_event_name":"SessionStart"}')
        assert await asyncio.wait_for(emitted.get(), 2) == "hooks.session"
        assert emitted.empty()
