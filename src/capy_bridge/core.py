from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from .capy_api import CapyAPI, CapyError, pr_links, thread_summary
from .config import Config, Project, load_config
from .memory import Memory, iso
from .oauth import Auth, Principal
from .store import Store

log = logging.getLogger("capy_bridge")

MSG_LIMIT = 1500
RESULT_LIMIT = 20_000
PACK_LIMIT = 12_000


class BridgeError(Exception):
    pass


class Bridge:
    def __init__(
        self,
        config_path: str | Path | None,
        data_dir: str | Path,
        env: dict[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        capy_transport: httpx.AsyncBaseTransport | None = None,
        oauth_http: httpx.AsyncClient | None = None,
        access_verifier: Any = None,
        cfg: Config | None = None,
    ):
        self.cfg = cfg or load_config(config_path, env)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.store = Store(self.data_dir / "capy-bridge.db", clock=clock)
        self.memory = Memory(self.store, self.cfg.mirror_dir)
        self.auth = Auth(self.cfg, self.store, http=oauth_http, access_verifier=access_verifier, secret=self._secret())
        self.capy = CapyAPI(self.cfg.capy_api_base, self.cfg.capy_api_key, transport=capy_transport) if self.cfg.capy_api_key else None

    def _secret(self) -> bytes:
        path = self.data_dir / "form-secret"
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
            path.chmod(0o600)
        return path.read_bytes()

    async def aclose(self) -> None:
        if self.capy:
            await self.capy.aclose()
        await self.auth.http.aclose()
        self.store.close()

    def health(self) -> dict[str, Any]:
        problems = self.cfg.problems()
        return {
            "status": "degraded" if problems else "ok",
            "problems": problems,
            "oauth": bool(self.cfg.issuer),
            "offload": self.capy is not None,
            "static_keys": [k.name for k in self.cfg.static_keys],
            "mirror": bool(self.cfg.mirror_dir),
        }

    # Delegation guardrails ------------------------------------------------------

    def today(self) -> str:
        return datetime.fromtimestamp(self.clock(), ZoneInfo(self.cfg.timezone)).date().isoformat()

    def used_today(self) -> int:
        row = self.store.one("SELECT COUNT(*) AS n FROM delegations WHERE day = ? AND kind = 'delegate' AND outcome = 'ok'", (self.today(),))
        return row["n"]

    def log(self, p: Principal, kind: str, target: str, text: str, outcome: str, *, project_id: str | None = None, thread_id: str | None = None, title: str | None = None, detail: str | None = None) -> None:
        self.store.x(
            "INSERT INTO delegations(at, day, kind, source, client, target, project_id, thread_id, title, excerpt, outcome, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.clock(), self.today(), kind, p.source, p.client_id[:200], target, project_id, thread_id, title, text[:300], outcome, detail),
        )
        log.info("%s %s by %s (%s) -> %s %s", kind, outcome, p.source, p.client_id[:60], target, thread_id or detail or "")

    def need_capy(self) -> CapyAPI:
        if self.capy is None:
            raise BridgeError("Capy offload is not configured on this server (CAPY_API_KEY missing). Memory tools still work.")
        return self.capy

    def allowed_repos(self, project: Project | None) -> set[str]:
        if project:
            return set(project.repos)
        return {r for p in self.cfg.projects.values() for r in p.repos}

    async def _files(self, capy: CapyAPI, files: list[dict[str, Any]]) -> list[dict[str, str]]:
        decoded = []
        total = 0
        for f in files:
            name = str(f.get("name") or "").strip()
            if not name or "/" in name or len(name) > 200:
                raise BridgeError("each file needs a plain name like notes.md")
            if f.get("text") is not None:
                data = str(f["text"]).encode()
                media = f.get("media_type") or "text/plain"
            elif f.get("base64"):
                try:
                    data = base64.b64decode(str(f["base64"]), validate=True)
                except ValueError as e:
                    raise BridgeError(f"file {name}: base64 is invalid") from e
                media = f.get("media_type") or "application/octet-stream"
            else:
                raise BridgeError(f"file {name}: pass text or base64")
            if not data:
                raise BridgeError(f"file {name} is empty")
            total += len(data)
            if total > self.cfg.max_file_bytes:
                raise BridgeError(f"files exceed {self.cfg.max_file_bytes} bytes in total")
            decoded.append((name, str(media), data))
        return [await capy.upload(n, m, d) for n, m, d in decoded]

    async def delegate(self, p: Principal, *, task: str, title: str | None = None, target: str | None = None, repos: list[str] | None = None, files: list[dict[str, Any]] | None = None, crew: bool = False) -> dict[str, Any]:
        capy = self.need_capy()
        task = (task or "").strip()
        target = (target or self.cfg.default_target).strip()
        if not task:
            raise BridgeError("task is empty")

        def refuse(reason: str) -> BridgeError:
            self.log(p, "delegate", target, task, "refused", title=title, detail=reason)
            return BridgeError(reason)

        project = None
        if target == "captain":
            if not self.cfg.captain_id:
                raise refuse("no Captain is configured (capy.captain_id)")
        elif target in self.cfg.projects:
            project = self.cfg.projects[target]
            if crew and not self.cfg.captain_id:
                raise refuse("crew=true needs capy.captain_id")
        else:
            allowed = ["captain", *self.cfg.projects]
            raise refuse(f"target {target!r} is not allowed; use one of {allowed}")
        repos = [r.strip().lower() for r in (repos or []) if r.strip()]
        allowed_repos = self.allowed_repos(project)
        bad = [r for r in repos if r not in allowed_repos]
        if bad:
            raise refuse(f"repo {bad[0]!r} is not on the allowlist for {target}; allowed: {sorted(allowed_repos) or 'none configured'}")
        used = self.used_today()
        if used >= self.cfg.daily_cap:
            raise refuse(f"daily delegation cap reached ({used}/{self.cfg.daily_cap} today, {self.cfg.timezone}); try tomorrow or raise delegate.daily_cap")

        header = f"[Delegated via capy-bridge from {p.source}]"
        lines = [header, "", task]
        if repos:
            lines += ["", "Repos: " + ", ".join(repos)]
        text = "\n".join(lines)
        digest = hashlib.sha256(f"{p.source}|{self.today()}|{target}|{text}".encode()).hexdigest()
        try:
            uploaded = await self._files(capy, files) if files else []
            if project is None:
                receipt = await capy.send_message(self.cfg.captain_id, text, files=uploaded or None, delivery=self.cfg.captain_delivery, client_key=digest[:64])
                thread_id = self.cfg.captain_id
                extra = {"message_id": receipt.get("id"), "deduped": receipt.get("deduped", False)}
                project_id = None
            else:
                t = await capy.create_thread(
                    request_id=str(uuid.UUID(digest[:32])),
                    project_id=project.id,
                    message=text + ("\n\n(Attachments follow in the next message.)" if uploaded else ""),
                    title=title,
                    captain_id=self.cfg.captain_id if crew else None,
                )
                thread_id = t["id"]
                project_id = project.id
                if uploaded:
                    await capy.send_message(thread_id, "Attachments for the task above.", files=uploaded, delivery="steer", client_key=("files" + digest)[:64])
                extra = {"status": t.get("status")}
        except CapyError as e:
            self.log(p, "delegate", target, task, "error", title=title, detail=str(e))
            raise BridgeError(f"Capy API refused the delegation: {e}") from e
        self.log(p, "delegate", target, task, "ok", project_id=project_id, thread_id=thread_id, title=title)
        return {
            "thread_id": thread_id,
            "target": target,
            **extra,
            "delegations_today": f"{used + 1}/{self.cfg.daily_cap}",
            "next": "Poll with capy_status(thread_id); read the outcome with capy_result(thread_id) once status is idle or waiting.",
        }

    async def check_thread_allowed(self, thread_id: str) -> dict[str, Any]:
        capy = self.need_capy()
        t = await capy.get_thread(thread_id)
        in_crew = self.cfg.captain_id and (t.get("captain") or {}).get("jamId") == self.cfg.captain_id
        if thread_id == self.cfg.captain_id or in_crew or t.get("projectId") in {pr.id for pr in self.cfg.projects.values()}:
            return t
        raise BridgeError("that thread is outside the projects and Captain this bridge may touch")

    # Reading Capy work -------------------------------------------------------------

    @staticmethod
    def _msg(m: dict[str, Any], limit: int = MSG_LIMIT) -> dict[str, Any]:
        text = m.get("text") or ""
        out = {"from": m.get("authorName") or m.get("source"), "source": m.get("source"), "at": m.get("createdAt"), "text": text[:limit] + (" …" if len(text) > limit else "")}
        if m.get("attachments"):
            out["attachments"] = [a["file"]["name"] for a in m["attachments"] if a.get("file")]
        return out

    async def status(self, thread_id: str, messages: int = 5, include_tools: bool = False) -> dict[str, Any]:
        capy = self.need_capy()
        t = await self.check_thread_allowed(thread_id)
        tail = await capy.tail_messages(thread_id, limit=50)
        shown = [m for m in tail if include_tools or m.get("source") != "tool"][-max(1, min(messages, 20)):]
        return {**thread_summary(t), "pull_requests": pr_links(tail), "latest": [self._msg(m) for m in shown]}

    async def result(self, thread_id: str, tail: int = 0) -> dict[str, Any]:
        capy = self.need_capy()
        t = await self.check_thread_allowed(thread_id)
        msgs = await capy.tail_messages(thread_id, limit=60)
        final = next((m for m in reversed(msgs) if m.get("source") == "assistant"), None)
        out: dict[str, Any] = {**thread_summary(t), "pull_requests": pr_links(msgs)}
        if t.get("status") == "working":
            out["note"] = "The thread is still working; this is its latest reply so far."
        if final is None:
            out["final_reply"] = None
        else:
            out["final_reply"] = self._msg(final, RESULT_LIMIT)
            atts = []
            for a in final.get("attachments") or []:
                f = a.get("file") or {}
                try:
                    d = await capy.download_url(thread_id, f["fileId"])
                    atts.append({"name": f.get("name"), "media_type": f.get("mediaType"), "size": f.get("sizeBytes"), "title": a.get("title"), "url": d["url"], "expires_at": d.get("expiresAt")})
                except (CapyError, KeyError) as e:
                    atts.append({"name": f.get("name"), "error": str(e)})
            out["attachments"] = atts
        if tail:
            out["transcript_tail"] = [self._msg(m, 600) for m in msgs if m.get("source") != "tool"][-min(tail, 30):]
        return out

    async def reply(self, p: Principal, thread_id: str, text: str, delivery: str = "steer") -> dict[str, Any]:
        capy = self.need_capy()
        if delivery not in ("queue", "steer", "interrupt"):
            raise BridgeError("delivery must be queue, steer or interrupt")
        if not text.strip():
            raise BridgeError("text is empty")
        t = await self.check_thread_allowed(thread_id)
        body = f"[Reply via capy-bridge from {p.source}]\n\n{text.strip()}"
        try:
            r = await capy.send_message(thread_id, body, delivery=delivery)
        except CapyError as e:
            self.log(p, "reply", thread_id, text, "error", thread_id=thread_id, detail=str(e))
            raise BridgeError(f"Capy API refused the reply: {e}") from e
        self.log(p, "reply", thread_id, text, "ok", project_id=t.get("projectId"), thread_id=thread_id)
        return {"thread_id": thread_id, "message_id": r.get("id"), "delivery": delivery}

    async def list(self, scope: str = "delegated", project: str | None = None, limit: int = 10) -> dict[str, Any]:
        capy = self.need_capy()
        limit = max(1, min(limit, 30))
        if scope == "delegated":
            rows = self.store.q(
                "SELECT thread_id, MAX(at) AS at, source, target, title, excerpt FROM delegations WHERE kind = 'delegate' AND outcome = 'ok' GROUP BY thread_id ORDER BY at DESC LIMIT ?",
                (limit,),
            )

            async def one(r: Any) -> dict[str, Any]:
                base = {"thread_id": r["thread_id"], "delegated_at": iso(r["at"]), "by": r["source"], "target": r["target"], "task": r["title"] or r["excerpt"][:120]}
                try:
                    return {**base, **thread_summary(await capy.get_thread(r["thread_id"]))}
                except CapyError as e:
                    return {**base, "error": str(e)}

            return {"scope": scope, "items": list(await asyncio.gather(*(one(r) for r in rows)))}
        if scope == "captain":
            if not self.cfg.captain_id:
                raise BridgeError("no Captain is configured")
            crew = await capy.captain_crew(self.cfg.captain_id)
            crew.sort(key=lambda t: t.get("lastActivityAt") or t.get("updatedAt") or "", reverse=True)
            return {"scope": scope, "items": [thread_summary(t) for t in crew[:limit]]}
        if scope == "project":
            if not project or project not in self.cfg.projects:
                raise BridgeError(f"project must be one of {list(self.cfg.projects)}")
            page = await capy.list_threads(self.cfg.projects[project].id, limit=limit)
            return {"scope": scope, "project": project, "items": [thread_summary(t) for t in page.get("items", [])]}
        raise BridgeError("scope must be delegated, captain or project")

    async def resolve_captain(self, captain: str | None) -> str:
        if not captain:
            if not self.cfg.captain_id:
                raise BridgeError("no Captain is configured; pass captain")
            return self.cfg.captain_id
        if captain.startswith("jam_"):
            return captain
        for c in await self.need_capy().list_captains():
            if captain.lower() in ((c.get("name") or "").lower(), (c.get("title") or "").lower()):
                return c["id"]
        raise BridgeError(f"no Captain named {captain!r}")

    async def captain_digest(self, captain: str | None = None) -> dict[str, Any]:
        capy = self.need_capy()
        cid = await self.resolve_captain(captain)
        d, crew = await asyncio.gather(capy.captain_digest(cid), capy.captain_crew(cid))
        md = d.get("markdown")
        return {
            "captain_id": cid,
            "file": d.get("path"),
            "updated_at": d.get("updatedAt"),
            "markdown": md[:60_000] if md else None,
            "crew_needs_you": [thread_summary(t) for t in crew if t.get("needsYou")],
            "crew_working": sum(1 for t in crew if t.get("status") == "working"),
        }

    # Context pack ---------------------------------------------------------------------

    async def context_pack(self, project: str | None = None) -> str:
        week = self.clock() - 7 * 86400
        out: list[str] = [f"# Shared context (capy-bridge, {iso(self.clock())})"]

        def section(title: str, entries: list[dict[str, Any]]) -> None:
            if not entries:
                return
            out.append(f"\n## {title}")
            for e in entries:
                out.append(f"- **{e['title']}** ({e['namespace']}, {e['source']}, {e['updated_at'][:10]}, id {e['id']}): {e['body'].strip()}")

        section("Profile", self.memory.recent(namespace="profile", limit=8, body_limit=500))
        if project:
            section(f"Project {project}", self.memory.recent(namespace=f"projects/{project}", limit=8, body_limit=500))
        section("Recent decisions", self.memory.recent(namespace="decisions", limit=8, since=self.clock() - 30 * 86400, body_limit=300))
        other = [e for e in self.memory.recent(since=week, limit=15, body_limit=0) if not e["namespace"].startswith(("profile", "decisions")) and not (project and e["namespace"].startswith(f"projects/{project}"))]
        if other:
            out.append("\n## Other memory changed this week")
            out += [f"- {e['title']} ({e['namespace']}, {e['source']}, id {e['id']})" for e in other[:10]]

        if self.capy is None:
            out.append("\n## Capy\nOffload is not configured on this server.")
        else:
            try:
                work = await self.list("delegated", limit=8)
                active = [w for w in work["items"] if w.get("status") in ("working", "waiting") or w.get("needs_you")]
                if active:
                    out.append("\n## Active Capy work")
                    out += [f"- {w.get('title') or w['task']} [{w.get('status')}{', needs you' if w.get('needs_you') else ''}] thread {w['thread_id']}" for w in active]
                if self.cfg.captain_id:
                    crew = await self.capy.captain_crew(self.cfg.captain_id)
                    asks = [t for t in crew if t.get("needsYou")]
                    working = [t for t in crew if t.get("status") == "working"]
                    out.append(f"\n## Captain crew\n{len(working)} working, {len(asks)} need the owner.")
                    if asks:
                        out.append("\n## Open asks")
                        out += [f"- {(t.get('ask') or {}).get('text') or t.get('title')} (thread {t['id']})" for t in asks[:10]]
            except (CapyError, BridgeError) as e:
                out.append(f"\n## Capy\nCould not read Capy work: {e}")
        text = "\n".join(out)
        return text[:PACK_LIMIT] + ("\n…(truncated)" if len(text) > PACK_LIMIT else "")

    def recent_delegations(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.store.q("SELECT * FROM delegations ORDER BY at DESC LIMIT ?", (limit,))
        return [{"at": iso(r["at"]), "kind": r["kind"], "source": r["source"], "client": r["client"], "target": r["target"], "thread_id": r["thread_id"], "outcome": r["outcome"], "detail": r["detail"], "excerpt": r["excerpt"][:120]} for r in rows]

