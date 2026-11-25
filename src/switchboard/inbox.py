"""Per-caller inbox: what destinations report back about delegated work, read by cursor or long-poll."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

from .audit import iso
from .store import Store


class Inbox:
    def __init__(self, store: Store):
        self.store = store
        self._cond: asyncio.Condition | None = None

    def _condition(self) -> asyncio.Condition:
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    # Handles ------------------------------------------------------------------------

    @staticmethod
    def new_handle() -> str:
        return "h_" + secrets.token_hex(8)

    def open_handle(self, *, owner: str, destination: str, ref: str, project: str | None, title: str | None, meta: dict[str, Any],
                    handle: str | None = None, status: str | None = None, stream_cursor: str | None = None) -> str:
        """Persist a handle for delegated work before anything is reported on it, so it survives restarts."""
        handle = handle or self.new_handle()
        self.store.x(
            "INSERT INTO handles(handle, owner, destination, ref, project, title, meta, created_at, status, stream_cursor) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (handle, owner, destination, ref, project, title, json.dumps(meta), self.store.clock(), status, stream_cursor),
        )
        return handle

    def handle(self, handle: str) -> dict[str, Any] | None:
        r = self.store.one("SELECT * FROM handles WHERE handle = ?", (handle,))
        if r is None:
            return None
        return {**dict(r), "meta": json.loads(r["meta"]), "state": json.loads(r["state"]), "open": bool(r["open"])}

    def handles(self, *, owner: str | None = None, destination: str | None = None, open_only: bool = False, project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        where, args = [], []
        for col, val in (("owner", owner), ("destination", destination), ("project", project)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if open_only:
            where.append("open = 1")
        sql = "SELECT handle FROM handles" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
        return [self.handle(r["handle"]) for r in self.store.q(sql, [*args, limit])]

    def update_handle(self, handle: str, *, status: str | None = None, stream_cursor: str | None = None, state: dict[str, Any] | None = None, open: bool | None = None) -> None:
        sets, args = [], []
        for col, val in (("status", status), ("stream_cursor", stream_cursor)):
            if val is not None:
                sets.append(f"{col} = ?")
                args.append(val)
        if state is not None:
            sets.append("state = ?")
            args.append(json.dumps(state))
        if open is not None:
            sets.append("open = ?")
            args.append(int(open))
        if sets:
            sets.append("updated_at = ?")
            args.append(self.store.clock())
            self.store.x(f"UPDATE handles SET {', '.join(sets)} WHERE handle = ?", [*args, handle])

    # Items ----------------------------------------------------------------------------

    async def add(self, handle: str, kind: str, data: dict[str, Any]) -> int:
        h = self.handle(handle)
        if h is None:
            raise KeyError(handle)
        self.store.x("UPDATE handles SET updated_at = ? WHERE handle = ?", (self.store.clock(), handle))
        item = self.store.x(
            "INSERT INTO inbox(owner, handle, destination, kind, data, at) VALUES (?,?,?,?,?,?)",
            (h["owner"], handle, h["destination"], kind, json.dumps(data), self.store.clock()),
        )
        cond = self._condition()
        async with cond:
            cond.notify_all()
        return item

    def head(self) -> int:
        """The newest item's cursor: a reader that starts here sees only what arrives next."""
        r = self.store.one("SELECT MAX(id) AS n FROM inbox")
        return int(r["n"] or 0)

    def _cursor(self, reader: str, handle: str | None) -> int:
        r = self.store.one("SELECT cursor FROM inbox_cursors WHERE owner = ? AND handle = ?", (reader, handle or "*"))
        return r["cursor"] if r else 0

    def _advance(self, reader: str, handle: str | None, cursor: int) -> None:
        self.store.x(
            "INSERT INTO inbox_cursors(owner, handle, cursor) VALUES (?,?,?) ON CONFLICT(owner, handle) DO UPDATE SET cursor = MAX(cursor, excluded.cursor)",
            (reader, handle or "*", cursor),
        )

    def read(self, *, owner: str | None, handle: str | None = None, since: int | None = None, destination: str | None = None,
             limit: int = 100, reader: str | None = None, advance: bool = True) -> dict[str, Any]:
        """Items after since (default: the reader's last position; the reader defaults to the owner), and the cursor to pass next time.

        owner None reads every caller's items (the Claude Code channel does this). Each reader keeps its own cursor,
        so the channel never moves the position capy_wait and capy_inbox read from.
        """
        reader = reader or owner or "*"
        start = self._cursor(reader, handle) if since is None else since
        where, args = ["id > ?"], [start]
        if owner:
            where.append("owner = ?")
            args.append(owner)
        if since is None and not handle:
            where.append("id > COALESCE((SELECT c.cursor FROM inbox_cursors c WHERE c.owner = ? AND c.handle = inbox.handle), 0)")
            args.append(reader)
        if handle:
            where.append("handle = ?")
            args.append(handle)
        if destination:
            where.append("destination = ?")
            args.append(destination)
        rows = self.store.q(f"SELECT * FROM inbox WHERE {' AND '.join(where)} ORDER BY id LIMIT ?", [*args, limit])
        cursor = rows[-1]["id"] if rows else start
        if rows and advance:
            self._advance(reader, handle, cursor)
        return {
            "items": [{"cursor": r["id"], "handle": r["handle"], "owner": r["owner"], "destination": r["destination"], "kind": r["kind"],
                       "at": iso(r["at"]), **json.loads(r["data"])} for r in rows],
            "cursor": cursor,
        }

    async def wait(self, *, owner: str | None, handle: str | None = None, since: int | None = None, destination: str | None = None,
                   timeout: float = 20.0, reader: str | None = None, advance: bool = True) -> dict[str, Any]:
        """Long-poll: return as soon as something new arrives, or an empty result when the timeout runs out."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        cond = self._condition()
        async with cond:
            while True:
                out = self.read(owner=owner, handle=handle, since=since, destination=destination, reader=reader, advance=advance)
                remaining = deadline - loop.time()
                if out["items"] or remaining <= 0:
                    out["timed_out"] = not out["items"]
                    return out
                try:
                    await asyncio.wait_for(cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass
