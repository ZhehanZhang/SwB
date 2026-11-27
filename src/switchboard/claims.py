"""One-time owner links for storing credentials by environment variable name."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
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


GROUP_SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_groups (
  code_hash TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  fields TEXT NOT NULL,
  expires_at REAL NOT NULL
)
"""
KINDS = ("token", "id", "url", "pem", "json")
FILE_KINDS = ("pem", "json")
MAX_FILE_BYTES = 64 * 1024
PEM_RE = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.S)


class ClaimError(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    """One credential in a grouped claim: the environment variable it fills, how it is checked, and its label."""

    name: str
    label: str
    kind: str = "token"
    hint: str = ""
    optional: bool = False
    link: str = ""


def normalize_pem(value: str) -> str:
    """Rebuild a PEM private key that lost its line breaks (a paste into a one-line field) and check that it loads."""
    found = PEM_RE.search(value)
    body = re.sub(r"\s+", "", found.group(2)) if found else ""
    if not body or not re.fullmatch(r"[A-Za-z0-9+/]+=*", body):
        raise ClaimError("Paste the whole private key, from -----BEGIN to -----END, or upload the .pem file.")
    label = found.group(1)
    pem = f"-----BEGIN {label}-----\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64)) + f"\n-----END {label}-----\n"
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as e:
        raise ClaimError("That private key doesn't load. Use the unencrypted .pem file GitHub gave you.") from e
    return pem


def normalize(kind: str, value: str) -> str:
    value = value.strip()
    if kind in FILE_KINDS:
        if len(value.encode()) > MAX_FILE_BYTES or "\x00" in value:
            raise ClaimError("That file is too large; the limit is 64 KiB.")
        if kind == "pem":
            return normalize_pem(value)
        try:
            parsed = json.loads(value)
        except ValueError as e:
            raise ClaimError("That isn't valid JSON.") from e
        if not isinstance(parsed, dict):
            raise ClaimError("Expected a JSON object.")
        return value
    if not value or len(value.encode()) > 8192 or any(c in value for c in "\r\n\x00"):
        raise ClaimError("Enter it on a single line, at most 8 KiB.")
    if kind == "id" and not re.fullmatch(r"\d{1,20}", value):
        raise ClaimError("Enter the number only.")
    if kind == "url" and not valid_webhook_url(value):
        raise ClaimError("Enter a public HTTPS URL without credentials or a fragment.")
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
        store.x(SCHEMA)
        store.x(GROUP_SCHEMA)
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
        return self.inspect_details(code)["name"]

    def inspect_details(self, code: str) -> dict[str, Any]:
        row = self.store.one("SELECT name, expires_at, require_https FROM secrets WHERE code_hash = ? AND expires_at > ?",
                             (_digest(code), self.store.clock()))
        if row is None:
            raise ClaimUnavailable(UNUSABLE)
        return dict(row)

    def pending(self, name: str) -> bool:
        return self.store.one("SELECT 1 FROM secrets WHERE name = ? AND code_hash IS NOT NULL AND expires_at > ?",
                              (name, self.store.clock())) is not None

    def consume(self, code: str, value: str) -> str:
        code_hash = _digest(code)
        value = value.strip()
        if not value or len(value.encode()) > 8192 or any(c in value for c in "\r\n\x00"):
            raise ClaimError("Enter a token on a single line, at most 8 KiB.")
        claim = self.store.one("SELECT require_https FROM secrets WHERE code_hash = ? AND expires_at > ?",
                               (code_hash, self.store.clock()))
        if claim is None:
            raise ClaimUnavailable(UNUSABLE)
        if claim["require_https"] and not valid_webhook_url(value):
            raise ClaimError("Enter a public HTTPS webhook URL without credentials or a fragment.")
        row = self.store.one(
            """UPDATE secrets SET code_hash = NULL, expires_at = NULL, sealed = ?
               WHERE code_hash = ? AND expires_at > ? RETURNING name""",
            (self._cipher.encrypt(value.encode()), code_hash, self.store.clock()),
        )
        if row is None:
            raise ClaimUnavailable(UNUSABLE)
        return row["name"]

    def mint_group(self, title: str, fields: list[Field], public_url: str | None, provider: str = "") -> str:
        """One link whose form saves every field together, after each one is checked."""
        names = [f.name for f in fields]
        if not fields or len(set(names)) != len(names) or not all(NAME_RE.fullmatch(n) and f.kind in KINDS for n, f in zip(names, fields)):
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

    def consume_group(self, code: str, values: dict[str, str]) -> list[str]:
        found = self.group(code)
        if found is None:
            raise ClaimUnavailable(UNUSABLE)
        sealed = {}
        for f in found["fields"]:
            raw = values.get(f.name) or ""
            if not raw.strip():
                if f.optional:
                    continue
                raise ClaimError(f"{f.label} is missing.")
            try:
                sealed[f.name] = self._cipher.encrypt(normalize(f.kind, raw).encode())
            except ClaimError as e:
                raise ClaimError(f"{f.label}: {e}") from None

        def save(db: Any) -> None:
            if db.execute("DELETE FROM claim_groups WHERE code_hash = ? AND expires_at > ? RETURNING code_hash",
                          (_digest(code), self.store.clock())).fetchone() is None:
                raise ClaimUnavailable(UNUSABLE)
            for name, value in sealed.items():
                db.execute("""INSERT INTO secrets(name, sealed) VALUES (?, ?)
                              ON CONFLICT(name) DO UPDATE SET sealed = excluded.sealed, code_hash = NULL, expires_at = NULL""", (name, value))

        self.store.tx(save)
        return list(sealed)

    def secret(self, name: str) -> str | None:
        row = self.store.one("SELECT sealed FROM secrets WHERE name = ?", (name,))
        return self._cipher.decrypt(row["sealed"]).decode() if row is not None and row["sealed"] is not None else None
