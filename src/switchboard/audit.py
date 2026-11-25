from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .store import Store

log = logging.getLogger("switchboard.audit")


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


class Audit:
    """Every routed action, refusals included, plus the per-day counts that caps are checked against."""

    def __init__(self, store: Store, tz: str):
        self.store = store
        self.tz = ZoneInfo(tz)
        self.tz_name = tz

    def today(self) -> str:
        return datetime.fromtimestamp(self.store.clock(), self.tz).date().isoformat()

    def count_since(self, destination: str, action: str, source: str, since: float) -> int:
        row = self.store.one(
            "SELECT COUNT(*) AS n FROM audit WHERE at >= ? AND destination = ? AND action = ? AND source = ? AND outcome = 'ok'",
            (since, destination, action, source),
        )
        return row["n"]

    def count_today(self, destination: str, action: str) -> int:
        row = self.store.one(
            "SELECT COUNT(*) AS n FROM audit WHERE day = ? AND destination = ? AND action = ? AND outcome = 'ok'",
            (self.today(), destination, action),
        )
        return row["n"]

    def record(self, *, origin: str, source: str, client: str, destination: str, action: str, outcome: str,
               target: str | None = None, ref: str | None = None, title: str | None = None, excerpt: str = "", detail: str | None = None) -> None:
        self.store.x(
            "INSERT INTO audit(at, day, origin, source, client, destination, action, target, ref, title, excerpt, outcome, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.store.clock(), self.today(), origin, source, client[:200], destination, action, target, ref, title, excerpt[:300], outcome, detail),
        )
        log.info("%s.%s %s by %s via %s (%s) -> %s %s", destination, action, outcome, source, origin, client[:60], target or "", ref or detail or "")

    def recent(self, *, destination: str | None = None, action: str | None = None, outcome: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        where, args = [], []
        for col, val in (("destination", destination), ("action", action), ("outcome", outcome)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        sql = "SELECT * FROM audit" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY at DESC, id DESC LIMIT ?"
        return [
            {"at": iso(r["at"]), "origin": r["origin"], "source": r["source"], "client": r["client"], "destination": r["destination"], "action": r["action"],
             "target": r["target"], "ref": r["ref"], "title": r["title"], "outcome": r["outcome"], "detail": r["detail"], "excerpt": r["excerpt"][:120]}
            for r in self.store.q(sql, [*args, limit])
        ]
