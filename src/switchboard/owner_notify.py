"""Private chat notices for the owner mailbox."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .audit import iso
from .subject import subject
from .store import dumps

if TYPE_CHECKING:
    from .core import Switchboard

PROVIDERS = ("telegram", "discord", "slack")


class OwnerNotify:
    def __init__(self, core: Switchboard):
        self.core = core

    def _destination(self) -> tuple[str, Any, str] | None:
        blocks = self.core.blocks
        configured = self.core.cfg.owner_notify
        if configured is None:
            configured = tuple(f"{name}:{space}" for kind in PROVIDERS
                               for name, block in blocks.items() if block.manifest.id == kind
                               for space in block.owner_notify_spaces())
        for surface in configured:
            name, space = surface.split(":", 1)
            block = blocks.get(name)
            if block is not None and block.manifest.id in PROVIDERS and space in block.owner_notify_spaces():
                return surface, block, space
        return None

    async def flush(self) -> bool:
        store = self.core.store
        if store.one("SELECT 1 FROM owner_notify_queue LIMIT 1") is None:
            return False
        destination = self._destination()
        surface = destination[0] if destination else None
        now = self.core.clock()

        def claim(db: Any) -> list[Any]:
            db.execute("""DELETE FROM owner_notify_queue WHERE NOT EXISTS (
                SELECT 1 FROM handles h WHERE h.handle = owner_notify_queue.handle AND h.status IS NOT 'cancelled')""")
            rows = db.execute("""SELECT q.handle, h.title, SUM(q.count) AS count, MAX(q.id) AS last_id
                FROM owner_notify_queue q JOIN handles h ON h.handle = q.handle
                GROUP BY q.handle ORDER BY MIN(q.id)""").fetchall()
            if not rows or surface is None:
                return rows
            slot = db.execute("""INSERT INTO owner_notify_surfaces(surface, attempted_at) VALUES (?, ?)
                ON CONFLICT(surface) DO UPDATE SET attempted_at = excluded.attempted_at
                WHERE owner_notify_surfaces.attempted_at <= ?""", (surface, now, now - self.core.modes.digest_s))
            return rows if slot.rowcount else []

        rows = store.tx(claim)
        if not rows:
            return False
        if destination is None:
            status = "no_owner_surface"
            detail = {"reason": "disabled" if self.core.cfg.owner_notify == () else "no_configured_surface"}
        else:
            _, block, space = destination
            title = subject("", "", None, (rows[0]["title"] or "")[:500], verb="")
            if re.fullmatch(r"h_[0-9a-f]{16}", title):
                title = ""
            title = re.sub(r"(?i)\b(?:https?://|mailto:|www\.)\S+|[<>\[\]`*_~@|]", " ", title)
            title = " ".join(title.split())[:100].strip() or "Owner inbox"
            count = sum(row["count"] for row in rows)
            text = f"{title}\n{count} new update{'s' if count != 1 else ''}"
            if base := self.core.cfg.public_url:
                path = f"/{quote(rows[0]['handle'], safe='')}" if len(rows) == 1 else ""
                text += f"\n{base}/inbox{path}"
            try:
                sent = await asyncio.wait_for(block.dm_owner(text, space=space), timeout=30)
            except asyncio.CancelledError:
                self.core.audit.record(origin="internal", source="swb", client="owner-notify", destination="mailbox",
                                       action="owner_notify", outcome="interrupted", ref=rows[0]["handle"], target=surface)
                raise
            except Exception:
                sent = False
            status = "notified" if sent else "notify_failed"
            detail = {"surface": surface, **({"notified_at": iso(self.core.clock())} if sent else {"reason": "send_failed"})}

        def finish(db: Any) -> None:
            db.executemany("DELETE FROM owner_notify_queue WHERE handle = ? AND id <= ?",
                           ((r["handle"], r["last_id"]) for r in rows))
            db.executemany("""UPDATE handles SET status = CASE WHEN status = 'stored' THEN ? ELSE status END,
                meta = json_set(meta, '$.owner_notify', json(?))
                WHERE handle = ? AND NOT EXISTS (SELECT 1 FROM owner_notify_queue q WHERE q.handle = handles.handle)""",
                           ((status, dumps(detail), r["handle"]) for r in rows))
            self.core.audit.record(origin="internal", source="swb", client="owner-notify", destination="mailbox",
                                   action="owner_notify", outcome=status, ref=rows[0]["handle"], target=surface,
                                   detail=detail.get("reason"))

        store.tx(finish)
        return True

    async def run(self) -> None:
        while True:
            await self.flush()
            await asyncio.sleep(5)
