"""One-time owner links for storing credentials by environment variable name."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.serialization import load_pem_private_key

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
  require_https INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'token',
  label TEXT NOT NULL DEFAULT ''
)
"""

LINK_SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_links (
  code_hash TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  expires_at REAL NOT NULL,
  require_https INTEGER NOT NULL,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
)
"""

GROUP_SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_groups (
  code_hash TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  fields TEXT NOT NULL,
  expires_at REAL NOT NULL
)
"""
KINDS = ("token", "id", "url", "pem", "json", "webhook_secret", "webhook_key")
FILE_KINDS = ("pem", "json")
MAX_FILE_BYTES = 64 * 1024
PEM_RE = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.S)
ClaimCheck = Callable[[str], object]
ClaimOnlineCheck = Callable[[str], Awaitable[str | None]]


class ClaimError(ValueError):
    pass


class ClaimInputError(ClaimError):
    """A validation title safe to show to the claim owner."""


@dataclass(frozen=True)
class Field:
    """One credential in a grouped claim: the environment variable it fills, how it is checked, and its label."""

    name: str
    label: str
    kind: str = "token"
    hint: str = ""
    optional: bool = False
    link: str = ""


class GroupFieldError(ClaimError):
    def __init__(self, field: Field, guidance: str = ""):
        self.field = field
        self.guidance = guidance
        super().__init__(f"Check {field.label}")


def normalize_pem(value: str, *, check: ClaimCheck | None = None) -> str:
    """Rebuild a PEM private key that lost its line breaks (a paste into a one-line field) and check that it loads."""
    found = PEM_RE.search(value)
    body = re.sub(r"\s+", "", found.group(2)) if found else ""
    if not body or not re.fullmatch(r"[A-Za-z0-9+/]+=*", body):
        raise ClaimInputError("Paste the full private key or upload the .pem file")
    label = found.group(1)
    pem = f"-----BEGIN {label}-----\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64)) + f"\n-----END {label}-----\n"
    if check is None:
        try:
            load_pem_private_key(pem.encode(), password=None)
        except (ValueError, TypeError, UnsupportedAlgorithm):
            raise ClaimInputError("Use a valid .pem file") from None
    else:
        check(pem)
    return pem


