from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  namespace TEXT NOT NULL,
  key TEXT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  deleted_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS memories_ns_key ON memories(namespace, key) WHERE key IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memories_updated ON memories(updated_at);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(title, body, tags, namespace, content='memories', content_rowid='rowid', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, title, body, tags, namespace) VALUES (new.rowid, new.title, new.body, new.tags, new.namespace);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, title, body, tags, namespace) VALUES ('delete', old.rowid, old.title, old.body, old.tags, old.namespace);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, title, body, tags, namespace) VALUES ('delete', old.rowid, old.title, old.body, old.tags, old.namespace);
  INSERT INTO memories_fts(rowid, title, body, tags, namespace) VALUES (new.rowid, new.title, new.body, new.tags, new.namespace);
END;
CREATE TABLE IF NOT EXISTS memory_revisions (
  memory_id TEXT NOT NULL,
  at REAL NOT NULL,
  source TEXT NOT NULL,
  action TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_revisions_id ON memory_revisions(memory_id, at);

CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id TEXT PRIMARY KEY,
  secret_hash TEXT,
  name TEXT,
  redirect_uris TEXT NOT NULL,
  auth_method TEXT NOT NULL,
  kind TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_used_at REAL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
  code_hash TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  challenge TEXT NOT NULL,
  resource TEXT NOT NULL,
  scope TEXT NOT NULL,
  source TEXT NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  client_id TEXT NOT NULL,
  resource TEXT NOT NULL,
  scope TEXT NOT NULL,
  source TEXT NOT NULL,
  family TEXT NOT NULL,
  expires_at REAL NOT NULL,
  revoked_at REAL
);
CREATE INDEX IF NOT EXISTS oauth_tokens_family ON oauth_tokens(family);
CREATE TABLE IF NOT EXISTS login_failures (
  at REAL NOT NULL,
  ip TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delegations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  source TEXT NOT NULL,
  client TEXT NOT NULL,
  target TEXT NOT NULL,
  project_id TEXT,
  thread_id TEXT,
  title TEXT,
  excerpt TEXT NOT NULL,
  outcome TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS delegations_day ON delegations(day, kind, outcome);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):013d}{secrets.token_hex(4)}"


class Store:
    """One SQLite file for memories, OAuth state, and the delegation log. Calls are short and serialized."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self.db.execute(sql, tuple(args))
            return cur.lastrowid or cur.rowcount

    def tx(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                out = fn(self.db)
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")
            return out


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: every word must match, prefix-matched, no operators."""
    words = re.findall(r"[\w][\w'.-]*", text.lower())
    return " ".join(f'"{w.replace(chr(34), "")}"*' for w in words[:12])


def dumps(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"))
