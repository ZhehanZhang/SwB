from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any
from unicodedata import category

from ..client import Disconnected
from .base import ActionSpec, CapabilityError

_MAX_PROMPT = 60_000
_PROMPT_FIELDS = ("session", "text", "conversation", "agent")
HARNESS_ACTIONS = {
    "list": ActionSpec("List local agent sessions.", {
        "type": "object", "additionalProperties": False,
    }),
    "prompt": ActionSpec("Send a message to a local agent session.", {
        "type": "object",
        "properties": {
            key: {"type": "string", **({"maxLength": _MAX_PROMPT} if key == "text" else {"minLength": 1})}
            for key in _PROMPT_FIELDS
        },
        "required": list(_PROMPT_FIELDS),
        "additionalProperties": False,
    }),
}


def check_list_args(args: dict[str, Any]) -> None:
    if args:
        raise CapabilityError("List takes no arguments.")


def prompt_args(args: dict[str, Any]) -> tuple[str, str, str, str]:
    if args.keys() != set(_PROMPT_FIELDS) or any(not isinstance(value, str) for value in args.values()):
        raise CapabilityError("Provide a session, text, conversation, and agent.")
    session, text, conversation, agent = (args[key] for key in _PROMPT_FIELDS)
    if not all(value.strip() and value.isprintable() for value in (session, conversation, agent)):
        raise CapabilityError("Provide a session, conversation, and agent.")
    if len(text) > _MAX_PROMPT:
        raise CapabilityError("Use prompt text of up to 60000 characters.")
    if any(char not in "\n\t\r" and category(char) in {"Cc", "Cs"} for char in text):
        raise CapabilityError("Prompt text contains unsupported characters.")
    return session, text, conversation, agent


def folder_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    name = PurePosixPath(value.replace("\\", "/")).name
    return name if name.isprintable() and name not in {".", ".."} and ":" not in name else ""


async def reconnect(listen: Callable[[], Awaitable[None]]) -> None:
    loop = asyncio.get_running_loop()
    delay = 1
    while True:
        started = loop.time()
        try:
            await listen()
        except Disconnected:
            raise
        except Exception:
            pass
        if loop.time() - started >= 30:
            delay = 1
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
