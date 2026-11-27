"""Connecting an agent in one step: the owner mints a one-time pairing code, the client redeems it for its own key.

A code names the agent (claude-code, codex, muse-code, ...) and optionally its project, lives ten minutes and works
once. Redeeming it (POST /connect) registers the agent and returns a bearer key for it alone; only its hash is stored.
Keys are revocable from the root page or the connect_revoke tool. A later swb connect CLI wraps this: it redeems the
code and writes the client's MCP config.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .store import Store

AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RESERVED = frozenset({"slack", "local", "mailbox", "agent", "auto", "admin", "config", "core", "memory", "-"})
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_TTL_S = 600
KEY_PREFIX = "swb_"
PUBLIC_HTTPS_PREREQUISITE = (
    "Run swb up --tunnel cloudflare to get a public HTTPS address. "
    "See https://swb.one/docs/first-run#get-a-public-address for setup."
)


def public_https_prerequisite(owner_sign_in: bool) -> str:
    return PUBLIC_HTTPS_PREREQUISITE if owner_sign_in else f"{PUBLIC_HTTPS_PREREQUISITE} Set up owner sign-in for OAuth."


SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_keys (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  project TEXT,
  client TEXT,
  created_by TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_used_at REAL,
  revoked_at REAL
);
CREATE INDEX IF NOT EXISTS agent_keys_agent ON agent_keys(agent);
"""


class ConnectError(ValueError):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def new_code() -> tuple[str, str]:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}", _sha(raw)


