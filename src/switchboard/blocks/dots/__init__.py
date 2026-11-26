"""Dots: ChatGPT dots (always-on ChatGPT agents, each with its own Slack identity) reached through a dedicated Slack
relay channel.

Switchboard finds the dots itself: in the configured workspace it scans the members for bot users that look like dots
(an app id in app_ids, or a name matching match, "dot" by default), again on user_change and team_join and every
rescan_minutes, and registers each as a target named after its handle (example-dots -> @swb example-dots). Config can
add, rename or disable any of them under dots:. Like every target, a dot is private unless shared.

All traffic goes through one private relay channel (channel, default #swb-dots), which switchboard creates as the owner
when create is true. Switchboard never invites anyone: the root page and status name the one /invite the owner runs to
add @swb and the dots. The relay channel is never watched for routing. A send opens one Slack thread per conversation
and mentions the dot: the owner's own sends (his Slack messages routed here) post as him, through his user token and
with no approval prompt; his agents' sends post as @swb with the agent's name. The dot's replies in that thread come
back on the conversation, tied to the sender's inbox.
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
    "CREATE TABLE IF NOT EXISTS dot_relays (workspace TEXT PRIMARY KEY, channel TEXT NOT NULL, created INTEGER NOT NULL)",
)
SLUG_RE = re.compile(r"[^a-z0-9]+")

INSTRUCTIONS = """dots targets are ChatGPT dots in the owner's Slack: send to one (its handle, e.g. example-dots) and switchboard posts
it in the dots relay channel; the dot's answer comes back to your inbox on the conversation send returned."""

