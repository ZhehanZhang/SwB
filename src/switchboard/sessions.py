from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .store import Store

SESSION_COOKIE = "__Host-swb_session"
SESSION_LIFETIME = 30 * 86400
SESSION_IDLE = 7 * 86400
LINK_TTL = 300
LINK_HISTORY_TTL = 86400
CHALLENGE_TTL = 120
CHALLENGE_COOKIE = "__Host-swb_wa"


@dataclass(frozen=True)
class Owner:
    label: str
    via: str
    session_id: str | None = None

    @property
    def csrf_key(self) -> str:
        return self.session_id or self.label


def set_cookie(response: Response, name: str, value: str, ttl: int) -> None:
    response.set_cookie(name, value, max_age=ttl, httponly=True, secure=True, samesite="lax", path="/")


def clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, httponly=True, secure=True, samesite="lax", path="/")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def safe_next(value: str | None) -> str:
    if (not value or not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        return "/"
    return value


def login_redirect(request: Request) -> RedirectResponse:
    path = (request.scope.get("raw_path") or request.url.path.encode()).decode("latin-1")
    query = request.url.query
    return RedirectResponse("/login?" + urlencode({"next": safe_next(path + ("?" + query if query else ""))}),
                            status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


class Sessions:
    def __init__(self, store: Store):
        self.store = store

    def owner(self, cookie: str | None) -> Owner | None:
        if not cookie:
            return None
        key, now = _hash(cookie), self.store.clock()
        row = self.store.one("SELECT via, created_at, last_seen FROM owner_sessions WHERE id = ?", (key,))
        if row is None or now - row["created_at"] >= SESSION_LIFETIME or now - row["last_seen"] >= SESSION_IDLE:
            return None
        if now - row["last_seen"] >= 60:
            self.store.n("UPDATE owner_sessions SET last_seen = ? WHERE id = ? AND last_seen <= ?", (now, key, now - 60))
        return Owner(f"session:{key[:12]}", row["via"], key)

    def start(self, request: Request, response: Response, via: str, client_id: str | None = None,
              source: str | None = None) -> Response:
        previous = request.cookies.get(SESSION_COOKIE)
        cookie, now = secrets.token_urlsafe(32), self.store.clock()

        def rotate(db):
            if via == "setup" and db.execute("SELECT 1 FROM owner_initialized").fetchone() is not None:
                return False
            db.execute("INSERT OR IGNORE INTO owner_initialized(id) VALUES (1)")
            if previous:
                db.execute("DELETE FROM owner_sessions WHERE id = ?", (_hash(previous),))
            db.execute("DELETE FROM owner_sessions WHERE created_at <= ? OR last_seen <= ?",
                       (now - SESSION_LIFETIME, now - SESSION_IDLE))
            db.execute("INSERT INTO owner_sessions(id, via, client_id, source, created_at, last_seen) VALUES (?,?,?,?,?,?)",
                       (_hash(cookie), via, client_id, source, now, now))
            return True

        if not self.store.tx(rotate):
            return Response(status_code=409)
        set_cookie(response, SESSION_COOKIE, cookie, SESSION_LIFETIME)
        return response

    def logout(self, request: Request, response: Response) -> Response:
        if cookie := request.cookies.get(SESSION_COOKIE):
            self.store.n("DELETE FROM owner_sessions WHERE id = ?", (_hash(cookie),))
        clear_cookie(response, SESSION_COOKIE)
        return response

    def mint_link(self, client_id: str, source: str) -> str:
        token, now = secrets.token_urlsafe(32), self.store.clock()

        def mint(db):
            db.execute("DELETE FROM owner_login_links WHERE expires_at <= ?", (now - LINK_HISTORY_TTL,))
            db.execute("DELETE FROM owner_login_links_used WHERE expires_at <= ?", (now - LINK_HISTORY_TTL,))
            db.execute("INSERT INTO owner_login_links(token_hash, client_id, source, expires_at) VALUES (?,?,?,?)",
                       (_hash(token), client_id, source, now + LINK_TTL))

        self.store.tx(mint)
        return token

    def consume_link(self, token: str):
        key = _hash(token)

        def consume(db):
            now = self.store.clock()
            row = db.execute(
                "DELETE FROM owner_login_links WHERE token_hash = ? AND expires_at > ? RETURNING client_id, source, expires_at",
                (key, now),
            ).fetchone()
            if row is not None:
                db.execute("INSERT INTO owner_login_links_used(token_hash, expires_at) VALUES (?,?)", (key, row["expires_at"]))
                return row, None
            used = db.execute("SELECT expires_at FROM owner_login_links_used WHERE token_hash = ?", (key,)).fetchone()
            if used is not None:
                return None, "link_used" if used["expires_at"] > now else "link_expired"
            if db.execute("SELECT 1 FROM owner_login_links WHERE token_hash = ?", (key,)).fetchone() is not None:
                return None, "link_expired"
            return None, "link_invalid"

        return self.store.tx(consume)

    def challenge(self, kind: str, owner: Owner | None) -> tuple[str, bytes]:
        cookie, challenge, now = secrets.token_urlsafe(32), secrets.token_bytes(32), self.store.clock()

        def issue(db):
            db.execute("DELETE FROM owner_webauthn_challenges WHERE expires_at <= ?", (now,))
            db.execute("INSERT INTO owner_webauthn_challenges(id, challenge, kind, owner_key, expires_at) VALUES (?,?,?,?,?)",
                       (_hash(cookie), challenge, kind, owner.csrf_key if owner else None, now + CHALLENGE_TTL))

        self.store.tx(issue)
        return cookie, challenge

    def consume_challenge(self, request: Request, kind: str, owner: Owner | None) -> bytes | None:
        if not (cookie := request.cookies.get(CHALLENGE_COOKIE)):
            return None
        row = self.store.tx(lambda db: db.execute(
            "DELETE FROM owner_webauthn_challenges WHERE id = ? RETURNING challenge, kind, owner_key, expires_at",
            (_hash(cookie),),
        ).fetchone())
        if (row is not None and row["kind"] == kind and row["expires_at"] > self.store.clock()
                and hmac.compare_digest((row["owner_key"] or "").encode(), (owner.csrf_key if owner else "").encode())):
            return row["challenge"]
        return None

    def user_handle(self) -> bytes:
        query = "SELECT handle FROM owner_webauthn_user WHERE id = 1"
        row = self.store.one(query)
        if row is None:
            self.store.x("INSERT OR IGNORE INTO owner_webauthn_user(id, handle) VALUES (1, ?)", (secrets.token_bytes(32),))
            row = self.store.one(query)
        return row["handle"]
