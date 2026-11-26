"""Dots: ChatGPT dots (always-on ChatGPT agents, each with its own Slack identity) reached through a dedicated Slack
relay channel.

Switchboard finds the dots itself: in the configured workspace it scans the members for bot users that look like dots
(an app id in app_ids, or a name matching match, "dot" by default), again on user_change and team_join and every
rescan_minutes, and registers each as a target named after its handle (example-dots -> @swb example-dots). Config can
add, rename or disable any of them under dots:. Like every target, a dot is private unless shared.

All traffic goes through one private channel (channel, default #agents), which switchboard creates as the owner
when create is true. Switchboard never invites anyone: status names the one /invite the owner runs to add @swb and the
dots. The channel is never watched for routing. Each send is a new post by @swb that @mentions the dot: his agents
post under their own persona, his own Slack messages under "via <his name>". The dot's replies in that post's thread
come back on the conversation, tied to the sender's inbox.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from .. import Action, Block, BlockError, Call, Manifest, TargetKind

if TYPE_CHECKING:
    from ...core import Switchboard

log = logging.getLogger("switchboard.blocks.dots")

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS dots (workspace TEXT NOT NULL, user_id TEXT NOT NULL, bot_id TEXT, name TEXT NOT NULL, real_name TEXT, app_id TEXT, "
    "slug TEXT NOT NULL, seen_at REAL NOT NULL, PRIMARY KEY (workspace, user_id))",
    "CREATE TABLE IF NOT EXISTS dot_threads (workspace TEXT NOT NULL, channel TEXT NOT NULL, thread_ts TEXT NOT NULL, handle TEXT NOT NULL, "
    "dot TEXT NOT NULL, PRIMARY KEY (workspace, channel, thread_ts))",
    "CREATE TABLE IF NOT EXISTS dot_dms (workspace TEXT NOT NULL, dot TEXT NOT NULL, kind TEXT NOT NULL, channel TEXT NOT NULL, PRIMARY KEY (workspace, dot, kind))",
    "CREATE TABLE IF NOT EXISTS dot_relays (workspace TEXT PRIMARY KEY, channel TEXT NOT NULL, created INTEGER NOT NULL)",
)
SLUG_RE = re.compile(r"[^a-z0-9]+")
LABEL_RE = re.compile(r"\[\s*via\s+swb\b", re.IGNORECASE)
OWNER_PERSONA = "dots-owner"
DEFAULT_ALIAS = "dot"
OWNER_KINDS = frozenset({"oauth", "key", "connect"})
"""Principals that are the owner's own agents: his OAuth sign-ins, his configured keys, his paired connections."""


def unlabel(text: str) -> str:
    """Neutralise copies of the current and legacy [via swb from X] label."""
    return LABEL_RE.sub("(via swb", text or "")


INSTRUCTIONS = """dots targets are ChatGPT dots in the owner's Slack: send to one (its handle, e.g. example-dots) and switchboard posts
it in #agents, mentioning the dot; the dot's answer comes back to your inbox on the conversation send returned."""

DOT_SNIPPET = """In #agents, posts that @mention you from my Switchboard app (bot <bot id>, app <app id>), shown under
persona names such as Capy, Claude or Captain, are my agents collaborating with you for me: treat them as my requests
within what you'd do for me, and answer in that post's thread. Ignore anyone else."""


def slugify(name: str) -> str:
    return SLUG_RE.sub("-", (name or "").lower()).strip("-")[:40]