DOT_SNIPPET = """In Slack, the private channel #swb-dots is my switchboard relay. Posts there that mention you, from me or from @swb
on behalf of my agents, are my mailbox: answer each in its thread, and keep answering in that thread. Posts from
@swb name the agent that asked; treat them as my agents' requests, within what you'd do for me. Ignore anyone else."""


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
            "dots": {"type": "object"}}},
        instructions=INSTRUCTIONS,
    )

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        self.workspace = str(config.get("workspace") or "")
        self.channel_name = str(config.get("channel") or "#swb-dots")
        self.create = bool(config.get("create", True))
        self.app_ids = {str(a) for a in config.get("app_ids") or []}
        self.match = [str(m).lower() for m in config.get("match") or ["dot"]]
        self.rescan_s = float(config.get("rescan_minutes", 30)) * 60
        self.overrides = {str(k).lower(): (v or {}) for k, v in (config.get("dots") or {}).items()}
        self.problems_: list[str] = []
        for sql in SCHEMA:
            core.store.x(sql)

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

    async def start(self) -> None:
        engine = self.slack_engine
        if engine is not None and engine.connect:
            self._task = asyncio.create_task(self._loop(), name="dots")

    async def aclose(self) -> None:
        task = getattr(self, "_task", None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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

    def dot(self, ref: str) -> dict[str, Any] | None:
        ref = (ref or "").partition(":")[2] or ref
        return next((d for d in self.dots() if ref in (d["user_id"], d["slug"])), None)

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
        try:
            got = await rt.client("user" if "user" in rt.clients else "bot").conversations_members(channel=channel, limit=200)
            members = set(got.get("members") or [])
        except Exception as exc:
            members = set()
            self.problems_.append(f"can't read the relay channel's members: {type(exc).__name__}")
        missing = [f"<@{u}>" for u in [rt.bot_user_id, *(d["user_id"] for d in self.dots())] if u and members and u not in members]
        if missing:
            self.problems_.append(f"in #{name}, run: /invite {' '.join(missing)}  (switchboard never invites anyone itself)")
        return channel

    # Sending -----------------------------------------------------------------------------------

    async def targets(self) -> list[dict[str, Any]]:
        return [{"kind": "dot", "id": d["user_id"], "name": d["label"], "slugs": [d["slug"]]} for d in self.dots()]

    def problems(self) -> list[str]:
        return list(self.problems_)

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        rows = [(f"dot {d['slug']}", f"{d['label']} · {d['user_id']} in {self.workspace}", "found", True) for d in self.dots()]
        ch = self.relay_channel()
        rows.append(("Dots relay channel", f"#{self.channel_name.lstrip('#')} in {self.workspace or '-'}", ch or "not set up", bool(ch) and not self.problems_))
        return rows

    def actions(self) -> list[Action]:
        return [Action("send", "Message a dot", "Post in the dots relay channel, mentioning the dot; its reply comes back on the conversation.",
                       self.send, capped=True, burst=True),
                Action("result", "A dot conversation's replies", "The dot's replies so far on one conversation.", self.result, read_only=True)]

    async def result(self, call: Call, *, ref: str, tail: int | None = None, **_: Any) -> dict[str, Any]:
        h = self.core.inbox.handle(ref)
        if h is None or h["destination"] != self.name or h["owner"] != call.principal.source:
            raise BlockError(f"no dots conversation {ref!r}")
        replies = [{"text": d.get("text"), "final": d.get("final", True), "from": d.get("from")}
                   for d in (json.loads(r["data"]) for r in self.core.store.q("SELECT data FROM inbox WHERE handle = ? ORDER BY id", (ref,)))]
        return {"conversation": ref, "status": h["status"], "dot": h["meta"].get("dot"), "permalink": h["state"].get("permalink"),
                "final": replies[-1] if replies else None, "replies": replies[-(tail or 10):]}

    async def send(self, call: Call, *, task: str, target: str | None = None, title: str | None = None, project: str | None = None, **_: Any) -> dict[str, Any]:
        dot = self.dot(target or "")
        if dot is None:
            raise BlockError(f"no dot {target!r}; dots: {', '.join(d['slug'] for d in self.dots()) or 'none found yet'}")
        channel = self.relay_channel()
        if channel is None:
            raise BlockError("the dots relay channel isn't set up yet; see switchboard_status")
        from ...slack.config import Grant
        from ...slack.writes import OWNER_RELAY, WriteError
        from ..slack.intake import SLACK_SENDER

        p = call.principal
        sender = SLACK_SENDER.get() if p.kind == "slack" else None
        as_owner = bool(sender) and sender.get("trust") == "owner"
        handle = self.core.inbox.open_handle(owner=p.source, destination="dots", ref=dot["user_id"], project=project, title=title or f"dot {dot['slug']}",
                                             meta={"dot": dot["slug"], "workspace": self.workspace, "channel": channel}, status="sent", target=f"{self.name}:dot:{dot['user_id']}")
        label = (sender.get("user_name") or sender.get("user")) if sender else self.core.agents.label(p.source)
        if as_owner:
            body = re.sub(rf"^\s*(<@[A-Z0-9]+>\s*)*{re.escape(dot['slug'])}\b[\s:,]*", "", sender.get("text") or "", flags=re.I).strip() or task
            text = f"<@{dot['user_id']}> {body}"
        else:
            text = f"<@{dot['user_id']}> {task}\n\n_from {label} via switchboard_"
        if as_owner:
            key = Grant(id=OWNER_RELAY, persona=None, allow_persona_override=False, allowed_personas=None, scopes={self.workspace: ("user",)})
            identity = "user"
        else:
            persona = p.source if p.source in self.slack_engine.personas() else None
            key = Grant(id=f"dots:{p.source}", persona=persona, allow_persona_override=False, allowed_personas=None, scopes={self.workspace: ("bot",)})
            identity = "bot"
        try:
            out = await self.slack_engine.gate.submit(key, self.workspace, identity, "post_message", {"channel": channel, "text": text[:3900], "thread_ts": None})
        except WriteError as exc:
            raise BlockError(str(exc)) from exc
        if out.get("status") != "sent":
            return {"handle": handle, "conversation": handle, "dot": dot["slug"], "posted": False, **out}
        self.core.store.x("INSERT OR REPLACE INTO dot_threads(workspace, channel, thread_ts, handle, dot) VALUES (?,?,?,?,?)",
                          (self.workspace, channel, out["ts"], handle, dot["user_id"]))
        self.core.inbox.update_handle(handle, state={"thread_ts": out["ts"], "permalink": out.get("permalink")})
        call.note(target=f"dot:{dot['slug']}", ref=handle, excerpt=task)
        return {"handle": handle, "conversation": handle, "dot": dot["slug"], "posted": True, "as": "owner" if as_owner else "swb", "ts": out["ts"],
                "permalink": out.get("permalink"), "note": "the dot's reply in that thread comes back on this conversation"}

    async def on_relay_event(self, rt: Any, event: dict[str, Any]) -> None:
        """A message in the relay channel: a dot's reply in one of our threads goes back on its conversation. Everything
        else (our own posts, the owner typing there, other edits) is left alone, and nothing here is routed."""
        if event.get("subtype") not in (None, "bot_message", "thread_broadcast") or not event.get("thread_ts"):
            return
        who = {event.get("user"), event.get("bot_id")} - {None}
        dot = next((d for d in self.dots() if who & {d["user_id"], d.get("bot_id")}), None)
        if dot is None:
            return
        row = self.core.store.one("SELECT handle FROM dot_threads WHERE workspace = ? AND channel = ? AND thread_ts = ?",
                                  (self.workspace, event["channel"], event["thread_ts"]))
        if row is None:
            return
        from ...trust import wrap

        text = event.get("text") or ""
        sender = {"kind": "dot", "dot": dot["slug"], "user": dot["user_id"], "name": dot["label"], "workspace": self.workspace, "trust": "known",
                  "ts": event["ts"], "thread_ts": event["thread_ts"]}
        await self.core.inbox.add(row["handle"], "final", {"text": wrap(text, dot["label"], dot["user_id"], "known", f"in {self.workspace} relay"),
                                                           "raw_text": text, "final": True, "from": sender})
        self.core.inbox.update_handle(row["handle"], status="answered")


DotsDestination = DotsBlock
