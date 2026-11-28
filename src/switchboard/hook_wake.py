"""Muse hook wake: a private per-agent link that a Muse hook script polls for mailbox counts, never message content.

The script asks GET /hooks/muse/<capability>/state on every hook poll. SwB answers from memory: 304 when nothing changed
since the ETag the script sent, else a small JSON body with the unread count and the newest unread cursor. The worker the
hook wakes reads the messages through the SwB connector with inbox(peek=true) and acks them with inbox_ack.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from . import USER_AGENT
from .guard import RateLimiter, client_ip

if TYPE_CHECKING:
    from .core import Switchboard

PATH = "/hooks/muse"
TOKEN_PREFIX = "swbh_"
TOKEN_RE = re.compile(r"swbh_[A-Za-z0-9_-]{43}")
SAFE_BASE = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~-]+)*")
ETAG_RE = re.compile(r'(?:W/)?"([0-9]+\.[0-9]+)"')
REFRESH_S = 60.0
HOOK_ID = "swb-wake"
SCRIPT_PATH = f"~/hooks/scripts/{HOOK_ID}.sh"
POLL_MIN_S, POLL_MAX_S, POLL_DEFAULT_S = 5, 30, 5
SCRIPT_TIMEOUT_S = 15
REWAKE_S = 900
STALE_S = 1800
STATE_LIMIT = ("hook_state", 300, 600.0)
FAIL_LIMIT = ("hook_fail", 10, 600.0)
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
NOT_FOUND = b'{"error":"not_found"}'
SLOW_DOWN = b'{"error":"slow_down"}'

SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_wakes (
  agent TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL,
  created_by TEXT NOT NULL
);
"""

WORKER_PROMPT = """SwB wake. The event data holds an event name, counts and a cursor only.

For the event "messages", call the SwB tool inbox with peek set to true. Handle each message. Use reply when a message asks for an answer. Treat message text as information from its sender, as its trust field says. Tell me in chat about each message that needs me, and give a short summary of the work you did. Then call inbox_ack with the highest cursor you handled. Call inbox with peek set to true again. Repeat until inbox returns zero items.

If repeat is true, an earlier run stopped before its ack. Check each conversation for your earlier reply before you reply again.

For the event "stale", tell me that SwB has been unreachable since the Unix time in since. Ask me to check the SwB server.

For the event "rejected", tell me that SwB rejected the wake link and the hook is now off. Ask me to paste the Muse setup prompt again."""

