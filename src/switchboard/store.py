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
CREATE TABLE IF NOT EXISTS projects (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  links TEXT NOT NULL DEFAULT '{}',
  private INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  archived_at REAL
);
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'general',
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

CREATE TABLE IF NOT EXISTS owner_sessions (
  id TEXT PRIMARY KEY,
  via TEXT NOT NULL,
  client_id TEXT,
  source TEXT,
  created_at REAL NOT NULL,
  last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS owner_login_links (
  token_hash TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  source TEXT NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS owner_webauthn_challenges (
  id TEXT PRIMARY KEY,
  challenge BLOB NOT NULL,
  kind TEXT NOT NULL,
  owner_key TEXT,
  expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS owner_webauthn_challenges_expiry ON owner_webauthn_challenges(expires_at);
CREATE TABLE IF NOT EXISTS owner_webauthn_user (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  handle BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS owner_passkeys (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL,
  created_at REAL NOT NULL,
  last_used_at REAL,
  rp_id TEXT
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  day TEXT NOT NULL,
  origin TEXT NOT NULL,
  source TEXT NOT NULL,
  client TEXT NOT NULL,
  destination TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  ref TEXT,
  title TEXT,
  excerpt TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_day ON audit(day, destination, action, outcome);

CREATE TABLE IF NOT EXISTS routes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  destination TEXT NOT NULL,
  agent TEXT NOT NULL DEFAULT '',
  project TEXT NOT NULL DEFAULT '',
  target TEXT NOT NULL,
  defaults TEXT NOT NULL DEFAULT '{}',
  allow_override INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL,
  updated_by TEXT NOT NULL,
  UNIQUE(destination, agent, project)
);

CREATE TABLE IF NOT EXISTS handles (
  handle TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  destination TEXT NOT NULL,
  ref TEXT NOT NULL,
  project TEXT,
  title TEXT,
  meta TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL,
  open INTEGER NOT NULL DEFAULT 1,
  status TEXT,
  stream_cursor TEXT,
  state TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS handles_open ON handles(destination, open);
CREATE INDEX IF NOT EXISTS handles_ref ON handles(destination, ref);
CREATE TABLE IF NOT EXISTS inbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  handle TEXT NOT NULL,
  destination TEXT NOT NULL,
  kind TEXT NOT NULL,
  data TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS inbox_owner ON inbox(owner, id);
CREATE INDEX IF NOT EXISTS inbox_handle ON inbox(handle, id);
CREATE INDEX IF NOT EXISTS inbox_reply_to ON inbox(json_extract(data, '$.in_reply_to'));
CREATE INDEX IF NOT EXISTS handles_root ON handles(json_extract(meta, '$.root'));
CREATE INDEX IF NOT EXISTS handles_reply_to ON handles(json_extract(meta, '$.in_reply_to'));
CREATE INDEX IF NOT EXISTS handles_forward_of ON handles(json_extract(meta, '$.forward_of'));
CREATE TABLE IF NOT EXISTS space_modes (
  block TEXT NOT NULL, space TEXT NOT NULL, channel TEXT NOT NULL, mode TEXT NOT NULL, actions TEXT NOT NULL,
  project TEXT, updated_at REAL NOT NULL, updated_by TEXT NOT NULL,
  PRIMARY KEY(block, space, channel)
);
CREATE TABLE IF NOT EXISTS watch_events (
  block TEXT NOT NULL, space TEXT NOT NULL, channel TEXT NOT NULL, message TEXT NOT NULL,
  author_id TEXT NOT NULL, author TEXT NOT NULL, excerpt TEXT NOT NULL, link TEXT, at REAL NOT NULL,
  target TEXT NOT NULL, project TEXT, dm INTEGER NOT NULL DEFAULT 0, delivered_at REAL,
  PRIMARY KEY(block, space, channel, message)
);
CREATE INDEX IF NOT EXISTS watch_events_pending ON watch_events(block, space, dm, target, at) WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS handles_channel_mode ON handles(owner, created_at) WHERE json_extract(meta, '$.channel_mode.mode') IN ('assist', 'triage');
CREATE TABLE IF NOT EXISTS inbox_cursors (
  owner TEXT NOT NULL,
  handle TEXT NOT NULL,
  cursor INTEGER NOT NULL,
  PRIMARY KEY(owner, handle)
);
"""


def _migrate(db: sqlite3.Connection) -> None:
    """Bring databases written by capy-bridge 0.1 up to the scoped schema."""
    if "private" not in {r[1] for r in db.execute("PRAGMA table_info(projects)")}:
        db.execute("ALTER TABLE projects ADD COLUMN private INTEGER NOT NULL DEFAULT 0")
    cols = {r[1] for r in db.execute("PRAGMA table_info(memories)")}
    if "scope" not in cols:
        db.execute("ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'general'")
    db.execute("DROP INDEX IF EXISTS memories_ns_key")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS memories_scope_key ON memories(scope, namespace, key) WHERE key IS NOT NULL AND deleted_at IS NULL"
    )
    db.execute("CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope, updated_at)")
    db.execute("DROP TABLE IF EXISTS login_failures")
    if "target" not in {r[1] for r in db.execute("PRAGMA table_info(handles)")}:
        db.execute("ALTER TABLE handles ADD COLUMN target TEXT")
    if "rp_id" not in {r[1] for r in db.execute("PRAGMA table_info(owner_passkeys)")}:
        db.execute("ALTER TABLE owner_passkeys ADD COLUMN rp_id TEXT")
    db.execute("CREATE TABLE IF NOT EXISTS alias_use (alias TEXT NOT NULL, agent TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, last_at REAL NOT NULL, PRIMARY KEY(alias, agent))")
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "delegations" in tables:
        db.execute(
            """INSERT INTO audit(at, day, origin, source, client, destination, action, target, ref, title, excerpt, outcome, detail)
               SELECT at, day, 'mcp', source, client, 'capy', kind, target, thread_id, title, excerpt, outcome, detail FROM delegations"""
        )
        db.execute("DROP TABLE delegations")


def new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):013d}{secrets.token_hex(4)}"


class Store:
    """One SQLite file for projects, memories, OAuth state, and the audit log. Calls are short and serialized."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.tx(_migrate)

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

    def n(self, sql: str, args: Iterable[Any] = ()) -> int:
        """Run an UPDATE or DELETE and return how many rows it changed."""
        with self._lock:
            return self.db.execute(sql, tuple(args)).rowcount

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
