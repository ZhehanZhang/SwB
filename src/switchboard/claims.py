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

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
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
  require_https INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'token',
  label TEXT NOT NULL DEFAULT ''
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
SLACK_PREFIXES = {"slack_bot": "xoxb-", "slack_user": "xoxp-", "slack_app": "xapp-"}
KINDS = ("token", "id", "url", "pem", "json", "webhook_secret", *SLACK_PREFIXES)
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


class GroupFieldError(ClaimError):
    def __init__(self, field: Field, message: str):
        self.field = field
        super().__init__(message)


def normalize_pem(value: str) -> str:
    """Rebuild a PEM private key that lost its line breaks (a paste into a one-line field) and check that it loads."""
    found = PEM_RE.search(value)
    body = re.sub(r"\s+", "", found.group(2)) if found else ""
    if not body or not re.fullmatch(r"[A-Za-z0-9+/]+=*", body):
        raise ClaimError("Paste the full private key or upload the .pem file")
    label = found.group(1)
    pem = f"-----BEGIN {label}-----\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64)) + f"\n-----END {label}-----\n"
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as e:
        raise ClaimError("Use the unencrypted .pem file from GitHub") from e
    return pem


def normalize(kind: str, value: str) -> str:
    value = value.strip()
    if kind in FILE_KINDS:
        if len(value.encode()) > MAX_FILE_BYTES or "\x00" in value:
            raise ClaimError("Use a text file up to 64 KiB")
        if kind == "pem":
            return normalize_pem(value)
        try:
            parsed = json.loads(value)
        except ValueError as e:
            raise ClaimError("Use a JSON object") from e
        if not isinstance(parsed, dict):
            raise ClaimError("Use a JSON object")
        return value
    if not value or len(value.encode()) > 8192 or any(c in value for c in "\r\n\x00"):
        raise ClaimError("Enter it on a single line, at most 8 KiB.")
    if kind == "id" and not re.fullmatch(r"[1-9][0-9]{0,23}", value):
        raise ClaimError("Enter a numeric App ID")
    if kind == "webhook_secret" and len(value) < 16:
        raise ClaimError("Use a webhook secret with at least 16 characters")
    if (prefix := SLACK_PREFIXES.get(kind)) and not re.fullmatch(prefix + r"[A-Za-z0-9_-]+", value):
        raise ClaimError(f"Use a Slack token that starts with {prefix}")
    if kind == "url" and not valid_webhook_url(value):
        raise ClaimError("Enter a public HTTPS URL without credentials or a fragment.")
    return value


async def check_slack_auth(kind: str, token: str) -> bool | None:
    method = "apps.connections.open" if kind == "slack_app" else "auth.test"
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            response = await http.post(f"https://slack.com/api/{method}", headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 429 or response.status_code >= 500:
            return None
        data = response.json()
    except (httpx.RequestError, ValueError):
        return None
    if isinstance(data, dict) and data.get("error") not in ("internal_error", "fatal_error", "service_unavailable", "ratelimited"):
        if isinstance(ok := data.get("ok"), bool):
            return ok
    return None


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
    def __init__(self, store: Store, *, slack_auth_check: Callable[[str, str], Awaitable[bool | None]] = check_slack_auth):
        self.store = store
        self._cipher = Fernet(_key(store))
        self.validators: dict[str, tuple[re.Pattern[str], str]] = {}
        self.slack_auth_check = slack_auth_check
        store.x(SCHEMA)
        store.x(GROUP_SCHEMA)
        columns = {r["name"] for r in store.q("PRAGMA table_info(secrets)")}
        for name, definition in (("require_https", "INTEGER NOT NULL DEFAULT 0"),
                                 ("kind", "TEXT NOT NULL DEFAULT 'token'"), ("label", "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                store.x(f"ALTER TABLE secrets ADD COLUMN {name} {definition}")

    def mint(self, name: str, public_url: str | None, *, url: bool = False, kind: str = "token", label: str = "") -> str:
        if not NAME_RE.fullmatch(name):
            raise ClaimError("A claim needs an environment variable name.")
        if not public_url:
            raise ClaimError("A claim link needs public_url configured.")
        if kind not in KINDS:
            raise ClaimError("Choose a credential kind.")
        code = secrets.token_urlsafe(32)
        self.store.x(
            """INSERT INTO secrets(name, code_hash, expires_at, require_https, kind, label) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET code_hash = excluded.code_hash, expires_at = excluded.expires_at,
                                               require_https = excluded.require_https, kind = excluded.kind, label = excluded.label""",
            (name, _digest(code), self.store.clock() + CLAIM_TTL_S, url, "url" if url else kind, label),
        )
        return f"{public_url.rstrip('/')}{CLAIM_PATH}/{code}"

    def inspect(self, code: str) -> str:
        return self.inspect_details(code)["name"]

    def inspect_details(self, code: str) -> dict[str, Any]:
        row = self.store.one("SELECT name, expires_at, require_https, kind, label FROM secrets WHERE code_hash = ? AND expires_at > ?",
                             (_digest(code), self.store.clock()))
        if row is None:
            raise ClaimUnavailable(UNUSABLE)
        return dict(row)

    def pending(self, name: str) -> bool:
        return self.store.one("SELECT 1 FROM secrets WHERE name = ? AND code_hash IS NOT NULL AND expires_at > ?",
                              (name, self.store.clock())) is not None

    def consume(self, code: str, value: str) -> str:
        code_hash = _digest(code)
        claim = self.inspect_details(code)
        kind = "url" if claim["require_https"] else claim["kind"]
        if kind == "token" and PEM_RE.search(value):
            kind = "pem"
        value = normalize(kind, value)
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
                raise GroupFieldError(f, f"{f.label} is missing.")
            try:
                normalized.append((f, normalize(f.kind, raw)))
            except ClaimError as e:
                raise GroupFieldError(f, str(e)) from None
        slack = [(f, token) for f, token in normalized if f.kind in SLACK_PREFIXES]
        checked = await asyncio.gather(*(self.slack_auth_check(f.kind, token) for f, token in slack))
        for (f, _), result in zip(slack, checked):
            if result is False:
                raise GroupFieldError(f, "Use a token accepted by Slack")
        sealed = {f.name: self._cipher.encrypt(value.encode()) for f, value in normalized}

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
