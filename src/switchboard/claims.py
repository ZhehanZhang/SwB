"""One-time owner links for storing credentials by environment variable name."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from .store import Store

CLAIM_PATH = "/claim"
CLAIM_TTL_S = 30 * 60
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
CODE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
UNUSABLE = "This link is invalid, expired, or already used. Ask for a new link."
SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
  name TEXT PRIMARY KEY,
  code_hash TEXT UNIQUE,
  expires_at REAL,
  sealed BLOB,
  require_https INTEGER NOT NULL DEFAULT 0
)
"""


class ClaimError(ValueError):
    pass


class ClaimUnavailable(ClaimError):
    pass


def _key(store: Store) -> bytes:
    if store.path == ":memory:":
        return Fernet.generate_key()
    path = Path(store.path).with_name("claim-key")
    try:
        return path.read_bytes()
    except FileNotFoundError:
        with NamedTemporaryFile(dir=path.parent) as temp:
            temp.write(Fernet.generate_key())
            temp.flush()
            try:
                os.link(temp.name, path)
            except FileExistsError:
                pass
        return path.read_bytes()


def _digest(code: str) -> str:
    if not CODE_RE.fullmatch(code):
        raise ClaimUnavailable(UNUSABLE)
    return hashlib.sha256(code.encode()).hexdigest()


def valid_webhook_url(value: str) -> bool:
    if any(c.isspace() or ord(c) < 32 or c in "\\#" for c in value):
        return False
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return False
    host = (url.hostname or "").rstrip(".")
    if url.scheme != "https" or url.username is not None or port == 0 or not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_global and not ip.is_multicast
    except ValueError:
        return (bool(re.fullmatch(r"[a-z0-9.-]+", host)) and "." in host and not host.rsplit(".", 1)[-1].isdigit()
                and not host.endswith((".localhost", ".local", ".internal", ".home.arpa")))


class Claims:
    def __init__(self, store: Store):
        self.store = store
        self._cipher = Fernet(_key(store))
        self.validators: dict[str, tuple[re.Pattern[str], str]] = {}
        store.x(SCHEMA)
        if "require_https" not in {r["name"] for r in store.q("PRAGMA table_info(secrets)")}:
            store.x("ALTER TABLE secrets ADD COLUMN require_https INTEGER NOT NULL DEFAULT 0")

    def mint(self, name: str, public_url: str | None, *, url: bool = False) -> str:
        if not NAME_RE.fullmatch(name):
            raise ClaimError("A claim needs an environment variable name.")
        if not public_url:
            raise ClaimError("A claim link needs public_url configured.")
        code = secrets.token_urlsafe(32)
        self.store.x(
            """INSERT INTO secrets(name, code_hash, expires_at, require_https) VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET code_hash = excluded.code_hash, expires_at = excluded.expires_at,
                                               require_https = excluded.require_https""",
            (name, _digest(code), self.store.clock() + CLAIM_TTL_S, url),
        )
        return f"{public_url.rstrip('/')}{CLAIM_PATH}/{code}"

    def inspect(self, code: str) -> str:
        row = self.store.one("SELECT name FROM secrets WHERE code_hash = ? AND expires_at > ?",
                             (_digest(code), self.store.clock()))
        if row is None:
            raise ClaimUnavailable(UNUSABLE)
        return row["name"]

    def consume(self, code: str, value: str) -> str:
        code_hash = _digest(code)
        value = value.strip()
        if not value or len(value.encode()) > 8192 or any(c in value for c in "\r\n\x00"):
            raise ClaimError("Enter a token on a single line, at most 8 KiB.")
        claim = self.store.one("SELECT name, require_https FROM secrets WHERE code_hash = ? AND expires_at > ?",
                               (code_hash, self.store.clock()))
        if claim is None:
            raise ClaimUnavailable(UNUSABLE)
        if claim["require_https"] and not valid_webhook_url(value):
            raise ClaimError("Enter a public HTTPS webhook URL without credentials or a fragment.")
        if (validation := self.validators.get(claim["name"])) and not validation[0].fullmatch(value):
            raise ClaimError(validation[1])
        row = self.store.one(
            """UPDATE secrets SET code_hash = NULL, expires_at = NULL, sealed = ?
               WHERE code_hash = ? AND expires_at > ? RETURNING name""",
            (self._cipher.encrypt(value.encode()), code_hash, self.store.clock()),
        )
        if row is None:
            raise ClaimUnavailable(UNUSABLE)
        return row["name"]

    def secret(self, name: str) -> str | None:
        row = self.store.one("SELECT sealed FROM secrets WHERE name = ?", (name,))
        return self._cipher.decrypt(row["sealed"]).decode() if row is not None and row["sealed"] is not None else None
