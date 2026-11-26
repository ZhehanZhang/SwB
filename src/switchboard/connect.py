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
from typing import Any

from .store import Store

AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RESERVED = frozenset({"slack", "local", "mailbox", "agent", "auto", "admin", "config", "core", "memory", "-"})
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_TTL_S = 600
KEY_PREFIX = "swb_"

SCHEMA = """
CREATE TABLE IF NOT EXISTS connect_codes (
  code_hash TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  project TEXT,
  client TEXT,
  created_by TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  used_at REAL
);
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


class Connections:
    def __init__(self, store: Store):
        self.store = store
        with store._lock:
            store.db.executescript(SCHEMA)

    def mint(self, *, agent: str, project: str | None, client: str | None, by: str, ttl_s: float = CODE_TTL_S) -> dict[str, Any]:
        agent = (agent or "").strip().lower()
        if not AGENT_RE.match(agent) or agent in RESERVED:
            raise ConnectError(f"agent must be lowercase letters, digits and dashes (not {', '.join(sorted(RESERVED - {'-'}))})")
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
        now = self.store.clock()
        self.store.x("INSERT INTO connect_codes(code_hash, agent, project, client, created_by, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                           (_sha(raw), agent, project, client, by, now, now + ttl_s))
        return {"code": f"{raw[:5]}-{raw[5:]}", "agent": agent, "project": project, "client": client, "expires_in_s": int(ttl_s)}

    def redeem(self, code: str) -> dict[str, Any]:
        raw = normalize_code(code)
        row = self.store.one("SELECT * FROM connect_codes WHERE code_hash = ?", (_sha(raw),)) if raw else None
        now = self.store.clock()
        if row is None or row["used_at"] is not None or row["expires_at"] < now:
            raise ConnectError("that code is unknown, used or expired; mint a new one")
        if not self.store.n("UPDATE connect_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL", (now, row["code_hash"])):
            raise ConnectError("that code was just used; mint a new one")
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        kid = "ak_" + secrets.token_hex(6)
        self.store.x("INSERT INTO agent_keys(id, agent, key_hash, project, client, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                           (kid, row["agent"], _sha(key), row["project"], row["client"], row["created_by"], now))
        return {"agent": row["agent"], "project": row["project"], "client": row["client"], "key_id": kid, "key": key}

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

    def revoke(self, *, key_id: str | None = None, agent: str | None = None) -> int:
        if not key_id and not agent:
            raise ConnectError("pass key_id or agent")
        now = self.store.clock()
        if key_id:
            return self.store.n("UPDATE agent_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, key_id))
        else:
            return self.store.n("UPDATE agent_keys SET revoked_at = ? WHERE agent = ? AND revoked_at IS NULL", (now, (agent or "").lower()))