def normalize(kind: str, value: str, *, check: ClaimCheck | None = None) -> str:
    if kind == "webhook_secret" and any(c.isspace() for c in value):
        raise ClaimInputError("Enter a continuous webhook secret")
    value = value.strip()
    if kind in FILE_KINDS:
        if len(value.encode()) > MAX_FILE_BYTES or "\x00" in value:
            raise ClaimInputError("Use a text file up to 64 KiB")
        if kind == "pem":
            return normalize_pem(value, check=check)
        try:
            parsed = json.loads(value)
        except ValueError:
            raise ClaimInputError("Use a JSON object") from None
        if not isinstance(parsed, dict):
            raise ClaimInputError("Use a JSON object")
    elif not value or len(value.encode()) > 8192 or any(c in value for c in "\r\n\x00"):
        raise ClaimInputError("Enter a single-line value up to 8 KiB")
    if kind == "id" and not re.fullmatch(r"[0-9]+", value):
        raise ClaimInputError("Enter a numeric ID")
    if kind in ("webhook_secret", "webhook_key") and len(value) < 16:
        raise ClaimInputError(f"Use a {kind.replace('_', ' ')} with at least 16 characters")
    if kind == "url" and not valid_webhook_url(value):
        raise ClaimInputError("Enter a public HTTPS webhook URL")
    if check is not None:
        check(value)
    return value


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
        self._name_checks: dict[str, ClaimCheck] = {}
        self._kind_checks: dict[str, ClaimCheck] = {}
        self._online_checks: dict[str, ClaimOnlineCheck] = {}
        store.x(SCHEMA)
        store.x(LINK_SCHEMA)
        if "resolved" not in {r["name"] for r in store.q("PRAGMA table_info(claim_links)")}:
            store.x("ALTER TABLE claim_links ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0")
        store.x("CREATE INDEX IF NOT EXISTS claim_links_name ON claim_links(name, expires_at)")
        store.x(GROUP_SCHEMA)
        columns = {r["name"] for r in store.q("PRAGMA table_info(secrets)")}
        for name, definition in (("require_https", "INTEGER NOT NULL DEFAULT 0"),
                                 ("kind", "TEXT NOT NULL DEFAULT 'token'"), ("label", "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                store.x(f"ALTER TABLE secrets ADD COLUMN {name} {definition}")

        def migrate(db: Any) -> None:
            db.execute("""INSERT OR IGNORE INTO claim_links(code_hash, name, expires_at, require_https, kind, label)
                          SELECT code_hash, name, expires_at, COALESCE(require_https, 0), kind, label FROM secrets
                          WHERE code_hash IS NOT NULL AND expires_at > ?""", (store.clock(),))
            db.execute("UPDATE secrets SET code_hash = NULL, expires_at = NULL WHERE code_hash IS NOT NULL")

        store.tx(migrate)

    def register_name(self, name: str, check: ClaimCheck) -> None:
        self._name_checks[name] = check

    def register_kind(self, kind: str, check: ClaimCheck, *, check_online: ClaimOnlineCheck | None = None) -> None:
        """Register local validation and an optional check that returns a safe error title."""
        self._kind_checks[kind] = check
        if check_online is not None:
            self._online_checks[kind] = check_online
        else:
            self._online_checks.pop(kind, None)

    def _known_kind(self, kind: str) -> bool:
        return kind in KINDS or kind in self._kind_checks

    def _normalize(self, name: str, kind: str, value: str) -> str:
        if not self._known_kind(kind):
            raise ClaimUnavailable(UNUSABLE)
        value = normalize(kind, value, check=self._name_checks.get(name))
        if check := self._kind_checks.get(kind):
            check(value)
        return value

    def mint(self, name: str, public_url: str | None, *, url: bool = False, kind: str = "token", label: str = "") -> str:
        if not NAME_RE.fullmatch(name):
            raise ClaimError("A claim needs an environment variable name.")
        if not public_url:
            raise ClaimError("A claim link needs public_url configured.")
        if not self._known_kind(kind):
            raise ClaimError("Choose a credential kind.")
        code = secrets.token_urlsafe(32)
        now = self.store.clock()

        def register(db: Any) -> None:
            db.execute("DELETE FROM claim_links WHERE expires_at <= ?", (now,))
            db.execute("INSERT INTO claim_links(code_hash, name, expires_at, require_https, kind, label) VALUES (?, ?, ?, ?, ?, ?)",
                       (_digest(code), name, now + CLAIM_TTL_S, url, "url" if url else kind, label))

        self.store.tx(register)
        return f"{public_url.rstrip('/')}{CLAIM_PATH}/{code}"

    def inspect(self, code: str) -> str:
        return self.inspect_details(code)["name"]

    def inspect_details(self, code: str) -> dict[str, Any]:
        row = self.store.one("SELECT name, expires_at, require_https, kind, label FROM claim_links WHERE code_hash = ? AND expires_at > ?",
                             (_digest(code), self.store.clock()))
        if row is None:
            raise ClaimUnavailable(UNUSABLE)
        return dict(row)

    def pending(self, name: str) -> bool:
        return self.store.one("SELECT 1 FROM claim_links WHERE name = ? AND expires_at > ? AND resolved = 0 LIMIT 1",
                              (name, self.store.clock())) is not None

    async def submit(self, code: str, value: str) -> str:
        claim = self.inspect_details(code)
        value = self._single_value(claim, value)
        if (check := self._online_checks.get(claim["kind"])) and (error := await check(value)):
            raise ClaimInputError(error)
        return self._save(code, value)

    def consume(self, code: str, value: str) -> str:
        return self._save(code, self._single_value(self.inspect_details(code), value))

    def _single_value(self, claim: dict[str, Any], value: str) -> str:
        kind = "url" if claim["require_https"] else claim["kind"]
        if kind == "token" and PEM_RE.search(value):
            kind = "pem"
        return self._normalize(claim["name"], kind, value)

    def _save(self, code: str, value: str) -> str:
        digest, sealed = _digest(code), self._cipher.encrypt(value.encode())

        def save(db: Any) -> str:
            row = db.execute("DELETE FROM claim_links WHERE code_hash = ? AND expires_at > ? RETURNING rowid, name, require_https",
                             (digest, self.store.clock())).fetchone()
            if row is None:
                raise ClaimUnavailable(UNUSABLE)
            db.execute("UPDATE claim_links SET resolved = 1 WHERE name = ? AND rowid < ?", (row["name"], row["rowid"]))
            db.execute("""INSERT INTO secrets(name, sealed, require_https) VALUES (?, ?, ?)
                          ON CONFLICT(name) DO UPDATE SET sealed = excluded.sealed, require_https = excluded.require_https""",
                       (row["name"], sealed, row["require_https"]))
            return row["name"]

        return self.store.tx(save)

    def mint_group(self, title: str, fields: list[Field], public_url: str | None, provider: str = "") -> str:
        """One link whose form saves every field together, after each one is checked."""
        names = [f.name for f in fields]
        if not fields or len(set(names)) != len(names) or not all(NAME_RE.fullmatch(f.name) and self._known_kind(f.kind) for f in fields):
            raise ClaimError("A grouped claim needs distinct environment variable names.")
        if not public_url:
            raise ClaimError("A claim link needs public_url configured.")
        code = secrets.token_urlsafe(32)
        self.store.x("INSERT INTO claim_groups(code_hash, title, provider, fields, expires_at) VALUES (?, ?, ?, ?, ?)",
                     (_digest(code), title, provider, json.dumps([asdict(f) for f in fields]), self.store.clock() + CLAIM_TTL_S))
        return f"{public_url.rstrip('/')}{CLAIM_PATH}/{code}"

    def group(self, code: str) -> dict[str, Any] | None:
        """The pending grouped claim behind a code: title, provider, fields and expiry, or None."""
        if not CODE_RE.fullmatch(code):
            return None
        row = self.store.one("SELECT title, provider, fields, expires_at FROM claim_groups WHERE code_hash = ? AND expires_at > ?",
                             (_digest(code), self.store.clock()))
        if row is None:
            return None
        return {"title": row["title"], "provider": row["provider"], "expires_at": row["expires_at"],
                "fields": [Field(**f) for f in json.loads(row["fields"])]}

    async def consume_group(self, code: str, values: dict[str, str]) -> list[str]:
        found = self.group(code)
        if found is None:
            raise ClaimUnavailable(UNUSABLE)
        normalized = []
        for f in found["fields"]:
            raw = values.get(f.name) or ""
            if not raw.strip():
                if f.optional:
                    continue
                raise GroupFieldError(f)
            try:
                normalized.append((f, self._normalize(f.name, f.kind, raw)))
            except ClaimInputError as e:
                raise GroupFieldError(f, str(e)) from None
            except ClaimUnavailable:
                raise
            except ClaimError:
                raise GroupFieldError(f) from None
        online = [(f, value, check) for f, value in normalized if (check := self._online_checks.get(f.kind))]
        errors = await asyncio.gather(*(check(value) for _, value, check in online))
        for (f, _, _), error in zip(online, errors):
            if error:
                raise GroupFieldError(f, error)
        sealed = {f.name: self._cipher.encrypt(value.encode()) for f, value in normalized}

        def save(db: Any) -> None:
            if db.execute("DELETE FROM claim_groups WHERE code_hash = ? AND expires_at > ? RETURNING code_hash",
                          (_digest(code), self.store.clock())).fetchone() is None:
                raise ClaimUnavailable(UNUSABLE)
            for name, value in sealed.items():
                db.execute("UPDATE claim_links SET resolved = 1 WHERE name = ? AND expires_at <= ?", (name, found["expires_at"]))
                db.execute("""INSERT INTO secrets(name, sealed) VALUES (?, ?)
                              ON CONFLICT(name) DO UPDATE SET sealed = excluded.sealed""", (name, value))

        self.store.tx(save)
        return list(sealed)

    def secret(self, name: str) -> str | None:
        row = self.store.one("SELECT sealed FROM secrets WHERE name = ?", (name,))
        return self._cipher.decrypt(row["sealed"]).decode() if row is not None and row["sealed"] is not None else None
