from __future__ import annotations

import asyncio
import sys
from datetime import datetime

import pytest

from switchboard.companion import macos_advertising as advertising

_NAME = "MuseGadget55EB61"
_UUID = "8188F9EE-3124-4ED4-9301-45CF77D8DB0E"
_CONFLICT_AT = "2026-11-27 20:17:01.392"
_CLEAN_AT = "2026-11-27 20:22:15.357"
_CONFLICT = (
    '2026-11-27 20:17:01.392 I  bluetoothd[440:110e70] [com.apple.bluetooth:Server.LE.Advertise] '
    '[0] Starting Connectable  Advertising instance name "MuseGadget55EB61", Apple Types [ 16 ], interval=270ms\n'
    '2026-11-27 20:17:01.392 I  bluetoothd[440:110e70] [com.apple.bluetooth:Server.LE.Advertise] '
    '[1] Starting Connectable  Advertising instance name "MuseGadget55EB61", '
    'UUID(s) [ 8188F9EE-3124-4ED4-9301-45CF77D8DB0E ], interval=30ms'
)
_CLEAN = (
    '2026-11-27 20:22:15.357 I  bluetoothd[440:11f9a3] [com.apple.bluetooth:Server.LE.Advertise] '
    '[0] Starting Connectable  Advertising instance name "MuseGadget55EB61", Apple Types [ 12 16 ], '
    'interval=181.25ms\n'
    '2026-11-27 20:22:15.357 I  bluetoothd[440:11f9a3] [com.apple.bluetooth:Server.LE.Advertise] '
    '[1] Starting Connectable  Advertising instance name "MuseGadget55EB61", interval=181.25ms'
)
_REQUEST = (
    '2026-11-26 18:39:27.326 Df bluetoothd[440:af4c4] [com.apple.bluetooth:Server.LE.Advertise] '
    'Received \'start advertising\' request from session "Luke.SidecarAssistant-peripheral-972-28", '
    'with advertised UUID(s) [ 8188F9EE-3124-4ED4-9301-45CF77D8DB0E ], '
    'interval 290, no namefor client: { Luke.SidecarAssistant, }'
)
_APP_INFO = (
    '"Outrider Helper" ASN:0x0-0x1234:\n'
    '    bundleID="Luke.SidecarAssistant"\n'
    '    bundle path="/Applications/Outrider Helper.app"\n'
    '    pid = 972 type="Background"\n'
    '    checkin time = 2026/11/26 18:39:26\n'
)


def test_latest_evidence_groups_the_compact_rows():
    conflict, clean = (_CONFLICT_AT, {_UUID}), (_CLEAN_AT, set())
    assert advertising._latest_evidence(_CONFLICT + "\n" + _CONFLICT.splitlines()[0], _NAME, "") == conflict
    sibling = _CONFLICT.splitlines()[1].replace("[1] Starting", "[0] Starting")
    clear = _CLEAN.splitlines()[1].replace(_CLEAN_AT, _CONFLICT_AT)
    assert advertising._latest_evidence(sibling + "\n" + clear, _NAME, "") == conflict
    assert advertising._latest_evidence(_CLEAN + "\n" + _CONFLICT, _NAME, "") == clean
    assert advertising._latest_evidence(_CONFLICT, _NAME, _CLEAN_AT) is None
    assert advertising._latest_evidence(_CONFLICT, "MuseGadget55EB62", "") is None


@pytest.mark.parametrize("logs", [
    "",
    _CLEAN.splitlines()[0],
    _CONFLICT.replace(_UUID, "<private>"),
    _CONFLICT.replace(_UUID, advertising._MUSE_SERVICE),
    _CONFLICT.replace(_UUID, "0x1800"),
    _CONFLICT.replace("Server.LE.Advertise", "Other"),
])
def test_partial_redacted_and_nonconflicting_uuid_logs_are_inconclusive(logs):
    assert advertising._latest_evidence(logs, _NAME, "") is None


def test_hashed_values_do_not_count_in_the_advertisement_budget():
    hashed = _CONFLICT.replace("UUID(s)", "Hashed UUID(s)")
    assert advertising._latest_evidence(hashed, _NAME, "") == (_CONFLICT_AT, set())
    overflow = _CONFLICT.replace(_UUID, "0x1800 1801 1802 1803 1804")
    assert advertising._latest_evidence(overflow, _NAME, "") == (
        _CONFLICT_AT, {"1800", "1801", "1802", "1803", "1804"},
    )


@pytest.mark.parametrize(("history", "info", "expected"), [
    (_REQUEST, _APP_INFO, "Outrider Helper"),
    (_REQUEST, _APP_INFO.replace('"Outrider Helper" ASN', '"Luke.SidecarAssistant" ASN'), "Outrider Helper"),
    (_REQUEST, _APP_INFO.replace("pid = 972", "pid = 981"), ""),
    (_REQUEST, _APP_INFO.replace('bundleID="Luke.SidecarAssistant"', 'bundleID="Other.App"'), ""),
    (_REQUEST, _APP_INFO.replace("2026/11/26 18:39:26", "2026/11/27 20:00:00"), ""),
    (_REQUEST, "", ""),
    (_REQUEST.replace("advertised UUID(s)", "hashed UUID(s)"), _APP_INFO, ""),
    (_REQUEST + "\n" + _REQUEST.replace("SidecarAssistant", "OtherApp"), _APP_INFO, ""),
    ("", _APP_INFO, ""),
])
async def test_attribution_requires_a_running_matching_app(monkeypatch, history, info, expected):
    async def command(*args, timeout):
        if args[0] == "/usr/bin/log":
            assert args[:7] == ("/usr/bin/log", "show", "--last", "2d", "--style", "compact", "--info")
            assert _UUID in args[-1]
            return history
        assert args == ("/usr/bin/lsappinfo", "info", "-app", "972")
        return info

    monkeypatch.setattr(advertising, "_command", command)
    assert await advertising._attribution(frozenset({_UUID}), _CONFLICT_AT) == expected


async def test_watch_reports_changes_and_preserves_evidence_until_a_fresh_clear(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls):
            return cls.fromisoformat("2026-11-27 20:17:00.000")

    monkeypatch.setattr(advertising, "datetime", Clock)
    monkeypatch.setattr(advertising.sys, "platform", "darwin")
    monkeypatch.setattr(advertising, "_POLL_SECONDS", 0)
    polls = asyncio.Queue()
    observed = asyncio.Queue()
    reports = []

    async def command(*args, timeout):
        if args[0] == "/usr/bin/lsappinfo":
            return _APP_INFO
        if args[3] == "2d":
            return _REQUEST
        assert args[:8] == ("/usr/bin/log", "show", "--last", "15s", "--style", "compact", "--info", "--predicate")
        assert 'process == "bluetoothd" AND category == "Server.LE.Advertise"' in args[-1]
        assert 'Starting Connectable' in args[-1] and f'Advertising instance name "{_NAME}"' in args[-1]
        observed.put_nowait(None)
        return await polls.get()

    async def report(app):
        reports.append(app)

    monkeypatch.setattr(advertising, "_command", command)
    task = asyncio.create_task(advertising.watch(_NAME, report))
    try:
        async with asyncio.timeout(1):
            await observed.get()
            for log, statuses in [
                (_CONFLICT.replace(_CONFLICT_AT, "2026-11-27 20:16:59.900"), []),
                ("", []),
                (_CONFLICT, [""]),
                (_CONFLICT, ["", "Outrider Helper"]),
                ("", ["", "Outrider Helper"]),
                (_CLEAN.splitlines()[0], ["", "Outrider Helper"]),
                (_CLEAN, ["", "Outrider Helper", None]),
                (_CONFLICT, ["", "Outrider Helper", None]),
                (_CLEAN, ["", "Outrider Helper", None]),
            ]:
                polls.put_nowait(log)
                await observed.get()
                assert reports == statuses
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.parametrize(("platform", "name"), [("linux", _NAME), ("darwin", _NAME + '\" OR true')])
async def test_watch_ignores_other_platforms_and_unsafe_names(monkeypatch, platform, name):
    monkeypatch.setattr(advertising.sys, "platform", platform)

    async def unexpected(*args, **kwargs):
        pytest.fail("Unexpected diagnostic activity")

    monkeypatch.setattr(advertising, "_command", unexpected)
    await advertising.watch(name, unexpected)


async def test_diagnostic_cancellation_reaps_its_own_subprocess(monkeypatch):
    started = asyncio.Future()
    spawn = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        started.set_result(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    task = asyncio.create_task(advertising._command(sys.executable, "-c", "import time; time.sleep(30)", timeout=5))
    process = await asyncio.wait_for(started, timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode is not None
