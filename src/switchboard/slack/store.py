from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  team TEXT NOT NULL,
  channel TEXT NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ws_created ON events(workspace, created_at);
CREATE TABLE IF NOT EXISTS sent (
  team TEXT NOT NULL, channel TEXT NOT NULL, ts TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (team, channel, ts)
);
CREATE TABLE IF NOT EXISTS cursors (
  workspace TEXT NOT NULL, channel TEXT NOT NULL, ts TEXT NOT NULL,
  PRIMARY KEY (workspace, channel)
);
CREATE TABLE IF NOT EXISTS write_requests (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  identity TEXT NOT NULL,
  action TEXT NOT NULL,
  params TEXT NOT NULL,
  persona TEXT,
  key_id TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  decided_at REAL,
  decided_by TEXT,
  result TEXT,
  notice TEXT
);
CREATE INDEX IF NOT EXISTS write_requests_status ON write_requests(status, expires_at);
CREATE TABLE IF NOT EXISTS thread_targets (
  workspace TEXT NOT NULL, channel TEXT NOT NULL, thread_ts TEXT NOT NULL, target TEXT NOT NULL, kind TEXT NOT NULL, at REAL NOT NULL,
  PRIMARY KEY (workspace, channel, thread_ts)
);
CREATE TABLE IF NOT EXISTS relays (
  handle TEXT PRIMARY KEY, workspace TEXT NOT NULL, channel TEXT NOT NULL, thread_ts TEXT NOT NULL, target TEXT NOT NULL, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  workspace TEXT,
  identity TEXT,
  detail TEXT NOT NULL
);
"""

REQUEST_JSON_FIELDS = ("params", "persona", "result", "notice")


class Store:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._lock = threading.Lock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def _exec(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.execute(sql, tuple(args))

    def add_event(self, event: dict[str, Any]) -> bool:
        cur = self._exec(
            "INSERT OR IGNORE INTO events (id, workspace, team, channel, ts, kind, payload, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (event["id"], event["workspace"], event["team"], event["channel"], event["ts"], event["kind"], json.dumps(event), self.clock()),
        )
        return cur.rowcount == 1

    def recent_events(self, workspaces: Iterable[str], kind: str | None = None, since: float | None = None, limit: int = 50) -> list[dict[str, Any]]:
        ws = list(workspaces)
        if not ws:
            return []
        sql = f"SELECT payload FROM events WHERE workspace IN ({','.join('?' * len(ws))})"
        args: list[Any] = list(ws)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if since is not None:
            sql += " AND created_at >= ?"
            args.append(since)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))
        return [json.loads(r["payload"]) for r in self._exec(sql, args).fetchall()]

    def prune_events(self, older_than_days: int) -> int:
        cutoff = self.clock() - older_than_days * 86400
        n = self._exec("DELETE FROM events WHERE created_at < ?", (cutoff,)).rowcount
        self._exec("DELETE FROM sent WHERE created_at < ?", (cutoff,))
        return n

    def mark_sent(self, team: str, channel: str, ts: str) -> None:
        self._exec("INSERT OR IGNORE INTO sent (team, channel, ts, created_at) VALUES (?,?,?,?)", (team, channel, ts, self.clock()))

    def set_thread_target(self, workspace: str, channel: str, thread_ts: str, target: str, kind: str) -> None:
        self._exec("INSERT OR REPLACE INTO thread_targets (workspace, channel, thread_ts, target, kind, at) VALUES (?,?,?,?,?,?)",
                   (workspace, channel, thread_ts, target, kind, self.clock()))

    def thread_target(self, workspace: str, channel: str, thread_ts: str) -> str | None:
        row = self._exec("SELECT target FROM thread_targets WHERE workspace=? AND channel=? AND thread_ts=?", (workspace, channel, thread_ts)).fetchone()
        return row[0] if row else None

    def add_relay(self, handle: str, workspace: str, channel: str, thread_ts: str, target: str) -> None:
        self._exec("INSERT OR REPLACE INTO relays (handle, workspace, channel, thread_ts, target, at) VALUES (?,?,?,?,?,?)",
                   (handle, workspace, channel, thread_ts, target, self.clock()))

    def relay(self, handle: str) -> dict[str, Any] | None:
        row = self._exec("SELECT handle, workspace, channel, thread_ts, target FROM relays WHERE handle=?", (handle,)).fetchone()
        return dict(zip(("handle", "workspace", "channel", "thread_ts", "target"), row)) if row else None

    def is_sent(self, team: str, channel: str, ts: str) -> bool:
        return self._exec("SELECT 1 FROM sent WHERE team=? AND channel=? AND ts=?", (team, channel, ts)).fetchone() is not None

    def get_cursor(self, workspace: str, channel: str) -> str | None:
        row = self._exec("SELECT ts FROM cursors WHERE workspace=? AND channel=?", (workspace, channel)).fetchone()
        return row["ts"] if row else None

    def set_cursor(self, workspace: str, channel: str, ts: str) -> None:
        self._exec(
            "INSERT INTO cursors (workspace, channel, ts) VALUES (?,?,?) ON CONFLICT(workspace, channel) DO UPDATE SET ts=excluded.ts",
            (workspace, channel, ts),
        )

    def create_request(self, *, workspace: str, identity: str, action: str, params: dict, persona: dict | None, key_id: str, ttl_seconds: float) -> dict[str, Any]:
        now = self.clock()
        rid = "wr_" + secrets.token_urlsafe(9)
        self._exec(
            "INSERT INTO write_requests (id, workspace, identity, action, params, persona, key_id, status, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, workspace, identity, action, json.dumps(params), json.dumps(persona) if persona else None, key_id, "pending", now, now + ttl_seconds),
        )
        return self.get_request(rid)  # type: ignore[return-value]

    @staticmethod
    def _request_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        for f in REQUEST_JSON_FIELDS:
            out[f] = json.loads(out[f]) if out[f] else None
        return out

    def get_request(self, rid: str) -> dict[str, Any] | None:
        return self._request_row(self._exec("SELECT * FROM write_requests WHERE id=?", (rid,)).fetchone())

    def list_requests(self, status: str | None = None, key_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM write_requests WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if key_id:
            sql += " AND key_id=?"
            args.append(key_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))
        return [self._request_row(r) for r in self._exec(sql, args).fetchall()]  # type: ignore[misc]

    def transition_request(self, rid: str, from_status: str, to_status: str, **fields: Any) -> bool:
        sets = ["status=?"]
        args: list[Any] = [to_status]
        for k, v in fields.items():
            sets.append(f"{k}=?")
            args.append(json.dumps(v) if k in REQUEST_JSON_FIELDS and v is not None else v)
        args += [rid, from_status]
        return self._exec(f"UPDATE write_requests SET {', '.join(sets)} WHERE id=? AND status=?", args).rowcount == 1

    def set_request_notice(self, rid: str, notice: dict) -> None:
        self._exec("UPDATE write_requests SET notice=? WHERE id=?", (json.dumps(notice), rid))

    def expire_requests(self) -> list[dict[str, Any]]:
        now = self.clock()
        rows = self._exec("SELECT id FROM write_requests WHERE status='pending' AND expires_at <= ?", (now,)).fetchall()
        expired = []
        for r in rows:
            if self.transition_request(r["id"], "pending", "expired", decided_at=now, decided_by="system:expiry"):
                expired.append(self.get_request(r["id"]))
        return expired  # type: ignore[return-value]

    def audit(self, actor: str, event: str, workspace: str | None = None, identity: str | None = None, **detail: Any) -> None:
        self._exec(
            "INSERT INTO audit (at, actor, action, workspace, identity, detail) VALUES (?,?,?,?,?,?)",
            (self.clock(), actor, event, workspace, identity, json.dumps(detail, default=str)),
        )

    def bot_posts_in_thread(self, workspace: str, channel: str, thread_ts: str, since: float) -> int:
        """Bot posts switchboard sent in one thread (its root included) since a time, from the audit log."""
        row = self._exec(
            "SELECT COUNT(*) FROM audit WHERE action = 'write_sent' AND workspace = ? AND identity = 'bot' AND at >= ?"
            " AND json_extract(detail, '$.action') = 'post_message' AND json_extract(detail, '$.params.channel') = ?"
            " AND (json_extract(detail, '$.params.thread_ts') = ? OR json_extract(detail, '$.result.ts') = ?)",
            (workspace, since, channel, thread_ts, thread_ts),
        ).fetchone()
        return int(row[0])

    def recent_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
