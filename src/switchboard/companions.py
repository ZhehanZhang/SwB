"""The companion registry and the running deployment's outbound job channel."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import re
import secrets
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .audit import Audit, iso
from .auth import COMPANION_KEY_PREFIX, Principal, sha
from .blocks import BlockError
from .connect import CODE_TTL_S, new_code, normalize_code
from .guard import COMPANION_EMIT_LIMIT
from .store import Store, dumps

COMPANION_ID = re.compile(r"cmp_[0-9a-f]{24}")
SLUG = re.compile(r"[a-z][a-z0-9_-]{0,31}")
OFFLINE = "Companion offline."
UNKNOWN_JOB = "Companion job expired or unavailable."
DISCONNECTED = "Companion disconnected."
FAILED = "Companion action failed."
ONLINE_S = 45
MAX_TIMEOUT_S = 900
SCHEMA = """
CREATE TABLE IF NOT EXISTS companion_codes (
  code_hash TEXT PRIMARY KEY,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS companions (
  id TEXT PRIMARY KEY,
  key_hash TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  platform TEXT NOT NULL,
  version TEXT NOT NULL,
  capabilities TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_seen REAL,
  revoked_at REAL
);
"""

Progress = Callable[[dict[str, Any]], Awaitable[None] | None]
Claim = Callable[[Principal, dict[str, Any]], Awaitable[dict[str, Any]]]
Event = Callable[[Principal, dict[str, Any]], Awaitable[dict[str, Any] | None]]


class CompanionError(BlockError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class InvalidCode(CompanionError):
    def __init__(self):
        super().__init__("Pairing code expired or invalid.", 401)


def _label(value: object, max_length: int, *, optional: bool = False) -> str:
    if isinstance(value, str) and len(value) <= max_length and (not value or value.isprintable()):
        text = value.strip()
        if text or optional:
            return text
    raise CompanionError("Invalid companion declaration.")


def _declaration(body: dict[str, Any]) -> tuple[str, str, str, str]:
    name, platform = _label(body.get("name"), 120), _label(body.get("platform"), 64)
    version = _label(body.get("version", ""), 32, optional=True)
    caps = body.get("capabilities")
    if not isinstance(caps, list) or len(caps) > 32:
        raise CompanionError("Invalid companion capabilities.")
    by_id = {}
    for cap in caps:
        if not isinstance(cap, dict):
            raise CompanionError("Invalid companion capability.")
        ident = _label(cap.get("id"), 32)
        actions = cap.get("actions")
        if not SLUG.fullmatch(ident) or ident in by_id or not isinstance(actions, list) or len(actions) > 32:
            raise CompanionError("Invalid companion capability.")
        names = set()
        action_specs = []
        for action in actions:
            spec = action if isinstance(action, dict) else {"name": action}
            action_name = _label(spec.get("name"), 32)
            if not SLUG.fullmatch(action_name) or action_name in names:
                raise CompanionError("Invalid companion action.")
            out = {"name": action_name}
            if "description" in spec:
                out["description"] = _label(spec["description"], 240, optional=True)
            if "schema" in spec:
                if not isinstance(spec["schema"], dict):
                    raise CompanionError("Invalid companion action.")
                out["schema"] = spec["schema"]
            names.add(action_name)
            action_specs.append(out)
        by_id[ident] = {"id": ident, "version": _label(cap.get("version"), 32), "title": _label(cap.get("title"), 80),
                        "actions": action_specs}
    return name, platform, version, dumps(list(by_id.values()))


@dataclass(eq=False)
class Stream:
    events: asyncio.Queue[tuple[str, str, str | None]] = field(default_factory=lambda: asyncio.Queue(maxsize=32))

    def close(self, event: str) -> None:
        while not self.events.empty():
            self.events.get_nowait()
        self.events.put_nowait((event, "{}", None))


@dataclass
class Job:
    companion_id: str
    capability: str
    action: str
    stream: Stream
    future: asyncio.Future[dict[str, Any]]
    progress: Progress | None


class Companions:
    def __init__(self, store: Store, audit: Audit):
        self.store, self.audit = store, audit
        self._streams: dict[str, Stream] = {}
        self._jobs: dict[str, Job] = {}
        self._claims: dict[str, tuple[Claim, str | None, str | None]] = {}
        self._events: dict[str, tuple[str, Event]] = {}
        with store._lock:
            store.db.executescript(SCHEMA)

    def mint(self, url: str, *, origin: str, source: str, client: str) -> dict[str, Any]:
        code, code_hash = new_code()
        now = self.store.clock()
        self.store.n("DELETE FROM companion_codes WHERE expires_at <= ?", (now,))
        self.store.x("INSERT INTO companion_codes(code_hash, expires_at) VALUES (?, ?)", (code_hash, now + CODE_TTL_S))
        self.audit.record(origin=origin, source=source, client=client, destination="core", action="companion_code", outcome="ok")
        return {"code": code, "url": url, "expires_in_s": CODE_TTL_S}

    def pair(self, body: dict[str, Any], *, client: str) -> dict[str, str]:
        code = body.get("code")
        if not isinstance(code, str) or len(code) > 32 or len(raw := normalize_code(code)) != 10:
            raise InvalidCode()
        now, code_hash = self.store.clock(), sha(raw)

        def redeem(db: sqlite3.Connection) -> dict[str, str]:
            if not db.execute("DELETE FROM companion_codes WHERE code_hash = ? AND expires_at > ?", (code_hash, now)).rowcount:
                raise InvalidCode()
            name, platform, version, caps = _declaration(body)
            companion_id, key = "cmp_" + secrets.token_hex(12), COMPANION_KEY_PREFIX + secrets.token_urlsafe(32)
            db.execute("INSERT INTO companions(id, key_hash, name, platform, version, capabilities, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (companion_id, sha(key), name, platform, version, caps, now))
            return {"companion_id": companion_id, "device_key": key}

        out = self.store.tx(redeem)
        self.audit.record(origin="http", source="-", client=client, destination="core", action="companion_pair", outcome="ok", target=out["companion_id"])
        return out

    def authenticate(self, token: str) -> Principal | None:
        if not token.startswith(COMPANION_KEY_PREFIX):
            return None
        row = self.store.one("SELECT id, name FROM companions WHERE key_hash = ? AND revoked_at IS NULL", (sha(token),))
        if row is None:
            return None
        return Principal(row["name"], f"companion:{row['id']}", "companion", origin="companion",
                         trust="device", scopes=(), tools=(), targets=())

    def hello(self, companion_id: str, body: dict[str, Any]) -> None:
        if not self.store.n("UPDATE companions SET name = ?, platform = ?, version = ?, capabilities = ?, last_seen = ? "
                            "WHERE id = ? AND revoked_at IS NULL", (*_declaration(body), self.store.clock(), companion_id)):
            raise CompanionError(DISCONNECTED, 401)

    def list(self, *, details: bool = False) -> list[dict[str, Any]]:
        now = self.store.clock()
        out = []
        for row in self.store.q("SELECT id, name, platform, version, capabilities, last_seen FROM companions "
                                "WHERE revoked_at IS NULL ORDER BY created_at, id"):
            caps, seen = json.loads(row["capabilities"]), row["last_seen"]
            out.append({"id": row["id"], "name": row["name"], "platform": row["platform"],
                        "online": row["id"] in self._streams or (seen is not None and now - seen < ONLINE_S),
                        "last_seen": iso(seen), "capabilities": caps if details else [cap["id"] for cap in caps],
                        **({"version": row["version"]} if details else {})})
        return out

    def _close(self, companion_id: str, stream: Stream, event: str) -> None:
        stream.close(event)
        if self._streams.get(companion_id) is stream:
            del self._streams[companion_id]
        for job in self._jobs.values():
            if job.stream is stream and not job.future.done():
                job.future.set_exception(CompanionError(DISCONNECTED if event == "revoked" else OFFLINE))

    def revoke(self, companion_id: str, *, origin: str, source: str, client: str) -> bool:
        if not self.store.n("UPDATE companions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (self.store.clock(), companion_id)):
            return False
        if (stream := self._streams.get(companion_id)) is not None:
            self._close(companion_id, stream, "revoked")
        self.audit.record(origin=origin, source=source, client=client, destination="core", action="companion_revoke",
                          outcome="ok", target=companion_id)
        return True

    async def events(self, companion_id: str, heartbeat_s: float) -> AsyncIterator[tuple[str, str]]:
        def touch() -> bool:
            return bool(self.store.n("UPDATE companions SET last_seen = ? WHERE id = ? AND revoked_at IS NULL",
                                     (self.store.clock(), companion_id)))

        if not touch():
            yield "revoked", "{}"
            return
        if (old := self._streams.get(companion_id)) is not None:
            self._close(companion_id, old, "closed")
        stream = self._streams[companion_id] = Stream()
        loop = asyncio.get_running_loop()
        next_ping = loop.time() + heartbeat_s
        try:
            yield "ping", "{}"
            while True:
                if loop.time() >= next_ping and self._streams.get(companion_id) is stream:
                    event, data, job_id = ("ping" if touch() else "revoked"), "{}", None
                    next_ping = loop.time() + heartbeat_s
                else:
                    try:
                        async with asyncio.timeout_at(next_ping):
                            event, data, job_id = await stream.events.get()
                    except TimeoutError:
                        continue
                if job_id is not None and ((job := self._jobs.get(job_id)) is None or job.future.done()):
                    continue
                if event != "closed":
                    yield event, data
                if event in ("closed", "revoked"):
                    return
        finally:
            self._close(companion_id, stream, "closed")

    async def invoke(self, companion_id: str, capability: str, action: str, args: dict[str, Any], timeout_s: float = 30,
                     on_progress: Progress | None = None, *, principal: Principal | None = None) -> dict[str, Any]:
        target = companion_id if COMPANION_ID.fullmatch(companion_id) else None
        ref, outcome, job_id = None, "refused", None
        try:
            if not 0 < timeout_s <= MAX_TIMEOUT_S:
                raise CompanionError(f"Choose a companion timeout between 0 and {MAX_TIMEOUT_S} seconds.")
            stream = self._streams.get(companion_id)
            if stream is None:
                raise CompanionError(OFFLINE)
            row = self.store.one("SELECT capabilities FROM companions WHERE id = ? AND revoked_at IS NULL", (companion_id,))
            if row is None:
                raise CompanionError(OFFLINE)
            if not any(cap["id"] == capability and any(spec["name"] == action for spec in cap["actions"])
                       for cap in json.loads(row["capabilities"])):
                raise CompanionError("Companion action unavailable.")
            ref = f"{capability}.{action}"
            if stream.events.full():
                raise CompanionError("Companion busy.")
            new_id = "cj_" + secrets.token_hex(16)
            try:
                data = json.dumps({"job_id": new_id, "capability": capability, "action": action, "args": args},
                                  separators=(",", ":"), allow_nan=False)
            except (ValueError, TypeError, RecursionError):
                raise CompanionError("Invalid companion action arguments.") from None
            future = asyncio.get_running_loop().create_future()
            job_id = new_id
            self._jobs[job_id] = Job(companion_id, capability, action, stream, future, on_progress)
            stream.events.put_nowait(("job", data, job_id))
            outcome = "failed"
            result = await asyncio.wait_for(future, timeout_s)
            outcome = "ok"
            return result
        except TimeoutError:
            raise CompanionError("Companion action timed out.") from None
        finally:
            if job_id is not None:
                self._jobs.pop(job_id, None)
            self.audit.record(origin=principal.origin if principal else "core", source=principal.source if principal else "core",
                              client=principal.client_id if principal else "core", destination="core", action="companion_invoke",
                              outcome=outcome, target=target, ref=ref)

    def _job(self, companion_id: str, body: dict[str, Any]) -> Job:
        if self.store.one("SELECT 1 FROM companions WHERE id = ? AND revoked_at IS NULL", (companion_id,)) is None:
            raise CompanionError(DISCONNECTED, 401)
        job_id = body.get("job_id")
        job = self._jobs.get(job_id) if isinstance(job_id, str) else None
        if job is None or job.companion_id != companion_id or job.future.done():
            raise CompanionError(UNKNOWN_JOB, 404)
        return job

    async def result(self, companion_id: str, body: dict[str, Any]) -> None:
        job = self._job(companion_id, body)
        if "progress" in body:
            if not isinstance(data := body["progress"], dict) or "ok" in body:
                raise CompanionError("Invalid companion progress.")
            if job.progress is not None:
                try:
                    if inspect.isawaitable(done := job.progress(data)):
                        await done
                except Exception:
                    raise CompanionError("Companion progress failed.", 500) from None
        elif body.get("ok") is True and isinstance(body.get("result"), dict):
            job.future.set_result(body["result"])
        elif body.get("ok") is False:
            job.future.set_exception(CompanionError(FAILED))
        else:
            raise CompanionError("Invalid companion result.")

    def register_claim(self, kind: str, handler: Claim, *, capability: str | None = None, action: str | None = None) -> None:
        if kind in self._claims:
            raise ValueError("Companion claim kind already registered.")
        self._claims[kind] = handler, capability, action

    async def claim(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        kind = body.get("kind")
        registration = self._claims.get(kind) if isinstance(kind, str) else None
        if registration is None:
            raise CompanionError("Unknown companion claim.", 404)
        companion_id = principal.client_id.removeprefix("companion:")
        job = self._job(companion_id, body)
        handler, capability, action = registration
        if (capability is not None and capability != job.capability) or (action is not None and action != job.action):
            raise CompanionError("Unknown companion claim.", 404)
        if not isinstance(payload := body.get("payload"), dict):
            raise CompanionError("Invalid companion claim.")
        outcome = "refused"
        task = asyncio.create_task(handler(principal, payload))
        try:
            done, _ = await asyncio.wait((task, job.future), return_when=asyncio.FIRST_COMPLETED)
            if job.future in done:
                raise CompanionError(UNKNOWN_JOB, 404)
            try:
                result = task.result()
            except Exception:
                outcome = "failed"
                raise CompanionError("Companion claim failed.") from None
            outcome = "ok"
            return result
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
            self.audit.record(origin="companion", source=companion_id, client=principal.client_id, destination="core",
                              action="companion_claim", outcome=outcome, target=companion_id, ref=kind)

    def register_event(self, kind: str, handler: Event, *, capability: str) -> None:
        if not kind or len(kind) > 96 or not SLUG.fullmatch(capability) or kind in self._events:
            raise ValueError("Invalid or duplicate companion event kind.")
        self._events[kind] = capability, handler

    async def emit(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        companion_id = principal.client_id.removeprefix("companion:")
        row = self.store.one("SELECT capabilities FROM companions WHERE id = ? AND revoked_at IS NULL", (companion_id,))
        if row is None:
            raise CompanionError(DISCONNECTED, 401)
        capability, kind = body.get("capability"), body.get("kind")
        if not any(cap["id"] == capability for cap in json.loads(row["capabilities"])):
            raise CompanionError("Companion capability unavailable.", 403)
        registration = self._events.get(kind) if isinstance(kind, str) else None
        if registration is None or registration[0] != capability:
            raise CompanionError("Unknown companion event.", 404)
        try:
            payload = body.get("payload")
            if not isinstance(payload, dict):
                raise ValueError
            size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        except (ValueError, RecursionError):
            raise CompanionError("Invalid companion event.") from None
        if size > COMPANION_EMIT_LIMIT:
            raise CompanionError("Companion event payloads are limited to 16 KiB.", 413)
        try:
            out = await registration[1](principal, payload)
            return out if out is not None else {"ok": True}
        except Exception:
            raise CompanionError("Companion event failed.") from None
