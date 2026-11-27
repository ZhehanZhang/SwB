from __future__ import annotations

import json
import os
import re
import socket
import stat
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from .harness import reply_text
from .memory import MEMORY_CONTENT_LIMIT, memory_filename, memory_source_key
from .state import CompanionState, state_path

STDIN_LIMIT = 1024 * 1024
LINE_LIMIT = 64 * 1024
SOCKET_TIMEOUT = 0.5

EVENT_STATES = {
    "claude-code": {
        "SessionStart": "idle",
        "UserPromptSubmit": "working",
        "PreToolUse": "working",
        "PermissionRequest": "blocked",
        "Stop": "done",
        "StopFailure": "idle",
        "SessionEnd": "ended",
    },
    "gemini": {
        "SessionStart": "idle",
        "BeforeAgent": "working",
        "AfterAgent": "done",
        "SessionEnd": "ended",
    },
    "codex": {
        "SessionStart": "idle",
        "UserPromptSubmit": "working",
        "PreToolUse": "working",
        "PermissionRequest": "blocked",
        "Stop": "done",
        "Interrupt": "idle",
        "SessionEnd": "ended",
    },
}
NOTIFICATION_STATES = {
    "claude-code": {
        "permission_prompt": "blocked",
        "elicitation_dialog": "blocked",
        "agent_needs_input": "blocked",
        "idle_prompt": "idle",
    },
    "gemini": {"ToolPermission": "blocked"},
}
FINAL_FIELDS = {
    "claude-code": ("Stop", "last_assistant_message"),
    "gemini": ("AfterAgent", "prompt_response"),
    "codex": ("Stop", "last_assistant_message"),
}
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def supported() -> bool:
    return sys.platform in ("linux", "darwin") and hasattr(socket, "AF_UNIX")


def socket_path() -> Path:
    return state_path().with_name("hooks.sock")


def event_state(harness: str, event: str, notification: str) -> str | None:
    if event == "Notification":
        return NOTIFICATION_STATES.get(harness, {}).get(notification)
    return EVENT_STATES.get(harness, {}).get(event)


def _identifier(value: object) -> str:
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else ""


def _read_local(root: Path, parts: tuple[str, ...], limit: int) -> str | None:
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
        directory = os.open(root, flags)
        stack.callback(os.close, directory)
        for part in parts[:-1]:
            directory = os.open(part, flags, dir_fd=directory)
            stack.callback(os.close, directory)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4 * limit:
            return None
        with open(fd, encoding="utf-8", newline="", closefd=False) as stream:
            text = stream.read(limit + 1)
        return text if len(text) <= limit else None


def _auto_memory(value: dict[str, Any]) -> dict[str, str] | None:
    tool = value.get("tool_input")
    raw = tool.get("file_path") if isinstance(tool, dict) else None
    if value.get("tool_name") not in ("Write", "Edit", "MultiEdit") or not isinstance(raw, str):
        return None
    target = Path(raw)
    if not target.is_absolute() or not memory_filename(target.name):
        return None
    if (state := CompanionState.load(state_path())) is not None and not state.memory_sync:
        return None
    target = target.resolve(strict=True)
    if not memory_filename(target.name):
        return None
    default = Path.home() / ".claude"
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or default).expanduser().resolve()
    roots = tuple(dict.fromkeys((config, default.resolve())))
    for root in roots:
        if target.is_relative_to(root):
            parts = target.relative_to(root).parts
            if len(parts) == 4 and parts[0] == "projects" and parts[2] == "memory":
                key = f"{parts[1]}/{parts[3]}"
                if memory_source_key(key) and (content := _read_local(root, parts, MEMORY_CONTENT_LIMIT)) is not None:
                    return {"path": key, "content": content, "kind": "auto-memory"}
                return None
    for root in roots:
        try:
            raw_settings = _read_local(root, ("settings.json",), 65_536)
            settings = json.loads(raw_settings) if raw_settings is not None else None
            directory = settings.get("autoMemoryDirectory") if isinstance(settings, dict) else None
            if isinstance(directory, str) and directory:
                custom = Path(directory).expanduser()
                if custom.is_absolute() and (custom := custom.resolve(strict=True)) == target.parent:
                    key = f"{custom.name}/{target.name}"
                    if memory_source_key(key) and (content := _read_local(custom, (target.name,), MEMORY_CONTENT_LIMIT)) is not None:
                        return {"path": key, "content": content, "kind": "auto-memory"}
        except (OSError, ValueError, RuntimeError):
            continue
    return None


def event_payload(
    harness: object, value: object, *, from_stdin: bool = False, pane_id: object = None,
) -> dict[str, Any] | None:
    if not isinstance(harness, str) or harness not in EVENT_STATES or not isinstance(value, dict):
        return None
    session, event = _identifier(value.get("session_id")), value.get("hook_event_name")
    if not session or not isinstance(event, str):
        return None
    raw_notification = value.get("notification_type")
    notification = raw_notification if event == "Notification" and isinstance(raw_notification, str) else ""
    result: dict[str, Any] = {"harness": harness, "session_id": session, "hook_event_name": event}
    if harness == "claude-code" and event == "PostToolUse":
        memory = _auto_memory(value) if from_stdin else value.get("memory")
        if (not isinstance(memory, dict) or memory.get("kind") != "auto-memory"
                or not memory_source_key(memory.get("path")) or not isinstance(content := memory.get("content"), str)
                or len(content) > MEMORY_CONTENT_LIMIT):
            return None
        result["memory"] = {key: memory[key] for key in ("path", "content", "kind")}
    elif event_state(harness, event, notification) is None:
        return None
    if notification:
        result["notification_type"] = notification
    cwd = value.get("cwd" if from_stdin else "dir")
    if isinstance(cwd, str):
        name = cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1][:64]
        if name not in (".", "..") and name.isprintable():
            result["dir"] = name
    if pane := _identifier(pane_id if from_stdin else value.get("pane_id")):
        result["pane_id"] = pane
    final_event, field = FINAL_FIELDS[harness]
    if event == final_event:
        answer = value.get(field if from_stdin else "text")
        if isinstance(answer, str) and (text := reply_text(answer)):
            result["text"] = text
    return result


def run(args: list[str]) -> None:
    if args in (["-h"], ["--help"]):
        print("Usage\n  swb hook {" + ",".join(EVENT_STATES) + "}")
        print("Forward a local agent event from standard input.")
        return
    if len(args) != 1 or args[0] not in EVENT_STATES or not supported():
        return
    try:
        path = socket_path()
        parent = path.parent.lstat()
        info = path.lstat()
        uid = os.getuid()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid or stat.S_IMODE(parent.st_mode) != 0o700
                or not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600):
            return
        raw = sys.stdin.buffer.read(STDIN_LIMIT + 1)
        if len(raw) > STDIN_LIMIT:
            return
        value = json.loads(raw.decode("utf-8"))
        payload = event_payload(args[0], value, from_stdin=True, pane_id=os.environ.get("HERDR_PANE_ID"))
        if payload is None:
            return
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(SOCKET_TIMEOUT)
            client.connect(str(path))
            client.sendall(line)
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt):
        pass
