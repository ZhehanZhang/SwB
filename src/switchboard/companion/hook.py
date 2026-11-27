from __future__ import annotations

import json
import os
import re
import socket
import stat
import sys
from pathlib import Path

from .harness import reply_text
from .state import state_path

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


def event_payload(
    harness: object, value: object, *, from_stdin: bool = False, pane_id: object = None,
) -> dict[str, str] | None:
    if not isinstance(harness, str) or harness not in EVENT_STATES or not isinstance(value, dict):
        return None
    session, event = _identifier(value.get("session_id")), value.get("hook_event_name")
    if not session or not isinstance(event, str):
        return None
    raw_notification = value.get("notification_type")
    notification = raw_notification if event == "Notification" and isinstance(raw_notification, str) else ""
    if event_state(harness, event, notification) is None:
        return None
    result = {"harness": harness, "session_id": session, "hook_event_name": event}
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