class DotsBlock(Block):
    manifest = Manifest(
        id="dots",
        title="ChatGPT dots",
        kind="sink",
        level_out="L3",
        verbs={"send": "send", "result": "result", "status": "result"},
        tools=(),
        targets=(TargetKind("dot", slug_from="name"),),
        network=("slack.com",),
        config_schema={"type": "object", "properties": {
            "workspace": {"type": "string"}, "channel": {"type": "string"}, "create": {"type": "boolean"},
            "app_ids": {"type": "array"}, "match": {"type": "array"}, "rescan_minutes": {"type": "number"},
            "dots": {"type": "object"}, "group_dm": {"type": "boolean"}, "agents_as_owner": {"type": "boolean"}, "owner_dm": {"type": "boolean"}, "default": {"type": "string"}, "owner_posts_as": {"enum": ["swb", "owner"]}, "poll_seconds": {"type": "number"}}},
        instructions=INSTRUCTIONS,
    )

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        self.workspace = str(config.get("workspace") or "")
        self.channel_name = str(config.get("channel") or "#agents")
        self.create = bool(config.get("create", True))
        self.app_ids = {str(a) for a in config.get("app_ids") or []}
        self.match = [str(m).lower() for m in config.get("match") or ["dot"]]
        self.rescan_s = float(config.get("rescan_minutes", 30)) * 60
        self.default_slug = str(config.get("default") or "").lower() or None
        self.overrides = {str(k).lower(): (v or {}) for k, v in (config.get("dots") or {}).items()}
        self.problems_: list[str] = []
        self.images: dict[str, str] = {}
        self.poll_s = float(config.get("poll_seconds", 5))
        self.use_group_dm = bool(config.get("group_dm", False))
        self.owner_dm_mode = bool(config.get("owner_dm", False))
        self.agents_as_owner = bool(config.get("agents_as_owner", False))
        self.owner_posts_as = "owner" if config.get("owner_posts_as") == "owner" else "swb"
        self.delivery_notes: dict[str, str] = {}
        for sql in SCHEMA:
            core.store.x(sql)
        cols = {r["name"] for r in core.store.q("PRAGMA table_info(dot_threads)")}
        for col, decl in (("mode", "TEXT NOT NULL DEFAULT 'relay'"), ("last_ts", "TEXT"), ("created_at", "REAL NOT NULL DEFAULT 0")):
            if col not in cols:
                core.store.x(f"ALTER TABLE dot_threads ADD COLUMN {col} {decl}")

    # Wiring ------------------------------------------------------------------------------------

    @property
    def slack_engine(self) -> Any:
        return self.core.slack

    def bind(self) -> None:
        engine = self.slack_engine
        if engine is None:
            return
        if not self.workspace:
            self.workspace = next(iter(engine.config.workspaces), "")
        engine.directory_listeners.append(self.on_directory)
        row = self.core.store.one("SELECT channel FROM dot_relays WHERE workspace = ?", (self.workspace,))
        if row is not None:
            engine.relays[(self.workspace, row["channel"])] = self.on_relay_event
            engine.owner_channels.add((self.workspace, row["channel"]))
        for r in self.core.store.q("SELECT channel, kind FROM dot_dms WHERE workspace = ?", (self.workspace,)):
            engine.relays[(self.workspace, r["channel"])] = self.on_relay_event
            engine.owner_channels.add((self.workspace, r["channel"]))

    async def start(self) -> None:
        engine = self.slack_engine
        if engine is not None and engine.connect:
            self._task = asyncio.create_task(self._loop(), name="dots")
            self._poll = asyncio.create_task(self._poll_loop(), name="dots-poll")

    async def aclose(self) -> None:
        tasks = [t for t in (getattr(self, "_task", None), getattr(self, "_poll", None)) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                await self.rescan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("dots rescan failed")
            await asyncio.sleep(self.rescan_s)

    def runtime(self) -> Any:
        engine = self.slack_engine
        if engine is None or self.workspace not in engine.runtimes:
            raise BlockError(f"dots need the Slack workspace {self.workspace or '(none)'} connected")
        return engine.runtimes[self.workspace]

    # Discovery ---------------------------------------------------------------------------------

    def looks_like_dot(self, m: dict[str, Any], bot_user: str | None) -> bool:
        if not m.get("is_bot") or m.get("deleted") or m.get("id") in (bot_user, "USLACKBOT"):
            return False
        profile = m.get("profile") or {}
        if self.app_ids and profile.get("api_app_id") in self.app_ids:
            return True
        names = " ".join(str(x or "") for x in (m.get("name"), m.get("real_name"), profile.get("real_name"), profile.get("display_name"))).lower()
        return any(p in names for p in self.match)

    async def rescan(self) -> list[dict[str, Any]]:
        """Find the workspace's dots, register them as targets, and make sure the relay channel exists."""
        rt = self.runtime()
        members, cursor = [], None
        for _ in range(20):
            resp = await rt.client("bot").users_list(limit=200, **({"cursor": cursor} if cursor else {}))
            members += resp.get("members") or []
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        found = [m for m in members if self.looks_like_dot(m, rt.bot_user_id)]
        for uid, o in self.overrides.items():
            if o.get("user_id") and not any(m["id"] == o["user_id"] for m in found):
                found.append(next((m for m in members if m.get("id") == o["user_id"]), {"id": o["user_id"], "name": uid, "profile": {}}))
        now = self.core.clock()
        for m in found:
            profile = m.get("profile") or {}
            if profile.get("image_192") or profile.get("image_72"):
                self.images[m["id"]] = profile.get("image_192") or profile.get("image_72")
            self.core.store.x("INSERT INTO dots(workspace, user_id, bot_id, name, real_name, app_id, slug, seen_at) VALUES (?,?,?,?,?,?,?,?) "
                              "ON CONFLICT(workspace, user_id) DO UPDATE SET bot_id = excluded.bot_id, name = excluded.name, real_name = excluded.real_name, "
                              "app_id = excluded.app_id, slug = excluded.slug, seen_at = excluded.seen_at",
                              (self.workspace, m["id"], profile.get("bot_id"), m.get("name") or m["id"], m.get("real_name") or profile.get("real_name"),
                               profile.get("api_app_id"), slugify(m.get("name") or m["id"]), now))
        self.core.changed()
        try:
            await self.ensure_channel(rt)
        except Exception as exc:
            log.warning("dots relay channel in %s: %s", self.workspace, exc)
            self.problems_.append(f"the dots relay channel failed: {str(exc)[:200]} (the user token needs groups:read and groups:write)")
        return self.dots()

    async def on_directory(self, label: str, event: dict[str, Any]) -> None:
        if label == self.workspace and (event.get("user") or {}).get("is_bot"):
            await self.rescan()

    def dots(self) -> list[dict[str, Any]]:
        out = []
        for r in self.core.store.q("SELECT * FROM dots WHERE workspace = ? ORDER BY name", (self.workspace,)):
            d = dict(r)
            o = self.overrides.get(d["slug"]) or next((v for v in self.overrides.values() if v.get("user_id") == d["user_id"]), {})
            if o.get("disabled"):
                continue
            d["slug"] = str(o.get("slug") or d["slug"]).lower()
            d["label"] = o.get("name") or d.get("real_name") or d["name"]
            out.append(d)
        return out

    def default_dot(self, dots: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """The owner's dot, the one "dot" names: config's default, else the only dot found."""
        dots = self.dots() if dots is None else dots
        if self.default_slug:
            return next((d for d in dots if self.default_slug in (d["slug"], d["user_id"].lower())), None)
        return dots[0] if len(dots) == 1 else None

    def dot(self, ref: str) -> dict[str, Any] | None:
        ref = (ref or "").partition(":")[2] or ref
        dots = self.dots()
        if ref.lower() == DEFAULT_ALIAS:
            return self.default_dot(dots)
        return next((d for d in dots if ref in (d["user_id"], d["slug"])), None)

    # The relay channel -------------------------------------------------------------------------

    def relay_channel(self) -> str | None:
        row = self.core.store.one("SELECT channel FROM dot_relays WHERE workspace = ?", (self.workspace,))
        return row["channel"] if row else None

    async def ensure_channel(self, rt: Any) -> str | None:
        """The relay channel: the configured id, else a private channel of that name the owner is in, else one created as
        him. Members are reported, never added: the owner runs the /invite."""
        self.problems_ = []
        channel = self.relay_channel()
        name = self.channel_name.lstrip("#")
        if channel is None and re.fullmatch(r"[CG][A-Z0-9]{6,}", name):
            channel = name
        if channel is None and "user" in rt.clients:
            resp = await rt.client("user").conversations_list(types="private_channel", exclude_archived=True, limit=1000)
            channel = next((c["id"] for c in resp.get("channels") or [] if c.get("name") == name), None)
            if channel is None and self.create:
                made = await rt.client("user").conversations_create(name=name, is_private=True)
                channel = made["channel"]["id"]
                self.slack_engine.store.audit("agent:dots", "relay_channel_created", self.workspace, channel=channel, name=name)
        if channel is None:
            self.problems_.append(f"no relay channel #{name} in {self.workspace}; create it, or set create: true and give the user token groups:write")
            return None
        self.core.store.x("INSERT INTO dot_relays(workspace, channel, created) VALUES (?,?,1) ON CONFLICT(workspace) DO UPDATE SET channel = excluded.channel",
                          (self.workspace, channel))
        self.slack_engine.relays[(self.workspace, channel)] = self.on_relay_event
        self.slack_engine.owner_channels.add((self.workspace, channel))
        try:
            got = await rt.client("user" if "user" in rt.clients else "bot").conversations_members(channel=channel, limit=200)
            members = set(got.get("members") or [])
        except Exception as exc:
            members = set()
            self.problems_.append(f"can't read the relay channel's members: {type(exc).__name__}")
        missing = [f"<@{u}>" for u in [rt.bot_user_id, *(d["user_id"] for d in self.dots())] if u and members and u not in members]
        self.relay_invite = f"/invite {' '.join(missing)}" if missing else None
        return channel

    # Sending -----------------------------------------------------------------------------------

    async def targets(self) -> list[dict[str, Any]]:
        dots = self.dots()
        default = self.default_dot(dots)
        return [{"kind": "dot", "id": d["user_id"], "name": d["label"],
                 "persona": {"id": f"dot-{d['slug']}", "name": d["label"], "icon_url": self.images.get(d["user_id"])},
                 "slugs": [d["slug"], *([DEFAULT_ALIAS] if default is d and d["slug"] != DEFAULT_ALIAS else [])]} for d in dots]

    def problems(self) -> list[str]:
        return list(self.problems_)

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        rows = [(f"dot {d['slug']}", f"{d['label']} ({d['user_id']}) in {self.workspace}", "found", True) for d in self.dots()]
        ch = self.relay_channel()
        invite = getattr(self, "relay_invite", None)
        rows.append(("Dots relay channel (fallback)", f"#{self.channel_name.lstrip('#')} in {self.workspace or '-'}",
                     (f"to use it, run in it: {invite} (switchboard never invites anyone itself)" if invite else ch) if ch else "not set up", bool(ch) and not invite))
        rows += [("Dots delivery", note, "falls back to the relay channel", None) for note in self.delivery_notes.values()]
        return rows

    def actions(self) -> list[Action]:
        return [Action("send", "Message a dot", "Post in the dots relay channel, mentioning the dot; its reply comes back on the conversation.",
                       self.send, capped=True),
                Action("result", "A dot conversation's replies", "The dot's replies so far on one conversation.", self.result, read_only=True)]

    async def result(self, call: Call, *, ref: str, tail: int | None = None, **_: Any) -> dict[str, Any]:
        h = self.core.inbox.handle(ref)
        if h is None or h["destination"] != self.name or h["owner"] != call.principal.source:
            raise BlockError(f"no dots conversation {ref!r}")
        replies = [{"text": d.get("text"), "final": d.get("final", True), "from": d.get("from")}
                   for d in (json.loads(r["data"]) for r in self.core.store.q("SELECT data FROM inbox WHERE handle = ? ORDER BY id", (ref,)))]
        return {"conversation": ref, "status": h["status"], "dot": h["meta"].get("dot"), "permalink": h["state"].get("permalink"),
                "final": replies[-1] if replies else None, "replies": replies[-(tail or 10):]}

    def owner_id(self) -> str | None:
        ws = self.slack_engine.config.workspaces.get(self.workspace)
        return next(iter(sorted(ws.owner_user_ids)), None) if ws is not None else None

    def _cached_dm(self, dot: dict[str, Any], kind: str) -> str | None:
        row = self.core.store.one("SELECT channel FROM dot_dms WHERE workspace = ? AND dot = ? AND kind = ?", (self.workspace, dot["user_id"], kind))
        return row["channel"] if row else None

    def _remember_dm(self, dot: dict[str, Any], kind: str, channel: str) -> str:
        self.core.store.x("INSERT OR REPLACE INTO dot_dms(workspace, dot, kind, channel) VALUES (?,?,?,?)", (self.workspace, dot["user_id"], kind, channel))
        self.slack_engine.relays[(self.workspace, channel)] = self.on_relay_event
        self.slack_engine.owner_channels.add((self.workspace, channel))
        return channel

    async def owner_dm(self, rt: Any, dot: dict[str, Any]) -> str | None:
        """The owner's own DM with the dot (found, else opened with his user token), or None when Slack won't give one."""
        channel = self._cached_dm(dot, "im")
        if channel or "user" not in rt.clients:
            return channel
        try:
            resp = await rt.client("user").conversations_list(types="im", limit=1000)
            channel = next((c["id"] for c in resp.get("channels") or [] if c.get("user") == dot["user_id"]), None)
            if channel is None:
                made = await rt.client("user").conversations_open(users=dot["user_id"])
                if not made.get("ok", True):
                    raise BlockError(made.get("error") or "refused")
                channel = made["channel"]["id"]
        except Exception as exc:
            self.delivery_notes[f"im:{dot['user_id']}"] = f"no DM with {dot['slug']}: {str(exc)[:160]}"
            return None
        self.delivery_notes.pop(f"im:{dot['user_id']}", None)
        return self._remember_dm(dot, "im", channel)

    async def group_dm(self, rt: Any, dot: dict[str, Any]) -> str | None:
        """A group DM of the owner, @swb and the dot, opened with the bot token, or None when Slack refuses."""
        channel = self._cached_dm(dot, "mpim")
        owner = self.owner_id()
        if channel or owner is None or "bot" not in rt.clients:
            return channel
        try:
            made = await rt.client("bot").conversations_open(users=f"{owner},{dot['user_id']}")
            if not made.get("ok", True):
                raise BlockError(made.get("error") or "refused")
            channel = made["channel"]["id"]
        except Exception as exc:
            self.delivery_notes[f"mpim:{dot['user_id']}"] = f"no group DM with {dot['slug']}: {str(exc)[:160]}"
            return None
        self.delivery_notes.pop(f"mpim:{dot['user_id']}", None)
        return self._remember_dm(dot, "mpim", channel)

    async def owner_persona(self, rt: Any, name: str | None) -> str:
        """A one-off persona for the owner's own sends posted by @swb: "via <his name>" with his Slack picture."""
        from ...slack.config import Persona

        engine = self.slack_engine
        icon = getattr(self, "_owner_icon", None)
        owner = self.owner_id()
        if icon is None and owner:
            try:
                profile = ((await rt.client("bot").users_info(user=owner)).get("user") or {}).get("profile") or {}
                icon = self._owner_icon = profile.get("image_192") or profile.get("image_72") or ""
            except Exception:
                icon = ""
        engine.dynamic_personas = {**engine.dynamic_personas, OWNER_PERSONA: Persona(id=OWNER_PERSONA, name=f"via {name or 'owner'}", icon_url=icon or None)}
        return OWNER_PERSONA

    async def send(self, call: Call, *, task: str, target: str | None = None, title: str | None = None, project: str | None = None, **_: Any) -> dict[str, Any]:
        """Sends go to the group DM of the owner, @swb and the dot, @mentioning the dot: agents post as @swb in their own
        persona, the owner's own Slack messages as @swb "via <him>" (or as him, owner_posts_as: owner). The relay channel is
        the fallback. With group_dm off, owner-trust sends post as him in his own DM with the dot instead."""
        dot = self.dot(target or "")
        if dot is None:
            raise BlockError(f"no dot {target!r}; dots: {', '.join(d['slug'] for d in self.dots()) or 'none found yet'}")
        from ...slack.config import Grant
        from ...slack.writes import OWNER_RELAY, WriteError
        from ..slack.intake import SLACK_SENDER

        rt = self.runtime()
        p = call.principal
        engine = self.slack_engine
        sender = SLACK_SENDER.get() if p.kind == "slack" else None
        owner_slack = bool(sender) and sender.get("trust") == "owner"
        owner_agent = p.kind in OWNER_KINDS
        if self.use_group_dm:
            as_him = owner_slack and self.owner_posts_as == "owner"
            channel, mode = await self.group_dm(rt, dot), "group_dm"
        else:
            as_him = (owner_slack and (self.owner_dm_mode or self.owner_posts_as == "owner")) or (owner_agent and self.agents_as_owner)
            channel, mode = (await self.owner_dm(rt, dot), "dm") if as_him and self.owner_dm_mode else (None, "")
        if not channel:
            channel, mode = self.relay_channel(), "relay"
        if channel is None:
            raise BlockError(f"Slack won't open a DM with {dot['slug']} and the dots relay channel isn't set up; see switchboard_status")
        handle = self.core.inbox.open_handle(handle=call.handle, owner=p.source, destination="dots", ref=dot["user_id"],
                                             project=project, title=title or f"dot {dot['slug']}",
                                             meta={"dot": dot["slug"], "workspace": self.workspace, "channel": channel, "mode": mode, **call.handle_meta},
                                             status="sent", target=f"{self.name}:dot:{dot['user_id']}")
        label = (sender.get("user_name") or sender.get("user")) if sender else self.core.agents.label(p.source)
        mention = "" if mode == "dm" else f"<@{dot['user_id']}> "
        if owner_slack:
            body = re.sub(rf"^\s*(<@[A-Z0-9]+>\s*)*{re.escape(dot['slug'])}\b[\s:,]*", "", sender.get("text") or "", flags=re.I).strip() or task
            text = f"{mention}{unlabel(body)}"
        elif as_him:
            text = f"{mention}[via swb from {' '.join(unlabel(label).replace(']', ')').split())}] {unlabel(task)}"
        elif owner_agent:
            text = f"{mention}{unlabel(task)}"
        else:
            text = f"{mention}{task}\n\n_from {label} via switchboard_"
        if as_him:
            key = Grant(id=OWNER_RELAY, persona=None, allow_persona_override=False, allowed_personas=None, scopes={self.workspace: ("user",)})
            identity = "user"
        else:
            if owner_slack:
                persona, by_ws = await self.owner_persona(rt, label), {}
            else:
                g = engine.config.grants.get(p.source)
                persona, by_ws = (g.persona, dict(g.persona_by_workspace)) if g is not None else (None, {})
            key = Grant(id=p.source if owner_agent else f"dots:{p.source}", persona=persona, allow_persona_override=False, allowed_personas=None,
                        scopes={self.workspace: ("bot",)}, persona_by_workspace=by_ws)
            identity = "bot"
        try:
            out = await engine.gate.submit(key, self.workspace, identity, "post_message", {"channel": channel, "text": text[:3900], "thread_ts": None})
        except WriteError as exc:
            raise BlockError(str(exc)) from exc
        if as_him:
            engine.store.audit(f"agent:{p.source}", "dot_post_as_owner", self.workspace, "user", sender=p.source, principal=p.kind,
                               client=p.client_id, slack_user=(sender or {}).get("user"), dot=dot["user_id"], channel=channel,
                               handle=handle, status=out.get("status"), ts=out.get("ts"))
        if out.get("status") != "sent":
            return {"handle": handle, "conversation": handle, "dot": dot["slug"], "posted": False, "mode": mode, **out}
        self.core.store.x("INSERT OR REPLACE INTO dot_threads(workspace, channel, thread_ts, handle, dot, mode, last_ts, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (self.workspace, channel, out["ts"], handle, dot["user_id"], mode, out["ts"], self.core.clock()))
        self.core.inbox.update_handle(handle, state={"thread_ts": out["ts"], "permalink": out.get("permalink"), "mode": mode})
        call.note(target=f"dot:{dot['slug']}", ref=handle, excerpt=task)
        return {"handle": handle, "conversation": handle, "dot": dot["slug"], "posted": True, "as": "owner" if as_him else "swb", "mode": mode,
                "channel": channel, "ts": out["ts"], "permalink": out.get("permalink"), "note": "the dot's reply comes back on this conversation"}

    def from_dot(self, m: dict[str, Any]) -> dict[str, Any] | None:
        if m.get("subtype") not in (None, "bot_message", "thread_broadcast"):
            return None
        who = {m.get("user"), m.get("bot_id")} - {None}
        return next((d for d in self.dots() if who & {d["user_id"], d.get("bot_id")}), None)

    def _row_for(self, channel: str, m: dict[str, Any], dot: dict[str, Any]) -> Any:
        """The conversation a dot message answers: its thread's, else (a top-level answer) the newest one sent to that dot there."""
        if m.get("thread_ts") and m["thread_ts"] != m.get("ts"):
            return self.core.store.one("SELECT * FROM dot_threads WHERE workspace = ? AND channel = ? AND thread_ts = ?", (self.workspace, channel, m["thread_ts"]))
        return self.core.store.one("SELECT * FROM dot_threads WHERE workspace = ? AND channel = ? AND dot = ? AND thread_ts < ? ORDER BY thread_ts DESC LIMIT 1",
                                   (self.workspace, channel, dot["user_id"], m.get("ts") or ""))

    async def deliver(self, row: Any, dot: dict[str, Any], m: dict[str, Any]) -> bool:
        if float(m["ts"]) <= float(row["last_ts"] or 0):
            return False
        self.core.store.x("UPDATE dot_threads SET last_ts = ? WHERE workspace = ? AND channel = ? AND thread_ts = ?",
                          (m["ts"], row["workspace"], row["channel"], row["thread_ts"]))
        from ...trust import wrap

        text = m.get("text") or ""
        sender = {"kind": "dot", "dot": dot["slug"], "user": dot["user_id"], "name": dot["label"], "workspace": self.workspace, "trust": "known",
                  "ts": m["ts"], "thread_ts": m.get("thread_ts"), "mode": row["mode"]}
        await self.core.inbox.add(row["handle"], "final", {"text": wrap(text, dot["label"], dot["user_id"], "known", f"in {self.workspace} Slack"),
                                                           "raw_text": text, "final": True, "from": sender})
        self.core.inbox.update_handle(row["handle"], status="answered")
        return True

    async def on_relay_event(self, rt: Any, event: dict[str, Any]) -> bool:
        """Consume dot messages, delivering any that match a conversation. Other events route normally."""
        dot = self.from_dot(event)
        if dot is None:
            return False
        if event.get("ts"):
            row = self._row_for(event["channel"], event, dot)
            if row is not None and row["dot"] == dot["user_id"]:
                await self.deliver(row, dot, event)
        return True

    async def poll_dms(self) -> int:
        """Read the dots' answers in DMs for an hour after each send: the owner's own DM with his user token (@swb can't
        see it), the group DM with the bot token (in case its events are missed). Each answer is delivered once."""
        rt = self.runtime()
        got = 0
        rows = self.core.store.q("SELECT * FROM dot_threads WHERE workspace = ? AND mode IN ('dm', 'group_dm') AND created_at > ? ORDER BY thread_ts",
                                 (self.workspace, self.core.clock() - 3600))
        dots = {d["user_id"]: d for d in self.dots()}
        for channel, mode in {(r["channel"], r["mode"]) for r in rows}:
            identity = "user" if mode == "dm" else "bot"
            if identity not in rt.clients:
                continue
            client = rt.client(identity)
            mine = [r for r in rows if r["channel"] == channel]
            oldest = min(float(r["last_ts"] or r["thread_ts"]) for r in mine)
            msgs = list((await client.conversations_history(channel=channel, oldest=f"{oldest:.6f}", limit=100)).get("messages") or [])
            for r in mine:
                msgs += [m for m in (await client.conversations_replies(channel=channel, ts=r["thread_ts"], limit=100)).get("messages") or []
                         if m.get("ts") != r["thread_ts"]]
            for m in sorted({m["ts"]: m for m in msgs if m.get("ts")}.values(), key=lambda m: float(m["ts"])):
                dot = self.from_dot(m)
                if dot is None or dot["user_id"] not in dots:
                    continue
                row = self._row_for(channel, m, dot)
                if row is not None and row["mode"] == mode and await self.deliver(row, dot, m):
                    got += 1
        return got

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            try:
                await self.poll_dms()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("dots DM poll failed")


DotsDestination = DotsBlock
