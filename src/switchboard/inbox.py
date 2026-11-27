"""Per-caller inbox: what blocks report back on conversations, and mailbox messages from other agents, read by cursor or long-poll."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any, Callable

from .audit import iso
from .store import Store

log = logging.getLogger("switchboard.inbox")


class Inbox:
    def __init__(self, store: Store):
        self.store = store
        self._cond: asyncio.Condition | None = None
        self.listeners: list[Callable[[str, int, str, str, dict[str, Any], float], None]] = []

    def _condition(self) -> asyncio.Condition:
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    # Handles ------------------------------------------------------------------------

    @staticmethod
    def new_handle() -> str:
        return "h_" + secrets.token_hex(8)

    def open_handle(self, *, owner: str, destination: str, ref: str, project: str | None, title: str | None, meta: dict[str, Any],
                    handle: str | None = None, status: str | None = None, stream_cursor: str | None = None, target: str | None = None) -> str:
        """Persist a conversation (a handle) before anything is reported on it, so it survives restarts. destination is the
        block that owns it, target the address it went to."""
        handle = handle or self.new_handle()
        self.store.x(
            "INSERT INTO handles(handle, owner, destination, ref, project, title, meta, created_at, status, stream_cursor, target) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (handle, owner, destination, ref, project, title, json.dumps(meta), self.store.clock(), status, stream_cursor, target),
        )
        return handle

    def handle(self, handle: str) -> dict[str, Any] | None:
        r = self.store.one("SELECT * FROM handles WHERE handle = ?", (handle,))
        if r is None:
            return None
        return {**dict(r), "meta": json.loads(r["meta"]), "state": json.loads(r["state"]), "open": bool(r["open"])}

    def handles(self, *, owner: str | None = None, destination: str | None = None, open_only: bool = False, project: str | None = None,
                limit: int = 50, roots_only: bool = False) -> list[dict[str, Any]]:
        where, args = [], []
        for col, val in (("owner", owner), ("destination", destination), ("project", project)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if open_only:
            where.append("open = 1")
        if roots_only:
            where.append("""NOT EXISTS (
                SELECT 1 FROM handles p WHERE p.handle != h.handle
                AND p.handle IN (json_extract(h.meta, '$.root'), json_extract(h.meta, '$.in_reply_to'), json_extract(h.meta, '$.forward_of'))
            ) AND NOT EXISTS (
                SELECT 1 FROM inbox i JOIN handles p ON p.handle = json_extract(i.data, '$.in_reply_to')
                WHERE i.handle = h.handle AND p.handle != h.handle
            )""")
        sql = "SELECT handle FROM handles h" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
        return [self.handle(r["handle"]) for r in self.store.q(sql, [*args, limit])]

    def thread_root(self, row: dict[str, Any]) -> dict[str, Any]:
        seen = {row["handle"]}
        while True:
            meta = row["meta"]
            parent = next((v for k in ("root", "in_reply_to", "forward_of") if isinstance(v := meta.get(k), str) and v not in seen), None)
            if parent is None:
                reply = self.store.one(
                    "SELECT json_extract(data, '$.in_reply_to') AS parent FROM inbox WHERE handle = ? "
                    "AND json_type(data, '$.in_reply_to') = 'text' ORDER BY id LIMIT 1", (row["handle"],),
                )
                parent = reply["parent"] if reply else None
            if not parent or parent in seen or (previous := self.handle(parent)) is None:
                return row
            seen.add(parent)
            row = previous

    def first_endpoints(self, handles: list[str]) -> dict[str, tuple[Any, str | None]]:
        if not handles:
            return {}
        rows = self.store.q(
            """SELECT h.handle,
                 (SELECT json_extract(i.data, '$."from"') FROM inbox i WHERE i.handle = h.handle
                  AND json_type(i.data, '$."from"') = 'object' ORDER BY i.id LIMIT 1) AS sender,
                 (SELECT json_extract(i.data, '$.target') FROM inbox i WHERE i.handle = h.handle
                  AND json_type(i.data, '$.target') = 'text' ORDER BY i.id LIMIT 1) AS target
               FROM handles h WHERE h.handle IN (""" + ",".join("?" for _ in handles) + ")",
            handles,
        )
        return {r["handle"]: (json.loads(r["sender"]) if r["sender"] else None, r["target"]) for r in rows}

    def project_for_ref(self, destination: str, ref: str) -> str | None:
        direct = self.store.one("SELECT project FROM handles WHERE handle = ?", (ref,))
        if direct is not None:
            return direct["project"]
        rows = self.store.q("SELECT DISTINCT project FROM handles WHERE destination = ? AND ref = ? LIMIT 2", (destination, ref))
        return rows[0]["project"] if len(rows) == 1 else None

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
            guard = " AND (destination != 'mailbox' OR status IS NOT 'cancelled')" if status == "answered" else ""
            self.store.x(f"UPDATE handles SET {', '.join(sets)} WHERE handle = ?{guard}", [*args, handle])

    async def cancel_mailbox(self, handle: str) -> None:
        self.update_handle(handle, status="cancelled", open=False)
        cond = self._condition()
        async with cond:
            cond.notify_all()

    # Items ----------------------------------------------------------------------------

    def _add(self, h: dict[str, Any], kind: str, data: dict[str, Any]) -> int:
        def write(db: Any) -> int:
            at = self.store.clock()
            db.execute("UPDATE handles SET updated_at = ? WHERE handle = ?", (at, h["handle"]))
            item = db.execute(
                "INSERT INTO inbox(owner, handle, destination, kind, data, at) VALUES (?,?,?,?,?,?)",
                (h["owner"], h["handle"], h["destination"], kind, json.dumps(data), at),
            ).lastrowid
            for listener in self.listeners:
                try:
                    listener(h["owner"], item, h["handle"], kind, data, at)
                except Exception:
                    log.exception("inbox listener failed for item %s", item)
            return item

        return self.store.tx(write)

    async def add(self, handle: str, kind: str, data: dict[str, Any]) -> int:
        h = self.handle(handle)
        if h is None:
            raise KeyError(handle)
        item = self._add(h, kind, data)
        replied = None
        if kind == "message" and data.get("final") and data.get("in_reply_to"):
            replied = self.handle(data["in_reply_to"])
            if replied is not None and replied["status"] == "cancelled":
                replied = None
            if replied is not None:
                self.update_handle(replied["handle"], status="answered")
        elif kind == "final" and data.get("final") is not False:
            replied = h
        while replied is not None and (original := replied["meta"].get("forward_of")):
            parent = self.handle(original)
            if parent is None or parent["status"] == "cancelled" or parent["meta"].get("forwarded_to") != replied["handle"]:
                break
            replied = parent
            meta = {"in_reply_to": original, "final": True, "from": data.get("from")}
            copy = self.open_handle(owner=replied["ref"], destination="mailbox", ref=replied["owner"], project=replied["project"],
                                    title=None, meta={**meta, "root": replied["meta"].get("root") or original}, status="delivered",
                                    target=f"agent:{replied['ref']}")
            self.update_handle(copy, open=False)
            self._add(self.handle(copy), "message", {**data, **meta, "project": replied["project"]})
            self.update_handle(original, status="answered")
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

    def _advance(self, reader: str, cursors: dict[str, int]) -> None:
        self.store.tx(lambda db: db.executemany(
            "INSERT INTO inbox_cursors(owner, handle, cursor) VALUES (?,?,?) ON CONFLICT(owner, handle) DO UPDATE SET cursor = MAX(cursor, excluded.cursor)",
            ((reader, handle, cursor) for handle, cursor in cursors.items()),
        ))

    @staticmethod
    def message_id(cursor: int) -> str:
        return f"msg_{cursor}"

    @staticmethod
    def _item(r: Any) -> dict[str, Any]:
        return {"cursor": r["id"], "handle": r["handle"], "owner": r["owner"], "destination": r["destination"], "kind": r["kind"],
                "at": iso(r["at"]), **json.loads(r["data"]), "message_id": Inbox.message_id(r["id"])}

    def item(self, item_id: int) -> dict[str, Any] | None:
        row = self.store.one("SELECT * FROM inbox WHERE id = ?", (item_id,))
        return {**self._item(row), "owner": row["owner"]} if row is not None else None

    def read(self, *, owner: str | None, handle: str | None = None, since: int | None = None, destination: str | None = None,
             limit: int = 100, reader: str | None = None, advance: bool = True, project: str | None = None) -> dict[str, Any]:
        """Items after since (default: the reader's last position; the reader defaults to the owner), and the cursor to pass next time.

        owner None reads every caller's items (the Claude Code channel does this). Each reader keeps its own cursor,
        so the channel never moves the position wait and inbox read from.
        """
        reader = reader or owner or "*"
        start = self._cursor(reader, None) if since is None else since
        position = f"conversation:{handle}" if handle else None
        idle_cursor = max(start, self._cursor(reader, position)) if position and since is None else start
        if handle and owner is not None and self.store.one(
            "SELECT 1 FROM handles WHERE handle = ? AND destination = 'mailbox' AND status = 'cancelled' "
            "AND (owner = ? OR ref = ?)", (handle, owner, owner),
        ):
            return {"items": [], "cursor": idle_cursor, "handle_status": "cancelled"}
        with_clause = ""
        where, args = ["id > ?"], [start]
        if owner:
            where.append("owner = ?")
            args.append(owner)
        if since is None:
            where.append("id > COALESCE((SELECT c.cursor FROM inbox_cursors c WHERE c.owner = ? AND c.handle = inbox.handle), 0)")
            args.append(reader)
        if handle:
            with_clause = """WITH RECURSIVE linked(handle) AS (
                VALUES (?)
                UNION SELECT h.handle FROM linked l JOIN handles h ON json_extract(h.meta, '$.root') = l.handle
                UNION SELECT h.handle FROM linked l JOIN handles h ON json_extract(h.meta, '$.in_reply_to') = l.handle
                UNION SELECT h.handle FROM linked l JOIN handles h ON json_extract(h.meta, '$.forward_of') = l.handle
                UNION SELECT i.handle FROM linked l JOIN inbox i ON json_extract(i.data, '$.in_reply_to') = l.handle
            ) """
            args.insert(0, handle)
            where.append("handle IN (SELECT handle FROM linked)")
        if destination:
            where.append("destination = ?")
            args.append(destination)
        if project:
            where.append("handle IN (SELECT h.handle FROM handles h WHERE h.project = ?)")
            args.append(project)
        rows = self.store.q(f"{with_clause}SELECT * FROM inbox WHERE {' AND '.join(where)} ORDER BY id LIMIT ?", [*args, limit])
        cursor = rows[-1]["id"] if rows else idle_cursor
        if rows and advance:
            if handle or destination or project:
                cursors = {r["handle"]: r["id"] for r in rows}
                if position:
                    cursors[position] = cursor
            else:
                cursors = {"*": cursor}
            self._advance(reader, cursors)
        return {
            "items": [self._item(r) for r in rows],
            "cursor": cursor,
        }

    async def wait(self, *, owner: str | None, handle: str | None = None, since: int | None = None, destination: str | None = None,
                   timeout: float = 20.0, reader: str | None = None, advance: bool = True, project: str | None = None) -> dict[str, Any]:
        """Long-poll: return as soon as something new arrives, or an empty result when the timeout runs out."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        cond = self._condition()
        async with cond:
            while True:
                out = self.read(owner=owner, handle=handle, since=since, destination=destination, reader=reader, advance=advance, project=project)
                remaining = deadline - loop.time()
                cancelled = out.get("handle_status") == "cancelled"
                if out["items"] or cancelled or remaining <= 0:
                    out["timed_out"] = not (out["items"] or cancelled)
                    return out
                try:
                    await asyncio.wait_for(cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass
