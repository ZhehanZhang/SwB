from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime

_NAME = re.compile(r"MuseGadget[0-9A-F]{6}")
_LOG_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\w+\s+"
    r"bluetoothd\[\d+:[0-9a-fA-F]+\]\s+\[com\.apple\.bluetooth:Server\.LE\.Advertise\]\s+(.+)$"
)
_AGGREGATE = re.compile(
    r'\[(\d+)\] Starting Connectable\s+Advertising instance name "([^"]+)", '
    r"(.*?)interval=\d+(?:\.\d+)?ms"
)
_ADVERTISED_UUIDS = re.compile(r"(?:^|, )UUID\(s\) \[([^\]]*)\]")
_HASHED_ONLY = re.compile(r"Hashed UUID\(s\) \[[^\]]*\], ", re.IGNORECASE)
_UUID128 = re.compile(r"[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}")
_MUSE_SERVICE = "7FDD3D1C-38EA-46CF-8B46-314ECF5F240C"
_REQUEST = re.compile(
    r'^Received \'start advertising\' request from session "([A-Za-z0-9.-]+)-peripheral-'
    r'([1-9][0-9]{1,8})-\d+", with advertised UUID\(s\) \[([^\]]*)\]'
)
_LOG_PREDICATE = 'process == "bluetoothd" AND category == "Server.LE.Advertise"'
_POLL_SECONDS = 3
_MAX_OUTPUT = 128 * 1024


def _uuid_list(value: str) -> set[str] | None:
    uuids = {uuid.removeprefix("0X") for uuid in re.split(r"[,\s]+", value.strip().upper()) if uuid}
    if all(_UUID128.fullmatch(uuid) or re.fullmatch(r"(?:[0-9A-F]{4}|[0-9A-F]{8})", uuid) for uuid in uuids):
        return uuids
    return None