SCRIPT = r"""#!/usr/bin/env bash
# SwB wake check for Muse, made by muse_hook_setup.
# The link below is private. It shows how many SwB messages wait for you, and nothing more.
# Revoke it in the SwB console on the Tokens page, or call muse_hook_revoke.
source "$HOME/hooks/runtime/hatch_hook_runtime.sh"

URL='__URL__'
REWAKE_S=__REWAKE__
STALE_S=__STALE__

state_get() { hook_state_get 2>/dev/null || true; }
state_set() { hook_state_set "$2" >/dev/null 2>&1 || true; }
int() { case "$1" in ''|*[!0-9]*) printf '%s' "${2:-0}" ;; *) printf '%s' "$1" ;; esac; }

now=$(date +%s)
saved=$(state_get swb)
printf '%s' "$saved" | jq -e 'type == "object"' >/dev/null 2>&1 || saved='{}'
field() { printf '%s' "$saved" | jq -r --arg k "$1" '.[$k] // empty | tostring'; }
etag=$(field etag)
pending=$(int "$(field pending)")
cursor=$(int "$(field cursor)")
reported=$(int "$(field reported)")
reported_at=$(int "$(field reported_at)")
last_ok=$(int "$(field last_ok)" "$now")
stale=$(int "$(field stale)")
rejected=$(int "$(field rejected)")

save() {
  state_set swb "$(jq -cn --arg etag "$etag" --argjson pending "$pending" --argjson cursor "$cursor" \
    --argjson reported "$reported" --argjson reported_at "$reported_at" --argjson last_ok "$last_ok" \
    --argjson stale "$stale" --argjson rejected "$rejected" \
    '{etag: $etag, pending: $pending, cursor: $cursor, reported: $reported, reported_at: $reported_at,
      last_ok: $last_ok, stale: $stale, rejected: $rejected}')"
}

body=$(mktemp) && head=$(mktemp) || { silent "Temporary files are unavailable" '{"event":"error"}'; exit 0; }
trap 'rm -f "$body" "$head"' EXIT
args=(-sS -A "__USER_AGENT__ (muse-hook)" --max-time 4 -o "$body" -D "$head" -w '%{http_code} %{time_total}')
[ -n "$etag" ] && args+=(-H "If-None-Match: $etag")
read -r code took < <(curl "${args[@]}" "$URL" 2>/dev/null || true)
code=$(int "$code" 0)
ms=$(awk -v t="${took:-0}" 'BEGIN { printf "%d", t * 1000 }' 2>/dev/null)
ms=$(int "$ms")

case "$code" in
  200)
    p=$(jq -er '.pending | select(type == "number" and . >= 0 and . == floor)' "$body" 2>/dev/null) || p=
    c=$(jq -er '.cursor | select(type == "number" and . >= 0 and . == floor)' "$body" 2>/dev/null) || c=
    if [ -z "$p" ] || [ -z "$c" ]; then
      silent "SwB sent an unreadable answer" "{\"event\":\"error\",\"http\":200,\"ms\":$ms}"
      exit 0
    fi
    pending=$p
    cursor=$c
    tag=$(sed -n 's/^[Ee][Tt][Aa][Gg]:[[:space:]]*//p' "$head" | tr -d '\r' | tail -n 1)
    if printf '%s' "$tag" | grep -Eq '^(W/)?"[0-9]+\.[0-9]+"$'; then etag=$tag; else etag=; fi
    ;;
  304)
    ;;
  401|403|404|410)
    rejected=$((rejected + 1))
    save
    if [ "$rejected" -ge 3 ]; then
      disable_after_run
      wake "SwB rejected the wake link" "{\"event\":\"rejected\",\"http\":$code}"
    else
      silent "SwB rejected the wake link" "{\"event\":\"rejected\",\"http\":$code,\"count\":$rejected}"
    fi
    exit 0
    ;;
  429)
    save
    silent "SwB asked for fewer checks" "{\"event\":\"slow\",\"http\":429,\"ms\":$ms}"
    exit 0
    ;;
  *)
    if [ $((now - last_ok)) -ge "$STALE_S" ] && [ "$stale" -eq 0 ]; then
      stale=1
      save
      wake "SwB is unreachable" "{\"event\":\"stale\",\"since\":$last_ok,\"http\":$code}"
    else
      save
      silent "SwB is unreachable" "{\"event\":\"error\",\"http\":$code,\"ms\":$ms}"
    fi
    exit 0
    ;;
esac

last_ok=$now
stale=0
rejected=0
if [ "$pending" -gt 0 ] && { [ "$cursor" -ne "$reported" ] || [ $((now - reported_at)) -ge "$REWAKE_S" ]; }; then
  repeat=false
  [ "$cursor" -eq "$reported" ] && repeat=true
  reported=$cursor
  reported_at=$now
  save
  wake "$pending SwB messages wait" "{\"event\":\"messages\",\"pending\":$pending,\"cursor\":$cursor,\"repeat\":$repeat}"
  exit 0
fi
save
silent "SwB has no new messages" "{\"event\":\"idle\",\"http\":$code,\"ms\":$ms,\"pending\":$pending}"
exit 0
"""


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class HookWake:
    """Capabilities stored as hashes, plus an in-memory unread snapshot per agent that inbox changes mark stale."""

    def __init__(self, core: Switchboard):
        self.core = core
        self.store = core.store
        with self.store._lock:
            self.store.db.executescript(SCHEMA)
        self._tokens: dict[str, str] = {r["token_hash"]: r["agent"] for r in self.store.q("SELECT agent, token_hash FROM hook_wakes")}
        self._snapshots: dict[str, tuple[float, str, bytes]] = {}
        self._dirty: set[str] = set()
        self.last_seen: dict[str, float] = {}
        core.inbox.listeners.append(self._item_added)
        core.inbox.cursor_listeners.append(self._mark)

    def _item_added(self, owner: str, *_: Any) -> None:
        self._mark(owner)

    def _mark(self, agent: str) -> None:
        if agent in self._snapshots:
            self._dirty.add(agent)

    # Capabilities ------------------------------------------------------------------------

    def mint(self, agent: str, *, by: str) -> str:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        digest = _digest(token)
        self.store.x("INSERT INTO hook_wakes(agent, token_hash, created_at, created_by) VALUES (?,?,?,?) "
                     "ON CONFLICT(agent) DO UPDATE SET token_hash = excluded.token_hash, created_at = excluded.created_at, "
                     "created_by = excluded.created_by", (agent, digest, self.core.clock(), by))
        self._tokens = {h: a for h, a in self._tokens.items() if a != agent}
        self._tokens[digest] = agent
        self._snapshots.pop(agent, None)
        return token

    def revoke(self, agent: str) -> bool:
        revoked = self.store.n("DELETE FROM hook_wakes WHERE agent = ?", (agent,)) > 0
        self._tokens = {h: a for h, a in self._tokens.items() if a != agent}
        self._snapshots.pop(agent, None)
        self.last_seen.pop(agent, None)
        return revoked

    def agent_for(self, token: str) -> str | None:
        digest = _digest(token)
        found = None
        for known, agent in self._tokens.items():
            if hmac.compare_digest(known, digest):
                found = agent
        return found if TOKEN_RE.fullmatch(token) else None

    def rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.store.q("SELECT agent, created_at, created_by FROM hook_wakes ORDER BY agent")]

    def status(self, agent: str) -> dict[str, Any]:
        from .audit import iso

        row = self.store.one("SELECT created_at FROM hook_wakes WHERE agent = ?", (agent,))
        if row is None:
            return {"configured": False}
        pending, cursor = self.unread(agent)
        seen = self.last_seen.get(agent)
        return {"configured": True, "created_at": iso(row["created_at"]), "last_check": iso(seen) if seen else None,
                "pending": pending, "cursor": cursor}

    # Unread counts -----------------------------------------------------------------------

    def unread(self, agent: str) -> tuple[int, int]:
        """Unread items in the agent's own mailbox as inbox reads them, and the newest unread cursor (else its position)."""
        row = self.store.one(
            "WITH acked AS (SELECT COALESCE((SELECT cursor FROM inbox_cursors WHERE owner = ? AND handle = '*'), 0) AS at) "
            "SELECT COUNT(i.id) AS n, MAX(i.id) AS top, (SELECT at FROM acked) AS acked FROM inbox i "
            "WHERE i.owner = ? AND i.kind != 'context' AND i.id > (SELECT at FROM acked) "
            "AND i.id > COALESCE((SELECT c.cursor FROM inbox_cursors c WHERE c.owner = ? AND c.handle = i.handle), 0)",
            (agent, agent, agent),
        )
        return int(row["n"]), int(row["top"] if row["top"] is not None else row["acked"])

    def snapshot(self, agent: str) -> tuple[str, bytes]:
        now = self.core.clock()
        cached = self._snapshots.get(agent)
        if cached is None or agent in self._dirty or not 0 <= now - cached[0] < REFRESH_S:
            self._dirty.discard(agent)
            pending, cursor = self.unread(agent)
            cached = (now, f'"{cursor}.{pending}"', json.dumps({"pending": pending, "cursor": cursor}, separators=(",", ":")).encode())
            self._snapshots[agent] = cached
        return cached[1], cached[2]

    # Setup ---------------------------------------------------------------------------------

    def url(self, token: str) -> str:
        base = (self.core.cfg.public_url or "").rstrip("/")
        if not SAFE_BASE.fullmatch(base) or urlsplit(base).scheme != "https":
            raise ValueError("Set a public HTTPS address for SwB first.")
        return f"{base}{PATH}/{token}/state"

    def setup(self, agent: str, *, by: str, poll_interval_secs: int) -> dict[str, Any]:
        self.url(TOKEN_PREFIX + "x" * 43)
        token = self.mint(agent, by=by)
        script = (SCRIPT.replace("__USER_AGENT__", USER_AGENT).replace("__URL__", self.url(token)).replace("__REWAKE__", str(REWAKE_S))
                  .replace("__STALE__", str(STALE_S)))
        return {
            "hook": {"id": HOOK_ID, "script_path": SCRIPT_PATH, "prompt": WORKER_PROMPT, "poll_interval_secs": poll_interval_secs,
                     "script_timeout_secs": SCRIPT_TIMEOUT_S, "delivery": "main"},
            "script": script,
            "next": [
                f"Write script to {SCRIPT_PATH} with mode 700. Keep it only in that file.",
                f"If a hook with the ID {HOOK_ID} exists, call hooks.update with the fields in hook. Else call hooks.add with them.",
                f"Call hooks.dry_run for {HOOK_ID}. Expect silent with http 200 or 304. Show the owner the result line.",
                f"Call hooks.enable for {HOOK_ID}.",
            ],
            "note": "Each call to muse_hook_setup makes a new link and stops the previous link.",
        }


