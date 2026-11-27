from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from sqlite3 import Row
from typing import Any
from zoneinfo import ZoneInfo

from .store import Store

log = logging.getLogger("switchboard.audit")
REFUSAL_AUDIT_INTERVAL_S = 600


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


class Audit:
    """Every routed action, refusals included, plus the per-day counts that caps are checked against."""

    def __init__(self, store: Store, tz: str):
        self.store = store
        self.tz = ZoneInfo(tz)
        self.tz_name = tz
        self._refusal_audited_at: OrderedDict[tuple[str, str, str, str, str | None, str], float] = OrderedDict()

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

    def record_refusal(self, *, origin: str, client: str, destination: str, action: str, detail: str,
                       target: str | None = None, ref: str | None = None) -> None:
        now = self.store.clock()
        cutoff = now - REFUSAL_AUDIT_INTERVAL_S
        while self._refusal_audited_at and next(iter(self._refusal_audited_at.values())) <= cutoff:
            self._refusal_audited_at.popitem(last=False)
        key = origin, client[:200], destination, action, target, detail
        if key not in self._refusal_audited_at:
            self.record(origin=origin, source="-", client=client, destination=destination, action=action,
                        outcome="refused", target=target, detail=detail, ref=ref)
            self._refusal_audited_at[key] = now

    def _recent_rows(self, *, destination: str | None = None, actions: Sequence[str] = (), origin: str | None = None,
                     outcome: str | None = None, limit: int = 20, cursor: int | None = None,
                     targets: Mapping[str, Sequence[str]] | None = None, keys: Sequence[str] = ()) -> list[Row]:
        where, args = [], []
        for col, val in (("destination", destination), ("origin", origin), ("outcome", outcome)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if actions:
            if "send" in actions and "agent_send" not in actions:
                actions = (*actions, "agent_send")
            where.append(f"action IN ({','.join('?' for _ in actions)})")
            args.extend(actions)
        if cursor is not None:
            boundary = self.store.one("SELECT at FROM audit WHERE id = ?", (cursor,))
            if boundary is None:
                raise ValueError("invalid cursor")
            where.append("(at, id) < (?, ?)")
            args.extend((boundary["at"], cursor))
        if targets is not None or keys:
            subjects = []
            for block, names in (targets or {}).items():
                if names:
                    subjects.append(f"(destination = ? AND target IN ({','.join('?' for _ in names)}))")
                    args.extend((block, *names))
            if keys:
                marks = ",".join("?" for _ in keys)
                subjects.append(f"(client IN ({marks}) OR (destination = 'core' AND action IN ('connect_redeem', 'connect_revoke') "
                                f"AND target IN ({marks})))")
                args.extend([*(f"connect:{key}" for key in keys), *keys])
            if not subjects:
                return []
            where.append("(" + " OR ".join(subjects) + ")")
        sql = "SELECT * FROM audit" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY at DESC, id DESC LIMIT ?"
        return self.store.q(sql, [*args, limit])

    def recent(self, *, destination: str | None = None, action: str | None = None, outcome: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {"at": iso(r["at"]), "origin": r["origin"], "source": r["source"], "client": r["client"], "destination": r["destination"], "action": r["action"],
             "target": r["target"], "ref": r["ref"], "title": r["title"], "outcome": r["outcome"], "detail": r["detail"], "excerpt": r["excerpt"][:120]}
            for r in self._recent_rows(destination=destination, actions=(action,) if action else (), outcome=outcome, limit=limit)
        ]

    def page(self, *, actions: Sequence[str], origin: str | None, outcome: str | None,
             limit: int, cursor: int | None) -> tuple[list[Row], int | None]:
        rows = self._recent_rows(actions=actions, origin=origin, outcome=outcome, limit=limit + 1, cursor=cursor)
        return rows[:limit], rows[limit - 1]["id"] if len(rows) > limit else None

    def related(self, *, targets: Mapping[str, Sequence[str]], keys: Sequence[str], limit: int = 20) -> list[Row]:
        return self._recent_rows(targets=targets, keys=keys, limit=limit)