def _latest_evidence(logs: str, name: str, since: str) -> tuple[str, frozenset[str]] | None:
    batches: dict[str, list[tuple[str, str]]] = {}
    for line in logs.splitlines():
        entry = _LOG_LINE.fullmatch(line)
        if entry is None or entry[1] < since:
            continue
        if (ad := _AGGREGATE.fullmatch(entry[2])) and ad[2] == name:
            batches.setdefault(entry[1], []).append((ad[1], ad[3]))

    for timestamp, rows in sorted(batches.items(), reverse=True):
        clean_row = False
        opaque = False
        uuids: set[str] = set()
        for index, fields in rows:
            if index == "1" and (not fields or _HASHED_ONLY.fullmatch(fields)):
                clean_row = True
            opaque |= "<private>" in fields
            for field in _ADVERTISED_UUIDS.finditer(fields):
                parsed = _uuid_list(field[1])
                if parsed is None:
                    opaque = True
                else:
                    uuids.update(parsed)
        foreign = uuids - {_MUSE_SERVICE}
        widths = [16 if "-" in uuid else len(uuid) // 2 for uuid in foreign]
        if 3 + 2 + len(name) + sum(widths) + 2 * len(set(widths)) > 31:
            return timestamp, frozenset(foreign)
        if clean_row and not uuids and not opaque:
            return timestamp, frozenset()
    return None


async def _command(*args: str, timeout: float) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return ""
    try:
        async with asyncio.timeout(timeout):
            assert process.stdout is not None
            try:
                await process.stdout.readexactly(_MAX_OUTPUT + 1)
            except asyncio.IncompleteReadError as exc:
                if await process.wait() == 0:
                    return exc.partial.decode("utf-8", errors="replace")
    except (TimeoutError, OSError):
        pass
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.communicate()
    return ""


async def _log(window: str, predicate: str, *, timeout: float) -> str:
    return await _command(
        "/usr/bin/log", "show", "--last", window, "--style", "compact", "--info", "--predicate",
        f"{_LOG_PREDICATE} AND {predicate}", timeout=timeout,
    )


async def _attribution(uuids: frozenset[str], observed: str) -> str:
    if len(uuids) > 8:
        return ""
    matches = " OR ".join(f'eventMessage CONTAINS[c] "{uuid}"' for uuid in sorted(uuids))
    logs = await _log(
        "2d", f"""eventMessage BEGINSWITH "Received 'start advertising' request" AND ({matches})""",
        timeout=20,
    )
    owners: dict[tuple[str, str], set[str]] = {}
    starts: dict[tuple[str, str], str] = {}
    for line in logs.splitlines():
        entry = _LOG_LINE.fullmatch(line)
        if entry is None or entry[1] > observed:
            continue
        if (request := _REQUEST.match(entry[2])) and (advertised := _uuid_list(request[3])):
            if matching := uuids & advertised:
                owner = request[1], request[2]
                owners.setdefault(owner, set()).update(matching)
                starts[owner] = max(starts.get(owner, ""), entry[1])
    if len(owners) != 1:
        return ""
    (bundle, pid), advertised = next(iter(owners.items()))
    if advertised != uuids:
        return ""

    info = await _command("/usr/bin/lsappinfo", "info", "-app", pid, timeout=2)
    actual_pid = re.search(r"^\s*pid\s*=\s*(\d+)\b", info, re.MULTILINE)
    actual_bundle = re.search(r'^\s*bundleID="([^"]+)"', info, re.MULTILINE)
    if not actual_pid or actual_pid[1] != pid or not actual_bundle or actual_bundle[1] != bundle:
        return ""
    checked_in = re.search(r"^\s*checkin time\s*=\s*(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", info, re.MULTILINE)
    if checked_in and checked_in[1].replace("/", "-") > starts[(bundle, pid)][:19]:
        return ""
    display = re.search(r'^"([^"]+)"\s+ASN:', info, re.MULTILINE)
    name = display[1] if display else ""
    if not name or name.casefold() == bundle.casefold():
        path = re.search(r'^\s*bundle path="[^"]*/([^/"]+)\.app"', info, re.MULTILINE)
        name = path[1] if path else ""
    name = name.strip()
    return name if 0 < len(name) <= 80 and name.isprintable() and "/" not in name and "\\" not in name else ""


async def watch(name: str, report: Callable[[str | None], Awaitable[None]]) -> None:
    """Watch only for the lifetime of a Mac pairing. Cancel when it ends."""
    if sys.platform != "darwin" or not _NAME.fullmatch(name):
        return
    since = datetime.now().isoformat(sep=" ", timespec="milliseconds")
    last_timestamp = since
    current: frozenset[str] = frozenset()
    last_report: str | None = None
    attribution: asyncio.Task[str] | None = None
    predicate = (
        'eventMessage CONTAINS "Starting Connectable" AND '
        f'eventMessage CONTAINS \'Advertising instance name "{name}"\''
    )
    try:
        while True:
            logs = await _log("15s", predicate, timeout=3)
            evidence = _latest_evidence(logs, name, since)
            if evidence is not None:
                timestamp, uuids = evidence
                if timestamp > last_timestamp or (timestamp == last_timestamp and uuids and not current):
                    last_timestamp = timestamp
                    if uuids != current:
                        if attribution is not None:
                            attribution.cancel()
                            with suppress(asyncio.CancelledError):
                                await attribution
                            attribution = None
                        current = uuids
                        status = "" if uuids else None
                        if status != last_report:
                            await report(status)
                            last_report = status
                        if uuids:
                            attribution = asyncio.create_task(_attribution(uuids, timestamp))
            if attribution is not None and attribution.done():
                app = attribution.result()
                attribution = None
                if app and app != last_report:
                    await report(app)
                    last_report = app
            await asyncio.sleep(_POLL_SECONDS)
    finally:
        if attribution is not None:
            attribution.cancel()
            with suppress(asyncio.CancelledError):
                await attribution