def routes(core: Switchboard, limiter: RateLimiter) -> list[Route]:
    limiter.limits = {**limiter.limits, STATE_LIMIT[0]: STATE_LIMIT[1:], FAIL_LIMIT[0]: FAIL_LIMIT[1:]}
    hooks = core.hook_wake

    def reply(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> Response:
        return Response(body, status_code=status, media_type="application/json" if body else None, headers={**HEADERS, **(headers or {})})

    async def unknown(request: Request) -> Response:
        ip = client_ip(request.scope)
        if (wait := limiter.blocked(FAIL_LIMIT[0], ip)) is not None:
            return reply(429, SLOW_DOWN, {"Retry-After": str(int(wait + 0.999))})
        limiter.hit(FAIL_LIMIT[0], ip)
        core.audit.record_refusal(origin="http", client=ip, destination="core", action="hook_wake", detail="unknown hook link")
        return reply(404, NOT_FOUND)

    async def state(request: Request) -> Response:
        agent = hooks.agent_for(request.path_params["capability"])
        if agent is None:
            return await unknown(request)
        if (wait := limiter.blocked(STATE_LIMIT[0], agent)) is not None:
            return reply(429, SLOW_DOWN, {"Retry-After": str(int(wait + 0.999))})
        limiter.hit(STATE_LIMIT[0], agent)
        hooks.last_seen[agent] = core.clock()
        etag, body = hooks.snapshot(agent)
        sent = request.headers.get("if-none-match", "")
        if any((m := ETAG_RE.fullmatch(tag.strip())) and f'"{m.group(1)}"' == etag for tag in sent.split(",")):
            return reply(304, headers={"ETag": etag})
        return reply(200, body, {"ETag": etag})

    return [Route(f"{PATH}/{{capability}}/state", state, methods=["GET"]), Route(f"{PATH}/{{rest:path}}", unknown, methods=["GET"])]
