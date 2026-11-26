"""Claude routines: a send to a routine target starts a Claude Code session on claude.ai that answers through switchboard.

Each routine is a Claude Code scheduled task with an API trigger. A general routine (kind general) answers for Claude at
large; a project routine (kind project) is a task created inside a Claude Project, so it runs with that project's repos,
files, instructions and memory, and can hand work to the project's coordinator session. Routines come from the config
(token in the environment) or are added by the owner on the root page or with routine_add (token encrypted at rest in
switchboard's store, never shown again).

A send to claude with project X fires X's project routine, else the general one, else it stays in claude's mailbox.
claude:<project> and claude-<project> name a project routine directly. Every send lands in the agent's mailbox first, so
the conversation exists whatever happens next; the fire carries a note naming the conversation handle. Replies come
back with in_reply_to set to it, interim ones with final=false and the answer with final=true.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import logging
import os
import re
import secrets
from typing import TYPE_CHECKING, Any

import httpx

from .. import Action, Block, BlockError, Call, Manifest, TargetKind
from ...config import SLUG_RE
from ...projects import ProjectError

if TYPE_CHECKING:
    from ...core import Switchboard

log = logging.getLogger("switchboard.blocks.claude_routine")

FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire"
API_VERSION = "2023-06-01"
ROUTINE_RE = re.compile(r"trig_[A-Za-z0-9]+")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")
MAX_TEXT = 60_000
KINDS = ("general", "project")
CLAIM_TTL_S = 1800
CLAIMS_PER_CALLER = 10
SIMILAR = 0.6
PAUSED = "this project is paused"

SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (name TEXT PRIMARY KEY, routine_id TEXT NOT NULL, agent TEXT NOT NULL, project TEXT, kind TEXT NOT NULL,
  token TEXT NOT NULL, created_at REAL NOT NULL, created_by TEXT NOT NULL)
"""
FIRES = "CREATE TABLE IF NOT EXISTS routine_fires (name TEXT PRIMARY KEY, fired_at REAL NOT NULL, last_status TEXT NOT NULL)"
PICKUPS = """
CREATE TABLE IF NOT EXISTS routine_pickups (handle TEXT PRIMARY KEY, routine TEXT NOT NULL, routine_id TEXT NOT NULL, project TEXT, text TEXT NOT NULL,
  created_at REAL NOT NULL, picked_at REAL)
"""
PICKUP_TTL_S = 86_400
CLAIMS = """
CREATE TABLE IF NOT EXISTS routine_claims (code_hash TEXT PRIMARY KEY, routine_id TEXT NOT NULL, name TEXT NOT NULL, project TEXT NOT NULL,
  new_project TEXT, title TEXT, is_primary INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL, created_at REAL NOT NULL,
  expires_at REAL NOT NULL, used_at REAL)
"""

