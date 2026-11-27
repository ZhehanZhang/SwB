import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from switchboard.companion.caps import registry
from switchboard.companion.caps._harness import check_list_args, folder_name, prompt_args, reconnect
from switchboard.companion.caps.base import CapabilityError, CompanionContext
from switchboard.companion.client import Disconnected
from switchboard.companion.harness import REPLY_MAX, Harness, Session


@pytest.mark.parametrize(("value", "expected"), [
    ("/private/work/project/", "project"),
    ("C:\\private\\work\\project", "project"),
    ("C:\\", ""),
    ("/", ""),
    (None, ""),
])
def test_folder_name_only(value, expected):
    assert folder_name(value) == expected


def test_harness_action_arguments():
    args = {"session": "pane-1", "text": "a" * 60_000, "conversation": "conversation-1", "agent": "codex"}
    assert prompt_args(args) == tuple(args.values())
    for invalid in ({"text": "a" * 60_001}, {"text": "\x1b[201~"}, {"agent": ""}, {"session": 1}, {"extra": ""}):
        with pytest.raises(CapabilityError):
            prompt_args({**args, **invalid})
    check_list_args({})
    with pytest.raises(CapabilityError):
        check_list_args({"session": "pane-1"})


def test_harnesses_require_opt_in(monkeypatch, tmp_path):
    def unexpected_import(*args):
        pytest.fail("Loaded a harness without opt-in")

    monkeypatch.setattr("switchboard.companion.caps.import_module", unexpected_import)
    assert registry((), tmp_path) == ({}, {})


def test_pending_wake_expires_and_aliases_are_consumed_together(monkeypatch):
    now = 100
    monkeypatch.setattr("switchboard.companion.harness.time.monotonic", lambda: now)
    relay = Harness()
    keys = ["codex:thread", "herdr:pane"]
    relay.expect(keys, "thread", "conversation", "codex")
    assert relay.pending(keys[0:1]) is relay.pending(keys[1:])
    assert relay.take(keys)
    assert relay.pending(keys) is None

    relay.expect(keys, "thread", "conversation", "codex")
    now += 1800
    assert relay.pending(keys) is None
    assert not relay.wakes


async def test_reconnect_backs_off_and_preserves_revocation(monkeypatch, caplog):
    error = RuntimeError("private prompt /private/project")
    listen = AsyncMock(side_effect=[error] * 8 + [Disconnected()])
    sleep = AsyncMock()
    monkeypatch.setattr("switchboard.companion.caps._harness.asyncio.sleep", sleep)

    with pytest.raises(Disconnected):
        await reconnect(listen)

    assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 4, 8, 16, 30, 30, 30]
    assert not caplog.text


async def test_reconnect_propagates_cancellation():
    with pytest.raises(asyncio.CancelledError):
        await reconnect(AsyncMock(side_effect=asyncio.CancelledError()))


async def test_report_recovers_when_the_same_state_failed_to_emit():
    emit = AsyncMock(side_effect=[CapabilityError("Unavailable."), {}])
    ctx = CompanionContext("device", emit)
    relay = Harness()
    state = Session("codex", "thread", dir="/private/project", state="idle")

    with pytest.raises(CapabilityError):
        await relay.report(ctx, "codex", state)
    assert await relay.report(ctx, "codex", state)
    assert not await relay.report(ctx, "codex", state)
    assert emit.await_count == 2
    assert emit.await_args.args == ("codex.session", state.payload())
    assert emit.await_args.args[1]["dir"] == "project"


@pytest.mark.parametrize("text", ["🦉" * REPLY_MAX, "\n" * REPLY_MAX + "text", "a" * (REPLY_MAX + 1)])
async def test_reply_caps_wire_bytes_and_consumes_the_wake(text):
    emit = AsyncMock()
    ctx = CompanionContext("device", emit)
    relay = Harness()
    keys = ["codex:thread"]
    assert not await relay.reply(ctx, "codex", keys, text)
    relay.expect(keys, "thread", "conversation", "codex")
    assert await relay.reply(ctx, "codex", keys, text)
    assert not await relay.reply(ctx, "codex", keys, text)
    assert emit.await_count == 1
    kind, payload = emit.await_args.args
    assert kind == "codex.reply"
    assert payload["conversation"] == "conversation"
    assert text.startswith(payload["text"])
    assert len(json.dumps(payload["text"], ensure_ascii=False).encode()) == REPLY_MAX + 2
    assert len(json.dumps(payload, ensure_ascii=False).encode()) < 16 * 1024
