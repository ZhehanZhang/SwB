# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Modified for SwB.

"""Bounded text reply tracking for a single Muse side chat."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

REPLY_QUIET_S = 3.0
EMPTY_REPLY_GRACE_S = 1.0
_MAX_PENDING_EVENTS = 64
_MAX_TEXT_BYTES = 64 * 1024
_MAX_MESSAGES = 32
_MAX_IGNORED_MESSAGES = 256
_CONTROL_CHARACTERS = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")
_TERMINAL_TASK_STATUSES = ("completed", "failed", "errored", "cancelled", "canceled")
_MESSAGE_EVENTS = ("delta.message_start", "delta.text_append", "delta.message_done", "message.assistant")


@dataclass
class Reply:
    text: str = ""
    done: bool = False


def transcript_text(value, message_id: str | None = None) -> str:
    """Read only assistant text content from Muse's structured transcript."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    messages = value.get("messages", [])
    if not isinstance(messages, list):
        return ""
    assistants = [message for message in messages
                  if isinstance(message, dict) and message.get("role") == "assistant"]
    if message_id is not None:
        assistants = [message for message in assistants if message.get("id") == message_id]
    if not assistants:
        return ""
    content = assistants[-1].get("content", [])
    if not isinstance(content, list):
        return ""
    texts = [item["text"] for item in content
             if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)]
    return "\n".join(texts)


class MuseTurn:
    """Track replies that follow the acknowledged user message or name it as parent."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.last_activity = time.monotonic()
        self.busy = False
        self.task_finished = False
        self._user_ids: set[str] = set()
        self._after_note = False
        self._last_seq = {"row": 0, "live": 0}
        self._pending: list[dict] = []
        self._pending_text_bytes = 0
        self._replies: dict[str, Reply] = {}
        self._ignored_messages: dict[str, None] = {}

    @property
    def text(self) -> str:
        return "\n\n".join(reply.text.strip() for reply in self._replies.values() if reply.text.strip())

    def acknowledge(self, response: dict, *, fresh: bool) -> None:
        result = response.get("result", response)
        if not isinstance(result, dict):
            raise ValueError("Invalid Muse chat acknowledgement")
        chat = result.get("session_id")
        if chat is not None and chat != self.session_id:
            raise ValueError("Muse acknowledged a different chat")
        self._user_ids = {result[key] for key in ("message_id", "reply_to_message_id")
                          if isinstance(result.get(key), str) and 0 < len(result[key]) <= 512}
        if not self._user_ids:
            raise ValueError("Muse chat acknowledgement omitted the user message ID")
        self._after_note = fresh
        self.last_activity = time.monotonic()
        pending, self._pending = self._pending, []
        for event in pending:
            self._route(event)

    def feed(self, event: dict) -> None:
        payload = event.get("payload")
        if event.get("type") != "event" or not isinstance(payload, dict):
            return
        chat = payload.get("session_id") or event.get("session_id")
        if chat and chat != self.session_id:
            return
        seq = event.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            key = "row" if event.get("event") in ("message.user", "message.assistant") else "live"
            if seq <= self._last_seq[key]:
                return
            self._last_seq[key] = seq
        if not self._user_ids:
            self._pending_text_bytes += sum(len(payload[key].encode("utf-8", errors="surrogatepass")) for key in
                                            ("text", "display_text", "content", "transcript")
                                            if isinstance(payload.get(key), str))
            if len(self._pending) >= _MAX_PENDING_EVENTS or self._pending_text_bytes > _MAX_TEXT_BYTES:
                raise ValueError("Muse sent too much chat data before acknowledging the wake")
            self._pending.append(event)
            return
        self._route(event)

    def complete(self) -> bool:
        if not self._user_ids or self.busy or any(not reply.done for reply in self._replies.values()):
            return False
        quiet = time.monotonic() - self.last_activity
        if any(reply.text.strip() for reply in self._replies.values()):
            return self.task_finished or quiet >= REPLY_QUIET_S
        return self.task_finished and quiet >= (EMPTY_REPLY_GRACE_S if self._replies else REPLY_QUIET_S)

    def _route(self, event: dict) -> None:
        payload = event["payload"]
        name = event.get("event")
        message_id = payload.get("message_id") or event.get("message_id") or payload.get("id")
        message_id = message_id if isinstance(message_id, str) else None
        parent = payload.get("reply_to_message_id") or payload.get("parent_message_id")
        parent = parent if isinstance(parent, str) else None
        if name == "message.user":
            self._after_note = message_id in self._user_ids
            return
        parent_linked = parent in self._user_ids or parent in self._replies
        if not (self._after_note or parent_linked or message_id in self._replies):
            return
        if name in ("agent.status", "task.status"):
            if parent and not parent_linked:
                return
            self._status(name, payload)
        elif name in _MESSAGE_EVENTS and message_id and payload.get("role") != "user":
            self._message(name, payload, message_id, parent, parent_linked)

    def _status(self, name: str, payload: dict) -> None:
        activity = payload.get("activity_code")
        status = payload.get("status")
        terminal = status in _TERMINAL_TASK_STATUSES
        if name == "task.status" and isinstance(status, str):
            self.task_finished = terminal
        if isinstance(activity, str):
            self.busy = activity not in ("", "online", "idle", *_TERMINAL_TASK_STATUSES)
        elif isinstance(status, str):
            self.busy = status not in ("", "idle") and not terminal
        if name == "task.status" and self.task_finished:
            self.busy = False
        self.last_activity = time.monotonic()

    def _message(self, name: str, payload: dict, message_id: str, parent: str | None, parent_linked: bool) -> None:
        reply = self._replies.get(message_id)
        if parent and not parent_linked:
            if reply is None:
                self._ignored_messages[message_id] = None
                if len(self._ignored_messages) > _MAX_IGNORED_MESSAGES:
                    del self._ignored_messages[next(iter(self._ignored_messages))]
            return
        if reply is None:
            if message_id in self._ignored_messages:
                return
            if len(self._replies) >= _MAX_MESSAGES or len(message_id) > 512:
                raise ValueError("Muse sent too many or oversized chat identifiers")
            reply = self._replies[message_id] = Reply()
        if name == "delta.text_append" and not reply.done and isinstance(payload.get("text"), str):
            reply.text += _CONTROL_CHARACTERS.sub("", payload["text"])
        if name in ("delta.message_done", "message.assistant"):
            completed_text = payload.get("display_text")
            if not isinstance(completed_text, str):
                transcript = payload.get("transcript")
                completed_text = (transcript if isinstance(transcript, str)
                                  else transcript_text(transcript, message_id) or payload.get("content"))
            if isinstance(completed_text, str):
                reply.text = _CONTROL_CHARACTERS.sub("", completed_text)
            if name == "delta.message_done" or payload.get("display_text_ready") is not False:
                reply.done = True
        if len(reply.text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise ValueError("Muse returned more than 64 KiB of reply text")
        self.last_activity = time.monotonic()