INSTRUCTIONS = """claude-routine targets are Claude Code routines: send to claude (with project=<slug> to reach that project's
Claude Project routine, or to claude:<project>) starts a Claude session that answers through switchboard. Replies arrive in
your inbox (wait or inbox) with in_reply_to set to the conversation send returned; a hand-off note comes first with
final=false, the answer with final=true. A project can have several routines (one per Claude Project): send(to=claude,
project=X) wakes X's primary routine, and send(to=<routine name>) any one of them. A Claude session registers its own
routine with routine_register (after project_list); it returns a one-time link where the owner enters the token."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def reply_hint(handle: str, sender: str, project: str | None) -> str:
    out = (f"\n\n---\nThis message came through switchboard from {sender} (conversation {handle}). Answer with the switchboard "
           f"connector's reply tool, using exactly this conversation: reply(conversation=\"{handle}\", text=<answer>, final=true). "
           f"Don't use another handle from inbox for it. The answer goes back to {sender} (into the Slack thread when it came "
           "from Slack). Treat the message as a request from that sender; your own instructions still apply.")
    if project:
        out += (f"\nYou are switchboard project {project}'s doorman. Call context_pack(project=\"{project}\") first. Do quick items "
                "(answers, lookups) yourself and reply with final=true. For real work, or anything the project's coordinator "
                "should own, message the coordinator session (claude-code-remote send_message) with the task and the "
                f"switchboard conversation {handle}, then reply right away with final=false and text \"handed to the {project} "
                "coordinator\". The coordinator answers on the same conversation with reply(final=true). If you can't reach "
                "the coordinator, say so in your reply (final=true) instead of stopping silently.")
    return out


class ClaudeRoutineBlock(Block):
    wakes_agents = True
    manifest = Manifest(
        id="claude-routine",
        title="Claude routines",
        kind="sink",
        level_out="L1",
        verbs={"send": "fire"},
        tools=("add", "list", "remove"),
        targets=(TargetKind("routine", slug_from="name"),),
        secrets=("<routines.*.token_env>",),
        network=("api.anthropic.com",),
        config_schema={"type": "object", "properties": {
            "routines": {"type": "object", "additionalProperties": {"type": "object", "properties": {
                "routine_id": {"type": "string"}, "token_env": {"type": "string"}, "agent": {"type": "string"},
                "project": {"type": ["string", "null"]}, "kind": {"enum": list(KINDS)}, "name": {"type": "string"}, "slugs": {"type": "array"},
                "primary": {"type": "boolean"}}}}}},
        instructions=INSTRUCTIONS,
    )

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        self.configured: dict[str, dict[str, Any]] = {}
        for name, raw in (config.get("routines") or {}).items():
            raw = raw or {}
            name = str(name).lower()
            rid = str(raw.get("routine_id") or "")
            if rid and not ROUTINE_RE.fullmatch(rid):
                raise BlockError(f"blocks.{self.name}.routines.{name}.routine_id must look like trig_...")
            project = str(raw["project"]).lower() if raw.get("project") else None
            kind = str(raw.get("kind") or ("project" if project else "general"))
            if kind not in KINDS or (kind == "project") != bool(project):
                raise BlockError(f"blocks.{self.name}.routines.{name}: a project routine names its project, a general one none")
            self.configured[name] = {"name": name, "routine_id": rid, "agent": str(raw.get("agent") or "claude").lower(), "project": project,
                                     "kind": kind, "token_env": str(raw.get("token_env") or f"CLAUDE_ROUTINE_TOKEN_{name.upper().replace('-', '_')}"),
                                     "title": raw.get("name"), "slugs": [str(s).lower() for s in raw.get("slugs") or []], "source": "config",
                                     "primary": bool(raw.get("primary"))}
        self.http = httpx.AsyncClient(transport=config.get("transport"), timeout=30)
        core.store.x(SCHEMA)
        core.store.x(FIRES)
        core.store.x(CLAIMS)
        core.store.x(PICKUPS)
        cols = {r["name"] for r in core.store.q("PRAGMA table_info(routines)")}
        if "title" not in cols:
            core.store.x("ALTER TABLE routines ADD COLUMN title TEXT")
        if "is_primary" not in cols:
            core.store.x("ALTER TABLE routines ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0")

    # Registry --------------------------------------------------------------------------------

    def _key(self) -> bytes:
        path = self.core.data_dir / "routine-key"
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
            path.chmod(0o600)
        return path.read_bytes()

    def _seal(self, token: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        return base64.b64encode(nonce + AESGCM(self._key()).encrypt(nonce, token.encode(), b"routine")).decode()

    def _open(self, sealed: str) -> str | None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            raw = base64.b64decode(sealed)
            return AESGCM(self._key()).decrypt(raw[:12], raw[12:], b"routine").decode()
        except Exception:
            return None

    def routines(self) -> dict[str, dict[str, Any]]:
        out = dict(self.configured)
        for row in self.core.store.q("SELECT * FROM routines ORDER BY created_at, name"):
            if row["name"] not in out:
                out[row["name"]] = {**{k: row[k] for k in ("name", "routine_id", "agent", "project", "kind", "title")},
                                    "sealed": row["token"], "slugs": [], "source": "stored", "created_by": row["created_by"],
                                    "primary": bool(row["is_primary"])}
        return out

    def primary(self, project: str | None, agent: str, routines: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """The routine a send to agent for project wakes: the one marked primary (a stored mark beats the config's), else
        the first registered. general routines (project None) follow the same rule."""
        mine = [r for r in (routines or self.routines()).values() if r["project"] == project and r["agent"] == agent]
        marked = sorted((r for r in mine if r["primary"]), key=lambda r: r["source"] != "stored")
        return (marked or mine or [None])[0]

    def token(self, r: dict[str, Any]) -> str | None:
        if r["source"] == "config":
            return self.core.cfg.env.get(r["token_env"]) or None
        return self._open(r["sealed"])

    def slugs(self, r: dict[str, Any], routines: dict[str, dict[str, Any]] | None = None) -> list[str]:
        """The routine's name, its own slugs, and <agent>-<project> for the project's primary routine only (unless another
        routine is named that)."""
        routines = routines or self.routines()
        alias = f"{r['agent']}-{r['project']}"
        lead = r["project"] and (self.primary(r["project"], r["agent"], routines) or {}).get("name") == r["name"] and alias not in routines
        return list(dict.fromkeys([r["name"]] + ([alias] if lead else []) + r["slugs"]))

    def status_of(self, r: dict[str, Any]) -> str:
        if not r["routine_id"] or not self.token(r):
            return "no token" if r["routine_id"] else "no routine id"
        row = self.core.store.one("SELECT last_status FROM routine_fires WHERE name = ?", (r["name"],))
        return {"ok": "tested"}.get(row["last_status"], row["last_status"]) if row else "untested"

    def _record_fire(self, name: str, status: str) -> None:
        self.core.store.x("INSERT INTO routine_fires(name, fired_at, last_status) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                          "fired_at = excluded.fired_at, last_status = excluded.last_status", (name, self.core.clock(), status))

    def rows(self) -> list[dict[str, Any]]:
        routines = self.routines()
        out = []
        for n, r in routines.items():
            lead = self.primary(r["project"], r["agent"], routines)
            out.append({"name": n, "routine_id": r["routine_id"], "agent": r["agent"], "project": r["project"], "kind": r["kind"], "source": r["source"],
                        "claude_project": r["title"], "primary": lead is not None and lead["name"] == n, "slugs": self.slugs(r, routines),
                        "status": self.status_of(r), "visibility": self.core.visibility.rule(f"target:{n}")})
        return out

    def _free_name(self, agent: str, slug: str | None, title: str | None, routine_id: str) -> str:
        """claude-<project> when free, else claude-<project>-<Claude Project name>, else a number: never another routine's name."""
        routines = self.routines()
        same = next((n for n, r in routines.items() if r["routine_id"] == routine_id and r["source"] == "stored"), None)
        if same:
            return same
        taken = set(routines) | set(self._claimed(routine_id))
        base = f"{agent}-{slug}" if slug else agent
        words = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")

        def fit(suffix: str) -> str:
            return (base[:40 - len(suffix)].rstrip("-") + suffix) if suffix else base[:40].rstrip("-")

        for n in [fit("")] + ([fit("-" + words[:20].rstrip("-"))] if words else []) + [fit(f"-{i}") for i in range(2, 100)]:
            if NAME_RE.fullmatch(n) and n not in taken:
                return n
        raise BlockError(f"no free routine name under {base}; pass name")

    def _claimed(self, routine_id: str, skip: str | None = None) -> dict[str, str]:
        """Names held by registrations still waiting for the owner, other than this routine's own (or the claim skip)."""
        return {r["name"]: r["routine_id"] for r in self.core.store.q(
            "SELECT name, routine_id, code_hash FROM routine_claims WHERE used_at IS NULL AND expires_at > ?", (self.core.clock(),))
            if r["routine_id"] != routine_id and r["code_hash"] != skip}

    def _check_name(self, name: str, routine_id: str, replace: bool, skip: str | None = None) -> None:
        if not NAME_RE.fullmatch(name):
            raise BlockError("a routine name is lowercase letters, digits and dashes")
        if name in self.configured:
            raise BlockError(f"{name} is set in the config file; change it there")
        held = self.routines().get(name)
        if held is not None and held["routine_id"] != routine_id and not replace:
            raise BlockError(f"routine {name} already exists for {held['routine_id']}; pick another name, or pass replace=true to point it at this routine")
        if name in self._claimed(routine_id, skip):
            raise BlockError(f"routine name {name} is held by a registration waiting for the owner; pick another name")

    def add(self, *, fire: str, token: str, project: str | None, name: str | None, agent: str, actor: str, title: str | None = None,
            primary: bool | None = None, replace: bool = False) -> dict[str, Any]:
        m = ROUTINE_RE.search(fire or "")
        if not m:
            raise BlockError("paste the routine's fire URL (…/routines/trig_…/fire) or its trig_ id")
        token = (token or "").strip()
        if not TOKEN_RE.fullmatch(token):
            raise BlockError("that doesn't look like a routine token (sk-ant-oat01-…)")
        p = self.core.project(project) if project else None
        if project and p is None:
            raise BlockError(f"no switchboard project {project!r}")
        slug = p.slug if p else None
        rid = m.group(0)
        if any(r["routine_id"] == rid for r in self.configured.values()):
            raise BlockError(f"{rid} is set in the config file; change it there")
        name = (name or "").strip().lower() or self._free_name(agent, slug, title, rid)
        self._check_name(name, rid, replace)
        held = self.core.store.one("SELECT title, is_primary FROM routines WHERE name = ?", (name,))
        mark = bool(primary) if primary is not None else bool(held and held["is_primary"])
        title = (title or "").strip()[:120] or (held["title"] if held else None)

        def run(db: Any) -> None:
            if mark:
                db.execute("UPDATE routines SET is_primary = 0 WHERE agent = ? AND project IS ?", (agent, slug))
            db.execute("INSERT INTO routines(name, routine_id, agent, project, kind, token, created_at, created_by, title, is_primary) VALUES (?,?,?,?,?,?,?,?,?,?) "
                       "ON CONFLICT(name) DO UPDATE SET routine_id = excluded.routine_id, agent = excluded.agent, project = excluded.project, "
                       "kind = excluded.kind, token = excluded.token, created_by = excluded.created_by, title = excluded.title, is_primary = excluded.is_primary",
                       (name, rid, agent, slug, "project" if slug else "general", self._seal(token), self.core.clock(), actor, title, int(mark)))

        self.core.store.tx(run)
        self.core.changed()
        return next(r for r in self.rows() if r["name"] == name)

    # Self-registration: a Claude session names its routine, the owner enters the token behind Access -------------

    def similar(self, *hints: str | None) -> list[dict[str, Any]]:
        """Projects whose slug, name or linked project name (claude_project, ...) is close to any hint."""
        def norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower())

        hs = [norm(h) for h in hints if h and norm(h)]
        out = []
        for p in self.core.projects.list():
            fields = [p.slug, p.name] + [str(v) for k, v in p.links.items() if k.endswith("_project") and isinstance(v, str)]
            fs = [norm(f) for f in fields if norm(f)]
            score = max((1.0 if h == f else 0.9 if min(len(h), len(f)) >= 3 and (h in f or f in h) else difflib.SequenceMatcher(None, h, f).ratio())
                        for h in hs for f in fs) if hs and fs else 0.0
            if score >= SIMILAR:
                out.append({"slug": p.slug, "name": p.name, "links": {k: v for k, v in p.links.items() if k != "repos"}, "score": round(score, 2)})
        return sorted(out, key=lambda x: -x["score"])

    def register(self, *, fire: str, claude_project: str, project: str | None, project_name: str | None, description: str | None,
                 create_new: bool, name: str | None, primary: bool | None, actor: str) -> dict[str, Any]:
        m = ROUTINE_RE.search(fire or "")
        if not m:
            raise BlockError("pass the routine's fire URL (…/routines/trig_…/fire) or its trig_ id")
        rid = m.group(0)
        title = (claude_project or "").strip()[:120]
        if not title:
            raise BlockError("pass claude_project: the name of the Claude Project this routine lives in")
        slug = (project or "").strip().lower().removeprefix("project:")
        existing = None
        if slug:
            try:
                existing = self.core.projects.get(slug, allow_archived=False)
            except ProjectError:
                existing = None
        else:
            linked = [p for p in self.core.projects.list() if str(p.links.get("claude_project", "")).strip().lower() == title.lower()]
            existing = linked[0] if len(linked) == 1 else None
            if existing is None:
                return {"pending": False, "reason": "pass project: an existing slug from project_list, or a new slug with create_new=true",
                        "candidates": self.similar(title)}
        new_project = None
        if existing is None:
            if not SLUG_RE.match(slug) or slug == "general":
                raise BlockError("project must be lowercase letters, digits and dashes (max 48), and not 'general'")
            near = self.similar(slug, project_name, title)
            if near and not create_new:
                return {"pending": False, "reason": f"no project {slug!r}, but these are close: attach to one (project=<its slug>), or pass "
                        "create_new=true if this really is a new project", "candidates": near}
            new_project = {"name": (project_name or title).strip()[:120], "description": (description or "").strip()[:2000],
                           "links": {"claude_project": title}}
        slug = existing.slug if existing else slug
        if any(r["routine_id"] == rid for r in self.configured.values()):
            raise BlockError(f"{rid} is set in the config file; change it there")
        name = (name or "").strip().lower() or self._free_name("claude", slug, title, rid)
        self._check_name(name, rid, False)
        now = self.core.clock()
        if (self.core.store.one("SELECT COUNT(*) AS n FROM routine_claims WHERE created_by = ? AND used_at IS NULL AND expires_at > ?", (actor, now))["n"]
                >= CLAIMS_PER_CALLER):
            raise BlockError(f"{CLAIMS_PER_CALLER} registrations are already waiting for the owner; let those expire or finish first")
        code = secrets.token_urlsafe(24)
        self.core.store.x("INSERT INTO routine_claims(code_hash, routine_id, name, project, new_project, title, is_primary, created_by, created_at, expires_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?)", (_sha(code), rid, name, slug, json.dumps(new_project) if new_project else None, title,
                                                           int(bool(primary)), actor, now, now + CLAIM_TTL_S))
        base = self.core.cfg.public_url or ""
        routines = self.routines()
        lead = self.primary(slug, "claude", routines)
        return {"pending": True, "link": f"{base}/routines/claim/{code}", "expires_in_s": CLAIM_TTL_S, "name": name, "routine_id": rid,
                "project": slug, "creates_project": new_project is not None, "claude_project": title,
                "primary": bool(primary) or lead is None or lead["name"] == name,
                "next": "Give the owner this link. He opens it (SSO), pastes the routine's token and clicks Save; the token never goes "
                        "through chat or MCP. routine_list shows the routine once saved (untested until its first fire)."}

    def claim(self, code: str) -> dict[str, Any]:
        row = self.core.store.one("SELECT * FROM routine_claims WHERE code_hash = ?", (_sha(code or ""),))
        if row is None:
            raise BlockError("this link isn't valid; ask the Claude session for a new one")
        if row["used_at"] is not None:
            raise BlockError("this link was already used")
        if row["expires_at"] <= self.core.clock():
            raise BlockError("this link has expired; ask the Claude session to call routine_register again")
        return {**{k: row[k] for k in ("routine_id", "name", "project", "title", "created_by")}, "primary": bool(row["is_primary"]),
                "new_project": json.loads(row["new_project"]) if row["new_project"] else None, "expires_at": row["expires_at"]}

    def complete(self, code: str, token: str, actor: str) -> dict[str, Any]:
        """The owner's half: the token for a pending registration. Creates the project if the registration asked for one."""
        c = self.claim(code)
        if not TOKEN_RE.fullmatch((token or "").strip()):
            raise BlockError("that doesn't look like a routine token (sk-ant-oat01-…)")
        self._check_name(c["name"], c["routine_id"], False, skip=_sha(code))
        if not self.core.store.n("UPDATE routine_claims SET used_at = ? WHERE code_hash = ? AND used_at IS NULL", (self.core.clock(), _sha(code))):
            raise BlockError("this link was already used")
        if c["new_project"]:
            try:
                self.core.projects.get(c["project"])
            except ProjectError:
                self.core.projects.create(slug=c["project"], **c["new_project"])
        return {**self.add(fire=c["routine_id"], token=token, project=c["project"], name=c["name"], agent="claude", actor=actor, title=c["title"],
                           primary=True if c["primary"] else None), "created_project": bool(c["new_project"])}

    def pending(self) -> list[dict[str, Any]]:
        return [{"name": r["name"], "routine_id": r["routine_id"], "project": r["project"], "claude_project": r["title"], "requested_by": r["created_by"],
                 "expires_in_s": int(r["expires_at"] - self.core.clock())}
                for r in self.core.store.q("SELECT * FROM routine_claims WHERE used_at IS NULL AND expires_at > ? ORDER BY created_at", (self.core.clock(),))]

    def remove(self, name: str) -> bool:
        name = (name or "").strip().lower()
        if name in self.configured:
            raise BlockError(f"{name} is set in the config file; remove it there")
        n = self.core.store.x("DELETE FROM routines WHERE name = ?", (name,))
        self.core.changed()
        return bool(n)

    # Block -----------------------------------------------------------------------------------

    def problems(self) -> list[str]:
        routines = self.routines()
        return [f"routine {r['name']}: {r['status']}" + (f" ({routines[r['name']]['token_env']} is not set; messages stay in the mailbox)"
                if r["status"] == "no token" and r["source"] == "config" else "") for r in self.rows() if r["status"] in ("no token", "no routine id")]

    def actions(self) -> list[Action]:
        return [
            Action("fire", "Message a Claude routine", "Leave the message in the routine's mailbox and start a session that replies.",
                   self.fire, capped=True, burst=True),
            Action("list", "Claude routines", "Registered routines: project, kind, status (untested until the first fire), visibility. Tokens are never shown.",
                   self.list_action, read_only=True),
            Action("register", "Register this Claude routine", "A Claude session registers its own routine for a switchboard project: "
                   "returns a one-time link where the owner enters the token. Read project_list first; a new project close to an existing one "
                   "is refused unless create_new is set.", self.register_action),
            Action("pickup", "Collect a routine's message", "For a routine session woken without text: the message it was woken for, "
                   "with the conversation handle and the switchboard note.", self.pickup_action),
            Action("add", "Add a Claude routine", "Owner only. Register a routine from its fire URL (or trig_ id) and token, for a switchboard project or "
                   "general. The token is encrypted at rest and never shown again. Prefer the root page's form, so the token stays out of chats.",
                   self.add_action),
            Action("remove", "Remove a Claude routine", "Owner only. Forget a routine added here (config ones are removed in the config file).",
                   self.remove_action, destructive=True),
        ]

    def _owner(self, call: Call, what: str) -> None:
        if call.principal.kind == "connect":
            raise BlockError(f"a connected agent can't {what}; the owner does that from the root page or his own sign-in")

    async def list_action(self, call: Call) -> list[dict[str, Any]]:
        return self.rows()

    async def add_action(self, call: Call, *, fire_url: str, token: str, project: str | None = None, name: str | None = None,
                         primary: bool | None = None, replace: bool = False) -> dict[str, Any]:
        self._owner(call, "add routines")
        out = self.add(fire=fire_url, token=token, project=project, name=name, agent="claude", actor=f"{call.principal.kind}:{call.principal.source}",
                       primary=primary, replace=replace)
        call.note(target=out["name"], excerpt=f"routine {out['routine_id']} for {out['project'] or 'general'}")
        return out

    async def register_action(self, call: Call, *, fire_url: str, claude_project: str, project: str | None = None, project_name: str | None = None,
                              description: str | None = None, create_new: bool = False, name: str | None = None, primary: bool | None = None) -> dict[str, Any]:
        out = self.register(fire=fire_url, claude_project=claude_project, project=project, project_name=project_name, description=description,
                            create_new=create_new, name=name, primary=primary, actor=f"{call.principal.kind}:{call.principal.source}")
        call.note(target=out.get("name") or "-", excerpt=f"routine register for {out.get('project') or project or '?'}: {'pending' if out['pending'] else 'candidates'}")
        return out

    async def remove_action(self, call: Call, *, name: str) -> dict[str, Any]:
        self._owner(call, "remove routines")
        call.note(target=name)
        return {"removed": self.remove(name)}

    async def targets(self) -> list[dict[str, Any]]:
        routines = self.routines()
        return [{"kind": "routine", "id": n, "name": (f"Claude · {r['title']}" if r["source"] == "stored" and r["title"] else r["title"])
                 or (f"Claude · {r['project']}" if r["project"] else f"{n} (Claude routine)"),
                 "slugs": self.slugs(r, routines), **({"project": r["project"]} if r["project"] else {})} for n, r in routines.items()]

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        return [(f"routine {r['name']}", f"{r['kind']}{' · ' + r['project'] if r['project'] else ''} · {r['routine_id'] or 'no id'}", r["status"],
                 r["status"] not in ("no token", "no routine id") and not r["status"].startswith("failed")) for r in self.rows()]

    async def validate_route_target(self, target: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        name = str(target.get("routine") or "").lower()
        if name not in self.routines():
            raise BlockError(f"no routine {name!r}; routines: {', '.join(self.routines()) or 'none'}")
        return {"routine": name}, {}

    def wake_target(self, agent: str, project: str) -> Any:
        from .. import Address

        p = (project or "").strip().lower().removeprefix("project:")
        own = self.primary(p, agent) if p else None
        return Address(self.name, "routine", own["name"]) if own else None

    def pick(self, target: str | None, project: str | None, agent: str | None = None) -> dict[str, Any] | None:
        """A named routine, upgraded to the project's primary routine when the send names a project and the named one is
        general; else that project's primary routine; else the general primary."""
        routines = self.routines()
        name = (target or "").partition(":")[2] or (target or "")
        named = routines.get(name.lower()) if name else None
        agent = (named or {}).get("agent") or agent or "claude"
        p = project.strip().lower().removeprefix("project:") if project else None
        if p and (named is None or named["kind"] == "general"):
            own = self.primary(p, agent, routines)
            if own is not None:
                return own
        if named is not None:
            return named
        return self.primary(None, agent, routines)

    async def fire(self, call: Call, *, task: str, target: str | None = None, project: str | None = None, title: str | None = None,
                   **_: Any) -> dict[str, Any]:
        if target is None:
            route, _tried = self.core.routes.resolve(destination=self.name, agent=call.principal.source, project=project)
            target = f"routine:{route.target['routine']}" if route is not None else None
        routine = self.pick(target, project)
        if routine is None:
            raise BlockError(f"no routine for that; routines: {', '.join(self.routines()) or 'none'}")
        name = routine["name"]
        project = project or routine["project"]
        out = await self.core.agent_send(call.principal, to=routine["agent"], text=task, project=project, allow_self=True)
        handle = out["handle"]
        call.note(target=f"routine:{name}", ref=handle, title=title, excerpt=task)
        token = self.token(routine)
        base = {**out, "routine": name, "routine_project": routine["project"]}
        if not token or not routine["routine_id"]:
            return {**base, "fired": False, "note": f"no token for routine {name}: left in the {routine['agent']} mailbox"}
        sender = self.core.agents.label(call.principal.source)
        text = task[:MAX_TEXT] + reply_hint(handle, sender, routine["project"])
        headers = {"Authorization": f"Bearer {token}", "anthropic-version": API_VERSION}
        bare = False
        try:
            resp = await self.http.post(FIRE_URL.format(routine_id=routine["routine_id"]), json={"text": text}, headers=headers)
            if resp.status_code == 403 and PAUSED in resp.text:
                bare = True
                resp = await self.http.post(FIRE_URL.format(routine_id=routine["routine_id"]), json={}, headers=headers)
        except httpx.HTTPError as e:
            log.warning("routine %s: fire failed: %s", name, type(e).__name__)
            self._record_fire(name, f"failed: {type(e).__name__}")
            return {**base, "fired": False, "note": f"routine {name} didn't start ({type(e).__name__}); the message is in the mailbox"}
        if resp.status_code >= 300:
            log.warning("routine %s: fire HTTP %d", name, resp.status_code)
            self._record_fire(name, f"failed: HTTP {resp.status_code}")
            return {**base, "fired": False, "note": f"routine {name} didn't start (HTTP {resp.status_code}); the message is in the mailbox"}
        self._record_fire(name, "ok")
        if bare:
            self.core.store.x("INSERT OR REPLACE INTO routine_pickups(handle, routine, routine_id, project, text, created_at) VALUES (?,?,?,?,?,?)",
                              (handle, name, routine["routine_id"], project, text, self.core.clock()))
        body = resp.json()
        session = {"session_id": body.get("claude_code_session_id"), "session_url": body.get("claude_code_session_url"), "routine": name}
        self.core.inbox.update_handle(handle, state={"routine": {**session, "bare": bare}})
        return {**base, "fired": True, **session, **({"woken_without_text": True} if bare else {}),
                "note": "the session answers with reply; it arrives in your inbox with in_reply_to set to this conversation (final=true for the answer)"
                        + ("; Anthropic refused the text for this Project routine (403 'this project is paused', anthropics/claude-code#102874), "
                           "so it was woken without it and collects the message with routine_pickup" if bare else "")}

    def pickup(self, routine: str | None) -> dict[str, Any]:
        """The message a routine was woken for without text: the oldest one waiting for that routine (by name or trig_ id),
        or for any routine when it doesn't know its own, with the switchboard note. Each message is handed out once."""
        want = (routine or "").strip().lower()
        args: list[Any] = [self.core.clock() - PICKUP_TTL_S]
        sql = "SELECT * FROM routine_pickups WHERE picked_at IS NULL AND created_at > ?"
        if want:
            sql += " AND (routine = ? OR lower(routine_id) = ?)"
            args += [want, want]
        for row in self.core.store.q(sql + " ORDER BY created_at", args):
            if self.core.store.n("UPDATE routine_pickups SET picked_at = ? WHERE handle = ? AND picked_at IS NULL", (self.core.clock(), row["handle"])):
                return {"conversation": row["handle"], "routine": row["routine"], "project": row["project"], "text": row["text"]}
        return {"conversation": None, "note": "nothing waiting" + (f" for {want}" if want else "")}

    async def pickup_action(self, call: Call, *, routine: str | None = None) -> dict[str, Any]:
        out = self.pickup(routine)
        call.note(target=out.get("routine") or routine or "-", ref=out.get("conversation"))
        return out

    def mcp_tools(self, mcp: Any, kit: Any) -> None:
        from .mcp_tools import register

        register(self, mcp, kit)

    async def aclose(self) -> None:
        await self.http.aclose()