class Connections:
    def __init__(self, store: Store, key_scopes: dict[str, tuple[str, ...]] | None = None):
        self.store = store
        self.key_scopes = key_scopes if key_scopes is not None else {}
        with store._lock:
            store.db.executescript(SCHEMA)

    def migrate_memory_grants(self, timezone: ZoneInfo) -> tuple[int, int] | None:
        now = self.store.clock()
        day = datetime.fromtimestamp(now, timezone).date().isoformat()

        def run(db: sqlite3.Connection) -> tuple[int, int] | None:
            if db.execute("SELECT 1 FROM paired_memory_grants_migration WHERE version = 1").fetchone():
                return None
            keys = db.execute("SELECT id, agent FROM agent_keys WHERE revoked_at IS NULL").fetchall()
            broad = {"general", *("project:" + p["slug"] for p in db.execute("SELECT slug FROM projects"))}
            count = 0
            for key in keys:
                limits = self.key_scopes.get(key["agent"])
                scopes = broad if limits is None or "*" in limits else set(limits)
                count += len(scopes)
                db.executemany("INSERT INTO paired_memory_grants(key_id, scope) VALUES (?, ?)",
                               ((key["id"], scope) for scope in sorted(scopes)))
                db.execute(
                    """INSERT INTO audit(at, day, origin, source, client, destination, action, target, outcome, detail)
                       VALUES (?, ?, 'startup', 'system', 'migration', 'core', 'memory_grants_migrated', ?, 'ok',
                               'Preserved existing paired key memory access.')""",
                    (now, day, key["id"]),
                )
            db.execute("INSERT INTO paired_memory_grants_migration(version, keys_migrated, grants_migrated) VALUES (1, ?, ?)",
                       (len(keys), count))
            return len(keys), count

        return self.store.tx(run)

    def memory_scopes(self, key_id: str) -> tuple[str, ...]:
        return tuple(row["scope"] for row in self.store.q(
            "SELECT scope FROM paired_memory_grants WHERE key_id = ? ORDER BY scope", (key_id,)))

    def remove_memory_scope(self, key_id: str, scope: str) -> bool | None:
        def run(db: sqlite3.Connection) -> bool | None:
            if db.execute("SELECT 1 FROM agent_keys WHERE id = ? AND revoked_at IS NULL", (key_id,)).fetchone() is None:
                return None
            return bool(db.execute("DELETE FROM paired_memory_grants WHERE key_id = ? AND scope = ?", (key_id, scope)).rowcount)

        return self.store.tx(run)

    def mint(self, *, agent: str, project: str | None, client: str | None, by: str, ttl_s: float = CODE_TTL_S) -> dict[str, Any]:
        agent = (agent or "").strip().lower()
        if not AGENT_RE.match(agent) or agent in RESERVED:
            raise ConnectError(f"agent must be lowercase letters, digits and dashes (not {', '.join(sorted(RESERVED - {'-'}))})")
        code, code_hash = new_code()
        now = self.store.clock()
        self.store.x("INSERT INTO connect_codes(code_hash, agent, project, client, created_by, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                     (code_hash, agent, project, client, by, now, now + ttl_s))
        return {"code": code, "agent": agent, "project": project, "client": client, "expires_in_s": int(ttl_s)}

    def redeem(self, code: str) -> dict[str, Any]:
        raw = normalize_code(code)

        def run(db: sqlite3.Connection) -> dict[str, Any]:
            row = db.execute("SELECT * FROM connect_codes WHERE code_hash = ?", (_sha(raw),)).fetchone() if raw else None
            now = self.store.clock()
            if row is None or row["used_at"] is not None or row["expires_at"] < now:
                raise ConnectError("that code is unknown, used or expired; mint a new one")
            key = KEY_PREFIX + secrets.token_urlsafe(32)
            kid = "ak_" + secrets.token_hex(6)
            db.execute("UPDATE connect_codes SET used_at = ? WHERE code_hash = ?", (now, row["code_hash"]))
            db.execute("INSERT INTO agent_keys(id, agent, key_hash, project, client, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                       (kid, row["agent"], _sha(key), row["project"], row["client"], row["created_by"], now))
            scopes = {f"agent:{row['agent']}", *self.key_scopes.get(row["agent"], ())}
            if row["project"]:
                scopes.add("project:" + row["project"].strip().lower().removeprefix("project:"))
            if "*" in scopes:
                scopes.remove("*")
                scopes.update({"general", *("project:" + p["slug"] for p in db.execute("SELECT slug FROM projects WHERE private = 0"))})
            db.executemany("INSERT INTO paired_memory_grants(key_id, scope) VALUES (?, ?)",
                           ((kid, scope) for scope in sorted(scopes)))
            return {"agent": row["agent"], "project": row["project"], "client": row["client"], "key_id": kid, "key": key}

        return self.store.tx(run)

    def verify(self, token: str) -> dict[str, Any] | None:
        if not token.startswith(KEY_PREFIX):
            return None
        row = self.store.one("SELECT * FROM agent_keys WHERE key_hash = ? AND revoked_at IS NULL", (_sha(token),))
        if row is None:
            return None
        now = self.store.clock()
        if not row["last_used_at"] or now - row["last_used_at"] > 60:
            self.store.n("UPDATE agent_keys SET last_used_at = ? WHERE id = ?", (now, row["id"]))
        return dict(row)

    def agents(self) -> list[str]:
        return [r["agent"] for r in self.store.q("SELECT DISTINCT agent FROM agent_keys WHERE revoked_at IS NULL")]

    def list(self, include_revoked: bool = False) -> list[dict[str, Any]]:
        rows = self.store.q("SELECT id, agent, project, client, created_by, created_at, last_used_at, revoked_at FROM agent_keys"
                            + ("" if include_revoked else " WHERE revoked_at IS NULL") + " ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    def revoke(self, *, key_id: str | None = None, agent: str | None = None) -> int | None:
        if not key_id and not agent:
            raise ConnectError("pass key_id or agent")
        now = self.store.clock()
        if key_id:
            revoked = self.store.n("UPDATE agent_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, key_id))
            return revoked if revoked or self.store.one("SELECT 1 FROM agent_keys WHERE id = ?", (key_id,)) else None
        return self.store.n("UPDATE agent_keys SET revoked_at = ? WHERE agent = ? AND revoked_at IS NULL", (now, (agent or "").lower()))
