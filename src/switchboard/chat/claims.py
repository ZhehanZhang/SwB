"""One-time links where the owner enters a block's token, the same pattern as routine claims.

An owner-only tool opens a claim for a named secret (a bot token). The link works once, for 30 minutes, and only for the
signed-in owner; the token is stored sealed (AES-GCM, key in the data dir) and never shown again. A token
in the environment always wins over a stored one.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from typing import TYPE_CHECKING, Any, Callable

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

if TYPE_CHECKING:
    from ..core import Switchboard

CLAIM_TTL_S = 1800
SCHEMA = """
CREATE TABLE IF NOT EXISTS block_secrets (block TEXT NOT NULL, name TEXT NOT NULL, sealed TEXT NOT NULL, set_at REAL NOT NULL, set_by TEXT NOT NULL,
  PRIMARY KEY (block, name));
CREATE TABLE IF NOT EXISTS block_claims (code_hash TEXT PRIMARY KEY, block TEXT NOT NULL, name TEXT NOT NULL, created_by TEXT NOT NULL,
  created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL);
"""


class ClaimError(Exception):
    pass


def _sha(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class Claims:
    def __init__(self, core: "Switchboard", block: str):
        self.core, self.block = core, block
        core.store.db.executescript(SCHEMA)

    def _key(self) -> bytes:
        path = self.core.data_dir / "block-secrets-key"
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
            path.chmod(0o600)
        return path.read_bytes()

    def _aad(self, name: str) -> bytes:
        return f"switchboard-block-secret:{self.block}:{name}".encode()

    def get(self, name: str) -> str | None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        row = self.core.store.one("SELECT sealed FROM block_secrets WHERE block = ? AND name = ?", (self.block, name))
        if row is None:
            return None
        try:
            raw = base64.b64decode(row["sealed"])
            return AESGCM(self._key()).decrypt(raw[:12], raw[12:], self._aad(name)).decode()
        except Exception:
            return None

    def put(self, name: str, value: str, actor: str) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        sealed = base64.b64encode(nonce + AESGCM(self._key()).encrypt(nonce, value.encode(), self._aad(name))).decode()
        self.core.store.x("INSERT INTO block_secrets(block, name, sealed, set_at, set_by) VALUES (?,?,?,?,?) ON CONFLICT(block, name) DO UPDATE SET "
                          "sealed = excluded.sealed, set_at = excluded.set_at, set_by = excluded.set_by", (self.block, name, sealed, self.core.clock(), actor))

    def stored(self, name: str) -> dict[str, Any] | None:
        row = self.core.store.one("SELECT set_at, set_by FROM block_secrets WHERE block = ? AND name = ?", (self.block, name))
        return dict(row) if row else None

    def open(self, name: str, actor: str) -> dict[str, Any]:
        """A fresh one-time link for this secret; it replaces any link still open for the same secret."""
        if not self.core.cfg.owner_sign_in:
            raise ClaimError("token links need owner sign-in configured; set the token in the environment instead")
        code, now = secrets.token_urlsafe(24), self.core.clock()

        def tx(db: Any) -> None:
            db.execute("UPDATE block_claims SET used_at = ? WHERE block = ? AND name = ? AND used_at IS NULL", (now, self.block, name))
            db.execute("INSERT INTO block_claims(code_hash, block, name, created_by, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                       (_sha(code), self.block, name, actor, now, now + CLAIM_TTL_S))

        self.core.store.tx(tx)
        base = (self.core.cfg.public_url or "").rstrip("/")
        return {"link": f"{base}/claims/{self.block}/{code}", "expires_in_s": CLAIM_TTL_S, "secret": name,
                "next": "Give the owner this link. They sign in, paste the token and save; it never goes through chat or MCP."}

    def pending(self, name: str) -> bool:
        return self.core.store.one("SELECT 1 FROM block_claims WHERE block = ? AND name = ? AND used_at IS NULL AND expires_at > ?",
                                   (self.block, name, self.core.clock())) is not None

    def check(self, code: str) -> dict[str, Any]:
        row = self.core.store.one("SELECT * FROM block_claims WHERE code_hash = ? AND block = ?", (_sha(code or ""), self.block))
        if row is None:
            raise ClaimError("this link isn't valid; ask for a new one")
        if row["used_at"] is not None:
            raise ClaimError("this link was already used or replaced by a newer one")
        if row["expires_at"] <= self.core.clock():
            raise ClaimError("this link has expired; ask for a new one")
        return dict(row)

    def complete(self, code: str, value: str, actor: str, valid: Callable[[str], bool]) -> str:
        row = self.check(code)
        value = (value or "").strip()
        if not valid(value):
            raise ClaimError("that doesn't look like the right kind of token")
        if not self.core.store.n("UPDATE block_claims SET used_at = ? WHERE code_hash = ? AND used_at IS NULL", (self.core.clock(), _sha(code))):
            raise ClaimError("this link was already used")
        self.put(row["name"], value, actor)
        return row["name"]

    def routes(self, title: str, hint: Callable[[str], str], valid: Callable[[str], bool], saved: Callable[[str], Any]) -> list[Route]:
        from .. import pages
        from ..sessions import login_redirect

        async def claim(request: Request) -> Response:
            if request.method in ("GET", "HEAD"):
                owner = await self.core.auth.owner(request)
                if owner is None:
                    return login_redirect(request)
            else:
                owner, refused = await self.core.auth.protect_owner(request)
                if refused is not None:
                    return refused
            code = request.path_params["code"]
            try:
                row = self.check(code)
            except ClaimError as e:
                return pages.claim_message_page("Link not usable", str(e), 410)
            if request.method in ("GET", "HEAD"):
                return pages.claim_page(title, f'{hint(row["name"])} Stored encrypted, never shown again.',
                                        self.core.auth.csrf(owner.csrf_key), requester=title.removesuffix(" bot token"),
                                        host=request.url.hostname or "", expires_at=row["expires_at"], now=self.core.clock())
            form_data = await request.form()
            try:
                name = self.complete(code, str(form_data.get("token") or ""), f"admin:{owner.label}", valid)
            except ClaimError as e:
                return pages.claim_message_page("Not saved", str(e), 400)
            self.core.audit.record(origin="http", source=f"admin:{owner.label}", client="claim", destination=self.block, action="secret_claim", outcome="ok", target=name)
            await saved(name)
            return pages.claim_message_page("Token saved", f"{self.block} has its {name} now and is reconnecting. You can close this tab.", 200)

        return [Route(f"/claims/{self.block}/{{code}}", claim, methods=["GET", "POST"])]
