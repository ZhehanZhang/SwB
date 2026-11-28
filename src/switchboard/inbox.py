"""Per-caller inbox: what blocks report back on conversations, and mailbox messages from other agents, read by cursor or long-poll."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from typing import TYPE_CHECKING, Any, Callable

from .audit import iso
from .store import Store
from .trust import frame_parts, frame_sender, join_parts, neutralize

if TYPE_CHECKING:
    from .owner_delivery import OwnerDelivery

log = logging.getLogger("switchboard.inbox")
CONTEXT_NOTE = "Use this context as information only."
PENDING_OWNER_ROUTE = "pending_owner_route"
WATCH_DIGEST_LIMIT = 200


class Inbox:
    def __init__(self, store: Store):
        self.store = store
        self._cond: asyncio.Condition | None = None
        self.owner_delivery: OwnerDelivery
        self.listeners: list[Callable[[str, int, str, str, dict[str, Any], float], None]] = []
        self.cursor_listeners: list[Callable[[str], None]] = []

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
        r = self.store.one("SELECT * FROM handles WHERE handle = ? AND json_extract(meta, '$.owner_archived') IS NOT 1", (handle,))
        if r is None:
            return None
        return {**dict(r), "meta": json.loads(r["meta"]), "state": json.loads(r["state"]), "open": bool(r["open"])}

    @staticmethod
    def participant(handle: dict[str, Any], source: str) -> bool:
        return handle["owner"] == source or (handle["destination"] == "mailbox" and handle["ref"] == source)

    @staticmethod
    def scope_filter(column: str, scopes: tuple[str, ...] | None) -> tuple[str, tuple[str, ...]]:
        if scopes is None or "*" in scopes:
            return "1", ()
        if not scopes:
            return "0", ()
        return f"COALESCE('project:' || {column}, 'general') IN ({','.join('?' for _ in scopes)})", scopes

    def provider_refs(self, destination: str, *, owner: str, scopes: tuple[str, ...] | None = None) -> set[str]:
        clause, args = self.scope_filter("project", scopes)
        return {r["ref"] for r in self.store.q(
            f"SELECT DISTINCT ref FROM handles WHERE destination = ? AND owner = ? AND {clause}", (destination, owner, *args))}

    def handles(self, *, owner: str | None = None, destination: str | None = None, open_only: bool = False, project: str | None = None,
                limit: int = 50, roots_only: bool = False, scopes: tuple[str, ...] | None = None,
                before: tuple[float, str] | None = None, older_first: bool = False,
                predicates: list[tuple[str, list[str]]] | None = None) -> list[dict[str, Any]]:
        where, args = ["(owner != '' OR json_extract(meta, '$.for_owner') IS NOT 1)", "json_extract(meta, '$.owner_archived') IS NOT 1"], []
        for col, val in (("owner", owner), ("destination", destination), ("project", project)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if open_only:
            where.append("open = 1")
        if scopes is not None:
            clause, values = self.scope_filter("h.project", scopes)
            where.append(clause)
            args.extend(values)
        if roots_only:
            where.append("""NOT EXISTS (
                SELECT 1 FROM handles p WHERE p.handle != h.handle
                AND json_extract(p.meta, '$.owner_archived') IS NOT 1
                AND p.handle IN (json_extract(h.meta, '$.root'), json_extract(h.meta, '$.in_reply_to'), json_extract(h.meta, '$.forward_of'))
            ) AND NOT EXISTS (
                SELECT 1 FROM inbox i JOIN handles p ON p.handle = json_extract(i.data, '$.in_reply_to')
                WHERE i.handle = h.handle AND p.handle != h.handle AND json_extract(p.meta, '$.owner_archived') IS NOT 1
            )""")
        timestamp = "COALESCE(updated_at, created_at)" if roots_only else "created_at"
        for predicate, values in predicates or ():
            where.append(predicate)
            args.extend(values)
        if before is not None:
            where.append(f"({timestamp}, handle) {'>' if older_first else '<'} (?, ?)")
            args.extend(before)
        direction = "ASC" if older_first else "DESC"
        order = f"{timestamp} {direction}" + (f", handle {direction}" if roots_only or before is not None else "")
        sql = "SELECT handle FROM handles h" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order} LIMIT ?"
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
        await self._notify_waiters()

    async def _notify_waiters(self) -> None:
        cond = self._condition()
        async with cond:
            cond.notify_all()

    def signal_waiters(self) -> None:
        if self._cond is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self._notify_waiters())

    # Items ----------------------------------------------------------------------------

    def _add(self, h: dict[str, Any], kind: str, data: dict[str, Any], *, connection: Any = None) -> int:
        def write(db: Any) -> int:
            at = self.store.clock()
            db.execute("UPDATE handles SET updated_at = ? WHERE handle = ?", (at, h["handle"]))
            item = db.execute(
                "INSERT INTO inbox(owner, handle, destination, kind, data, at) VALUES (?,?,?,?,?,?)",
                (h["owner"], h["handle"], h["destination"], kind, json.dumps(data), at),
            ).lastrowid
            if kind in ("context", "watch_digest"):
                return item
            for listener in self.listeners:
                try:
                    listener(h["owner"], item, h["handle"], kind, data, at)
                except Exception:
                    log.exception("inbox listener failed for item %s", item)
            return item

        return self.store.tx(write) if connection is None else write(connection)

    def _publish_owner_item(self, db: Any, handle: str, kind: str, data: dict[str, Any], recipient: str, step: str) -> None:
        db.execute("""UPDATE handles SET owner = ?, target = ?, status = 'delivered', meta = json_set(meta, '$.resolved_by', ?)
                   WHERE handle = ?""", (recipient, f"agent:{recipient}", step, handle))
        self._add({"handle": handle, "owner": recipient, "destination": "mailbox"}, kind,
                  {**data, "for_owner": True, "resolved_by": step}, connection=db)

    def store_owner_item(self, *, source: str, project: str | None, title: str | None, meta: dict[str, Any],
                         kind: str, data: dict[str, Any], handle: str | None = None, connection: Any = None) -> tuple[str, str | None]:
        handle = handle or self.new_handle()

        def write(db: Any) -> str | None:
            resolved = self.owner_delivery.resolve(project, data.get("block"), data.get("space"))
            at = self.store.clock()
            db.execute("""INSERT INTO handles(handle, owner, destination, ref, project, title, meta, created_at, updated_at, open, status)
                        VALUES (?, '', 'mailbox', ?, ?, ?, ?, ?, ?, 0, ?)""",
                       (handle, source, project, title, json.dumps({**meta, "for_owner": True}), at, at, PENDING_OWNER_ROUTE))
            if resolved is None:
                db.execute("INSERT INTO owner_delivery_pending(handle, kind, data) VALUES (?, ?, ?)", (handle, kind, json.dumps(data)))
                self.owner_delivery.audit(handle, source, kind, data, None, None)
                return None
            recipient, step = resolved
            self._publish_owner_item(db, handle, kind, data, recipient, step)
            self.owner_delivery.audit(handle, source, kind, data, recipient, step)
            return recipient

        recipient = self.store.tx(write) if connection is None else write(connection)
        if connection is None and recipient is not None:
            self.signal_waiters()
        return handle, recipient

    def flush_owner_delivery(self) -> None:
        def write(db: Any) -> list[tuple[str, str, dict[str, Any]]]:
            db.execute("""DELETE FROM owner_delivery_pending WHERE NOT EXISTS (
                SELECT 1 FROM handles h WHERE h.handle = owner_delivery_pending.handle AND h.status = ?)""", (PENDING_OWNER_ROUTE,))
            released = []
            resolutions = {}
            for row in db.execute("""SELECT h.handle, h.ref, h.project, p.kind, p.data FROM owner_delivery_pending p
                    JOIN handles h ON h.handle = p.handle ORDER BY p.rowid""").fetchall():
                data = json.loads(row["data"])
                group = row["project"], data.get("block"), data.get("space")
                if group not in resolutions:
                    resolutions[group] = self.owner_delivery.resolve(*group)
                if (resolved := resolutions[group]) is None:
                    continue
                handle, kind = row["handle"], row["kind"]
                recipient, step = resolved
                self._publish_owner_item(db, handle, kind, data, recipient, step)
                self.owner_delivery.audit(handle, row["ref"], kind, data, recipient, step)
                db.execute("DELETE FROM owner_delivery_pending WHERE handle = ?", (handle,))
                released.append((handle, kind, data))
            return released

        if self.store.one("SELECT 1 FROM owner_delivery_pending LIMIT 1") is None:
            return
        released = self.store.tx(write)
        for handle, kind, data in released:
            if kind == "message":
                self.record_reply(handle, kind, data)
        if released:
            self.signal_waiters()

    def pending_owner_count(self) -> int:
        return self.store.one("""SELECT COUNT(*) AS n FROM owner_delivery_pending p
            JOIN handles h ON h.handle = p.handle WHERE h.status = ?""", (PENDING_OWNER_ROUTE,))["n"]

    def store_watch_digest(self, db: Any, *, target: str, project: str | None, sender: dict[str, str], data: dict[str, Any]) -> None:
        group = """h.project IS ? AND json_extract(i.data, '$.block') = ? AND json_extract(i.data, '$.space') = ?
            AND COALESCE(json_extract(i.data, '$.dm'), ?) = ?"""
        args = (project, data["block"], data["space"], 0 if sender["block"] == "github" else None, data["dm"])
        owner = target == "owner"
        pending = db.execute(f"""SELECT h.handle, i.data FROM owner_delivery_pending i
            JOIN handles h ON h.handle = i.handle WHERE i.kind = 'watch_digest'
            AND h.status = ? AND {group} ORDER BY i.rowid DESC LIMIT 1""", (PENDING_OWNER_ROUTE, *args)).fetchone() if owner else None
        recipient = "json_extract(i.data, '$.for_owner') = 1" if owner else "i.owner = ? AND json_extract(i.data, '$.for_owner') IS NOT 1"
        reader = "c.owner = i.owner" if owner else "c.owner IN ('owner', i.owner)"
        latest = pending if pending is not None else db.execute(f"""SELECT i.id, i.handle, i.data,
            h.status IS NOT 'cancelled' AND NOT EXISTS (
            SELECT 1 FROM inbox_cursors c WHERE {reader}
            AND c.handle IN ('*', i.handle) AND c.cursor >= i.id) AS unread
            FROM inbox i JOIN handles h ON h.handle = i.handle
            WHERE {recipient} AND i.kind = 'watch_digest' AND {group}
            AND json_extract(h.meta, '$.owner_archived') IS NOT 1
            ORDER BY i.id DESC LIMIT 1""", args if owner else (target, *args)).fetchone()
        now = self.store.clock()
        if latest is not None and (pending is not None or latest["unread"]):
            previous = json.loads(latest["data"])
            channel = data["channel"] if data["channel"] == previous["channel"] else ""
            merged = json.dumps({
                **previous, **data,
                "channel": channel,
                "count": previous["count"] + data["count"],
                "items": (previous["items"] + data["items"])[-WATCH_DIGEST_LIMIT:],
            })
            if pending is not None:
                db.execute("UPDATE owner_delivery_pending SET data = ? WHERE handle = ?", (merged, latest["handle"]))
            else:
                db.execute("UPDATE inbox SET data = ? WHERE id = ?", (merged, latest["id"]))
            if sender["block"] == "github":
                sender = {**sender, "space": channel or data["space"]}
            db.execute("UPDATE handles SET updated_at = ?, meta = json_set(meta, '$.from', json(?)) WHERE handle = ?",
                       (now, json.dumps(sender), latest["handle"]))
            return
        title = f"Watch digest for {data['space']}"
        meta = {"from": sender}
        data = {**data, "items": data["items"][-WATCH_DIGEST_LIMIT:]}
        if owner:
            self.store_owner_item(source="", project=project, title=title, meta=meta, kind="watch_digest", data=data, connection=db)
            return
        handle = self.new_handle()
        db.execute("INSERT INTO handles(handle, owner, destination, ref, project, title, meta, created_at, open, status) "
                   "VALUES (?, ?, 'mailbox', '', ?, ?, ?, ?, 0, 'delivered')", (handle, target, project, title, json.dumps(meta), now))
        self._add({"handle": handle, "owner": target, "destination": "mailbox"}, "watch_digest", data, connection=db)

    async def add(self, handle: str, kind: str, data: dict[str, Any]) -> int:
        h = self.handle(handle)
        if h is None:
            raise KeyError(handle)
        item = self._add(h, kind, data)
        self.record_reply(handle, kind, data)
        await self._notify_waiters()
        return item

    def record_reply(self, handle: str, kind: str, data: dict[str, Any]) -> None:
        replied = None
        if kind == "message" and (original := data.get("in_reply_to")):
            if data.get("final"):
                replied = self.handle(original)
                if replied is not None and replied["status"] == "cancelled":
                    replied = None
                if replied is not None:
                    self.update_handle(replied["handle"], status="answered")
            elif data.get("final") is False:
                self.store.x(
                    "UPDATE handles SET status = 'working', updated_at = ? "
                    "WHERE handle = ? AND destination = 'mailbox' AND status = 'delivered'",
                    (self.store.clock(), original),
                )
        elif kind == "final" and data.get("final") is not False:
            replied = self.handle(handle)
        while replied is not None and (original := replied["meta"].get("forward_of")):
            parent = self.handle(original)
            if parent is None or parent["status"] == "cancelled" or parent["meta"].get("forwarded_to") != replied["handle"]:
                break
            replied = parent
            item_meta = {"in_reply_to": original, "final": True, "from": data.get("from")}
            meta = {**item_meta, "root": replied["meta"].get("root") or original}
            item = {**data, **item_meta, "project": replied["project"]}
            if replied["ref"] == "owner":
                self.store_owner_item(source=replied["owner"], project=replied["project"], title=None, meta=meta, kind="message", data=item)
            else:
                copy = self.open_handle(owner=replied["ref"], destination="mailbox", ref=replied["owner"], project=replied["project"],
                                        title=None, meta=meta, status="delivered", target=f"agent:{replied['ref']}")
                self.update_handle(copy, open=False)
                self._add(self.handle(copy), "message", item)
            self.update_handle(original, status="answered")

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
        for listener in self.cursor_listeners:
            listener(reader)

    def ack(self, reader: str, cursor: int) -> int:
        """Mark every item up to cursor as read for reader, capped at the newest item, and return the reader's position."""
        self._advance(reader, {"*": min(cursor, self.head())})
        return self._cursor(reader, None)

    def mark_owner_read(self, handle: str, cursor: int, count: int | None = None) -> bool:
        return bool(self.store.n("""INSERT INTO inbox_cursors(owner, handle, cursor)
            SELECT 'owner', handle, id FROM inbox WHERE handle = ? AND id = ?
            AND (kind != 'watch_digest' OR json_extract(data, '$.count') = ?)
            ON CONFLICT(owner, handle) DO UPDATE SET cursor = MAX(cursor, excluded.cursor)""", (handle, cursor, count)))

    @staticmethod
    def message_id(cursor: int) -> str:
        return f"msg_{cursor}"

    @staticmethod
    def message_cursor(message_id: str) -> int | None:
        if re.fullmatch(r"msg_[1-9][0-9]{0,18}", message_id):
            cursor = int(message_id[4:])
            if cursor <= 2**63 - 1:
                return cursor
        return None

    def message_handle(self, message_id: str) -> dict[str, Any] | None:
        cursor = self.message_cursor(message_id)
        row = self.store.one("SELECT handle FROM inbox WHERE id = ?", (cursor,)) if cursor is not None else None
        return self.handle(row["handle"]) if row is not None else None

    @staticmethod
    def _item(r: Any) -> dict[str, Any]:
        data = json.loads(r["data"])
        if r["kind"] == "watch_digest":
            count = data["count"]
            block, space = data["block"], data["space"]
            where = f"in {block} {space}"
            heading = f"{count} event{'s' if count != 1 else ''}"
            authored = any("parts" in entry for entry in data["items"])
            entries = []
            for entry in data["items"]:
                author, excerpt, link = entry.get("author") or entry.get("author_id") or "unknown", entry["excerpt"], entry["link"]
                safe_link = neutralize(link) if link else link
                if (parts := entry.pop("parts", None)) is not None:
                    framed = "\n\n".join(filter(None, (neutralize(excerpt), join_parts(frame_parts(parts, where)))))
                    entries.append("\n".join(filter(None, (framed, safe_link))))
                else:
                    _, framed = frame_sender(excerpt, author, None, where)
                    entries.append("\n".join(filter(None, (framed, safe_link) if authored else (author, excerpt, link))))
                entry.update(excerpt=framed, author=neutralize(author), link=safe_link)
            trust, text = frame_sender(heading if authored else "\n\n".join([heading, *entries]), "Watch digest", None, where)
            if authored:
                text = "\n\n".join([text, *entries])
            data.update(text=text, trust=trust, **{"from": {"block": block, "space": space, "trust": trust}})
        return {"cursor": r["id"], "handle": r["handle"], "owner": r["owner"], "destination": r["destination"], "kind": r["kind"],
                "at": iso(r["at"]), **data, "message_id": Inbox.message_id(r["id"])}

    def item(self, item_id: int) -> dict[str, Any] | None:
        row = self.store.one("""SELECT i.* FROM inbox i JOIN handles h ON h.handle = i.handle
            WHERE i.id = ? AND json_extract(h.meta, '$.owner_archived') IS NOT 1""", (item_id,))
        return {**self._item(row), "owner": row["owner"]} if row is not None else None

    def read(self, *, owner: str | None, handle: str | None = None, since: int | None = None, destination: str | None = None,
             limit: int = 100, reader: str | None = None, advance: bool = True, project: str | None = None,
             scopes: tuple[str, ...] | None = None, owner_digests: bool = False) -> dict[str, Any]:
        """Items after since (default: the reader's last position; the reader defaults to the owner), and the cursor to pass next time.

        owner None reads every caller's items (the Claude Code channel does this). Each reader keeps its own cursor,
        so the channel never moves the position wait and inbox read from.
        """
        reader = reader or owner or "*"
        start = self._cursor(reader, None) if since is None else since
        position = f"conversation:{handle}" if handle else None
        idle_cursor = max(start, self._cursor(reader, position)) if position and since is None else start
        if handle:
            row = self.store.one("""SELECT owner, ref, status, json_extract(meta, '$.owner_archived') AS archived
                FROM handles WHERE handle = ? AND destination = 'mailbox'""", (handle,))
            if row and row["archived"]:
                return {"items": [], "cursor": idle_cursor}
            if row and row["status"] == "cancelled" and owner in (row["owner"], row["ref"]):
                return {"items": [], "cursor": idle_cursor, "handle_status": "cancelled"}
        with_clause = ""
        where, args = ["id > ?", """NOT EXISTS (SELECT 1 FROM handles h WHERE h.handle = inbox.handle
            AND json_extract(h.meta, '$.owner_archived') = 1)"""], [start]
        if owner:
            where.append("(owner = ? OR (kind = 'watch_digest' AND json_extract(data, '$.for_owner') IS NOT 1))"
                         if owner_digests else "owner = ?")
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
        if scopes is not None and "*" not in scopes:
            clause, values = self.scope_filter("h.project", scopes)
            where.append(f"handle IN (SELECT h.handle FROM handles h WHERE {clause})")
            args.extend(values)
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
            if owner_digests and reader != "owner":
                digests = {r["handle"]: r["id"] for r in rows if r["kind"] == "watch_digest" and r["owner"] != owner}
                if digests:
                    self._advance("owner", digests)
        return {
            "items": [self._item(r) for r in rows],
            "cursor": cursor,
        }

    async def wait(self, *, owner: str | None, handle: str | None = None, since: int | None = None, destination: str | None = None,
                   timeout: float = 20.0, reader: str | None = None, advance: bool = True, project: str | None = None,
                   scopes: tuple[str, ...] | None = None, owner_digests: bool = False) -> dict[str, Any]:
        """Long-poll: return as soon as something new arrives, or an empty result when the timeout runs out."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        cond = self._condition()
        async with cond:
            while True:
                out = self.read(owner=owner, handle=handle, since=since, destination=destination, reader=reader, advance=advance,
                                project=project, scopes=scopes, owner_digests=owner_digests)
                remaining = deadline - loop.time()
                cancelled = out.get("handle_status") == "cancelled"
                if out["items"] or cancelled or remaining <= 0:
                    out["timed_out"] = not (out["items"] or cancelled)
                    return out
                try:
                    await asyncio.wait_for(cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass
