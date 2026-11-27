from __future__ import annotations

import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .audit import iso
from .config import NAMESPACE_RE
from .projects import ProjectError, Projects
from .store import Store, fts_query, new_id

MAX_BODY = 20_000
MAX_TITLE = 200
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
GENERAL = "general"
log = logging.getLogger("switchboard.memory")
CONFIRM_GENERAL = (
    "general is the owner's cross-project memory: every project and assistant reads it. Anything about one project belongs in "
    "project:<slug> (project_list shows them). If this really applies everywhere (his profile, a preference, a decision for all "
    "projects), confirm with the owner and retry with confirm_general=true."
)


class MemoryError_(ValueError):
    pass


class MemoryNotFound(MemoryError_):
    pass


def summary(body: str) -> str:
    return body.lstrip().partition("\n")[0].strip()[:200]


def _row(r: sqlite3.Row, body: bool = True, limit: int | None = None) -> dict[str, Any]:
    out = {
        "id": r["id"],
        "scope": r["scope"],
        "namespace": r["namespace"],
        "key": r["key"],
        "title": r["title"],
        "tags": [t for t in r["tags"].split(",") if t],
        "source": r["source"],
        "created_at": iso(r["created_at"]),
        "updated_at": iso(r["updated_at"]),
    }
    if body:
        text = r["body"]
        if limit is not None and len(text) > limit:
            text = text[:limit] + " …"
        out["body"] = text
    return out


class Memory:
    """Scoped memory: every entry lives in 'general' or in 'project:<slug>', with namespaces inside the scope."""

    def __init__(self, store: Store, projects: Projects, mirror_dir: str | None = None, mirror_debounce_s: float = 0):
        self.store = store
        self.projects = projects
        self.mirror_dir = Path(mirror_dir) if mirror_dir else None
        self.mirror_debounce_s = mirror_debounce_s
        self._mirror_lock = threading.Lock()
        self._mirror_timer: threading.Timer | None = None
        self._mirror_pending_since = 0.0
        if self.mirror_dir:
            self.flush_mirror()

    # Scopes -------------------------------------------------------------------------

    def write_scope(self, scope: str | None) -> str:
        """A scope for writing: required, explicit, and a live project when it isn't general."""
        s = (scope or "").strip().lower()
        if not s:
            raise MemoryError_("scope is required: 'general' or 'project:<slug>' (writes are never implicitly general)")
        if s == GENERAL:
            return s
        if not s.startswith("project:"):
            raise MemoryError_("scope must be 'general' or 'project:<slug>'")
        try:
            return self.projects.get(s[8:], allow_archived=False).scope
        except ProjectError as e:
            raise MemoryError_(str(e)) from e

    def explicit_scopes(self, scopes: list[str], project: str | None) -> list[str]:
        """Scopes a caller named on purpose, the named project first. A private project is readable only as the named project."""
        named = f"project:{project.strip().lower().removeprefix('project:')}" if project else None
        out: list[str] = []
        for raw in scopes:
            s = (raw or "").strip().lower()
            if s != GENERAL:
                if not s.startswith("project:"):
                    raise MemoryError_("scopes are 'general' or 'project:<slug>'")
                try:
                    p = self.projects.get(s[8:])
                except ProjectError as e:
                    raise MemoryError_(str(e)) from e
                if p.private and s != named:
                    raise MemoryError_(f"project {p.slug!r} is private: read it only with project={p.slug!r}")
            if s not in out:
                out.append(s)
        if not out:
            raise MemoryError_("scopes is empty")
        return sorted(out, key=lambda s: (s != named, s == GENERAL))

    def scope_of(self, id: str) -> str:
        r = self.store.one("SELECT scope FROM memories WHERE id = ? AND deleted_at IS NULL", (id,))
        if r is None:
            raise MemoryError_("memory not found")
        return r["scope"]

    def read_scopes(self, project: str | None, include_general: bool = True) -> list[str]:
        """Scopes a read covers, project first: the project plus general, or general alone."""
        if not project:
            return [GENERAL]
        slug = project.strip().lower().removeprefix("project:")
        if slug == GENERAL:
            return [GENERAL]
        try:
            p = self.projects.get(slug)
        except ProjectError as e:
            raise MemoryError_(str(e)) from e
        return [p.scope, GENERAL] if include_general else [p.scope]

    @staticmethod
    def _filter(scopes: list[str] | None, namespace: str | None) -> tuple[str, list[Any]]:
        sql, args = "", []
        if scopes is not None:
            sql += f" AND m.scope IN ({','.join('?' * len(scopes))})"
            args += scopes
        if namespace:
            ns = namespace.strip().strip("/").lower()
            sql += " AND (m.namespace = ? OR m.namespace LIKE ?)"
            args += [ns, ns + "/%"]
        return sql, args

    @staticmethod
    def _scope_rank(scopes: list[str] | None) -> tuple[str, list[Any]]:
        if not scopes or len(scopes) == 1:
            return "NULL", []
        return "CASE m.scope " + " ".join("WHEN ? THEN %d" % i for i in range(len(scopes))) + " END", list(scopes)

    # CRUD ---------------------------------------------------------------------------

    def validate(self, namespace: str, key: str | None, title: str, body: str) -> None:
        if not NAMESPACE_RE.match(namespace or ""):
            raise MemoryError_("namespace must be lowercase segments like 'profile', 'decisions' or 'notes/meetings' (max 3 levels)")
        if key is not None and not KEY_RE.match(key):
            raise MemoryError_("key must be a lowercase slug (a-z, 0-9, . _ -), max 80 chars")
        if not title.strip() or len(title) > MAX_TITLE:
            raise MemoryError_(f"title is required, max {MAX_TITLE} chars")
        if len(body) > MAX_BODY:
            raise MemoryError_(f"body is limited to {MAX_BODY} chars; split it into several entries")

    def write(self, *, source: str, scope: str | None, namespace: str, title: str, body: str, key: str | None = None,
              tags: list[str] | None = None, id: str | None = None, mode: str = "replace", confirm_general: bool = False,
              project: str | None = None) -> dict[str, Any]:
        scope = self.write_scope(scope)
        if project:
            named = project.strip().lower().removeprefix("project:")
            if scope != GENERAL and scope != f"project:{named}":
                raise MemoryError_(f"scope {scope} is not the project you named ({named!r}): write to project:{named}, or drop project if {scope} is right")
        if scope == GENERAL and not confirm_general:
            raise MemoryError_(CONFIRM_GENERAL)
        namespace = (namespace or "").strip().strip("/").lower()
        key = key.strip().lower() if key else None
        if mode not in ("replace", "append"):
            raise MemoryError_("mode must be replace or append")
        tag_s = ",".join(sorted({t.strip().lower().replace(",", " ") for t in (tags or []) if t.strip()}))
        now = self.store.clock()

        def run(db: sqlite3.Connection) -> tuple[str, str]:
            existing = None
            if id:
                existing = db.execute("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (id,)).fetchone()
                if existing is None:
                    raise MemoryError_(f"no memory with id {id}")
                if existing["scope"] != scope:
                    raise MemoryError_(f"memory {id} is in scope {existing['scope']}, not {scope}")
            elif key:
                existing = db.execute(
                    "SELECT * FROM memories WHERE scope = ? AND namespace = ? AND key = ? AND deleted_at IS NULL", (scope, namespace, key)
                ).fetchone()
            if existing is None:
                self.validate(namespace, key, title, body)
                mid = new_id("mem")
                db.execute(
                    "INSERT INTO memories(id, scope, namespace, key, title, body, tags, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (mid, scope, namespace, key, title.strip(), body, tag_s, source, now, now),
                )
                action = "create"
            else:
                mid = existing["id"]
                new_body = (existing["body"].rstrip() + "\n\n" + body) if mode == "append" else body
                new_title = title.strip() or existing["title"]
                self.validate(existing["namespace"], existing["key"], new_title, new_body)
                db.execute(
                    "UPDATE memories SET title = ?, body = ?, tags = ?, source = ?, updated_at = ? WHERE id = ?",
                    (new_title, new_body, tag_s or existing["tags"], source, now, mid),
                )
                action = mode
            r = db.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
            db.execute(
                "INSERT INTO memory_revisions(memory_id, at, source, action, title, body) VALUES (?,?,?,?,?,?)",
                (mid, now, source, action, r["title"], r["body"]),
            )
            return mid, action

        mid, action = self.store.tx(run)
        entry = self.read(id=mid)
        self._sync_mirror()
        return {"action": action, **{k: entry[k] for k in ("id", "scope", "namespace", "key", "title", "updated_at", "source")}}

    def read(self, *, id: str | None = None, scope: str | None = None, namespace: str | None = None, key: str | None = None) -> dict[str, Any]:
        if id:
            r = self.store.one("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (id,))
        elif scope and namespace and key:
            r = self.store.one(
                "SELECT * FROM memories WHERE scope = ? AND namespace = ? AND key = ? AND deleted_at IS NULL",
                (scope.strip().lower(), namespace.strip("/").lower(), key.lower()),
            )
        else:
            raise MemoryError_("pass id, or scope, namespace and key")
        if r is None:
            raise MemoryError_("memory not found")
        out = _row(r)
        revs = self.store.q("SELECT at, source, action FROM memory_revisions WHERE memory_id = ? ORDER BY at DESC LIMIT 5", (r["id"],))
        out["history"] = [{"at": iso(v["at"]), "source": v["source"], "action": v["action"]} for v in revs]
        return out

    def latest_writers(self, ids: list[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = self.store.q(
            "SELECT m.id, COALESCE((SELECT r.source FROM memory_revisions r WHERE r.memory_id = m.id "
            "AND r.action IN ('create', 'replace', 'append') ORDER BY r.at DESC, r.rowid DESC LIMIT 1), m.source) AS writer "
            "FROM memories m WHERE m.id IN (" + ",".join("?" for _ in ids) + ")", ids,
        )
        return {r["id"]: r["writer"] for r in rows}

    def delete(self, *, source: str, id: str, check_scope: Callable[[str], None] | None = None) -> dict[str, Any]:
        now = self.store.clock()

        def run(db: sqlite3.Connection) -> None:
            r = db.execute("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (id,)).fetchone()
            if r is None:
                raise MemoryNotFound("memory not found")
            if check_scope is not None:
                check_scope(r["scope"])
            db.execute("UPDATE memories SET deleted_at = ?, updated_at = ?, source = ? WHERE id = ?", (now, now, source, id))
            db.execute(
                "INSERT INTO memory_revisions(memory_id, at, source, action, title, body) VALUES (?,?,?,?,?,?)",
                (id, now, source, "delete", r["title"], r["body"]),
            )

        self.store.tx(run)
        self._sync_mirror()
        return {"deleted": id}

    def move(self, *, source: str, id: str, to_scope: str, check_scope: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Move one entry to another scope, keeping its id and revision history."""
        to = self.write_scope(to_scope)
        now = self.store.clock()

        def run(db: sqlite3.Connection) -> str:
            r = db.execute("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (id,)).fetchone()
            if r is None:
                raise MemoryNotFound("memory not found")
            if check_scope is not None:
                check_scope(r["scope"])
            if r["scope"] == to:
                raise MemoryError_(f"memory {id} is already in {to}")
            if r["key"] and db.execute("SELECT 1 FROM memories WHERE scope = ? AND namespace = ? AND key = ? AND deleted_at IS NULL",
                                       (to, r["namespace"], r["key"])).fetchone():
                raise MemoryError_(f"{to} already has {r['namespace']}/{r['key']}; merge or delete one of them first")
            db.execute("UPDATE memories SET scope = ?, source = ?, updated_at = ? WHERE id = ?", (to, source, now, id))
            db.execute(
                "INSERT INTO memory_revisions(memory_id, at, source, action, title, body) VALUES (?,?,?,?,?,?)",
                (id, now, source, f"move {r['scope']} -> {to}", r["title"], r["body"]),
            )
            return r["scope"]

        frm = self.store.tx(run)
        self._sync_mirror()
        return {"moved": id, "from": frm, "to": to}

    def list(self, *, scopes: list[str] | None, namespace: str | None = None, limit: int = 50, offset: int = 0,
             summaries: bool = False) -> dict[str, Any]:
        where, args = self._filter(scopes, namespace)
        rank, rargs = self._scope_rank(scopes)
        limit = max(1, min(limit, 200))
        rows = self.store.q(
            f"SELECT * FROM memories m WHERE m.deleted_at IS NULL{where} ORDER BY {rank}, m.updated_at DESC LIMIT ? OFFSET ?",
            [*args, *rargs, limit, offset],
        )
        counts = self.store.q(
            f"SELECT m.scope AS s, m.namespace AS ns, COUNT(*) AS n FROM memories m WHERE m.deleted_at IS NULL{where} GROUP BY m.scope, m.namespace ORDER BY m.scope, m.namespace",
            args,
        )
        totals: dict[str, dict[str, int]] = {}
        for c in counts:
            totals.setdefault(c["s"], {})[c["ns"]] = c["n"]
        return {"scopes": totals, "items": [{**_row(r, body=False), **({"summary": summary(r["body"])} if summaries else {})} for r in rows],
                "next_offset": offset + limit if len(rows) == limit else None}

    def search(self, *, query: str, scopes: list[str] | None, namespace: str | None = None, limit: int = 10,
               summaries: bool = False) -> dict[str, Any]:
        """Full-text search; with several scopes, matches in the first (the project) rank ahead of general."""
        limit = max(1, min(limit, 50))
        where, args = self._filter(scopes, namespace)
        rank, rargs = self._scope_rank(scopes)
        match = fts_query(query)
        if not match:
            raise MemoryError_("query has no searchable words")
        rows = self.store.q(
            f"""SELECT m.*, snippet(memories_fts, 1, '[', ']', ' … ', 24) AS snip
                FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid
                WHERE memories_fts MATCH ? AND m.deleted_at IS NULL{where}
                ORDER BY {rank}, bm25(memories_fts, 4.0, 1.0, 2.0, 1.0) LIMIT ?""",
            [match, *args, *rargs, limit],
        )
        if not rows:
            words = re.findall(r"\w+", query.lower())[:6]
            if words:
                cond = " AND ".join(["lower(m.title || ' ' || m.body || ' ' || m.tags) LIKE ?"] * len(words))
                rows = self.store.q(
                    f"SELECT m.*, substr(m.body, 1, 200) AS snip FROM memories m WHERE m.deleted_at IS NULL{where} AND ({cond}) ORDER BY {rank}, m.updated_at DESC LIMIT ?",
                    [*args, *[f"%{w}%" for w in words], *rargs, limit],
                )
        return {"scopes": scopes, "items": [{**_row(r, body=False), "snippet": r["snip"],
                                           **({"summary": summary(r["body"])} if summaries else {})} for r in rows]}

    def recent(self, *, scopes: list[str] | None, namespace: str | None = None, since: float | None = None, exclude_namespaces: tuple[str, ...] = (),
               limit: int = 10, body_limit: int = 600) -> list[dict[str, Any]]:
        where, args = self._filter(scopes, namespace)
        if since:
            where += " AND m.updated_at >= ?"
            args.append(since)
        for ns in exclude_namespaces:
            where += " AND m.namespace != ? AND m.namespace NOT LIKE ?"
            args += [ns, ns + "/%"]
        rows = self.store.q(f"SELECT * FROM memories m WHERE m.deleted_at IS NULL{where} ORDER BY m.updated_at DESC LIMIT ?", [*args, limit])
        return [_row(r, limit=body_limit) for r in rows]

    # Markdown export / mirror: one folder per scope, general/ and projects/<slug>/ ------------------------

    @staticmethod
    def rel_path(scope: str, namespace: str, key: str | None, id: str) -> Path:
        base = Path("general") if scope == GENERAL else Path("projects", scope.removeprefix("project:"))
        return base.joinpath(*namespace.split("/")) / f"{key or id}.md"

    @staticmethod
    def to_markdown(e: dict[str, Any]) -> str:
        front = ["---", f"id: {e['id']}", f"scope: {e['scope']}", f"namespace: {e['namespace']}"]
        if e.get("key"):
            front.append(f"key: {e['key']}")
        front += [f"source: {e['source']}", f"updated: {e['updated_at']}"]
        if e.get("tags"):
            front.append(f"tags: [{', '.join(e['tags'])}]")
        front += ["---", "", f"# {e['title']}", ""]
        return "\n".join(front) + "\n" + e["body"].rstrip() + "\n"

    def export(self, root: str | Path) -> int:
        root = Path(root)
        rows = self.store.q("SELECT * FROM memories WHERE deleted_at IS NULL ORDER BY scope, namespace, updated_at DESC")
        wanted = set()
        for r in rows:
            e = _row(r)
            rel = self.rel_path(e["scope"], e["namespace"], e["key"], e["id"])
            wanted.add(rel)
            _atomic_write(root / rel, self.to_markdown(e))
        for p in list(root.rglob("*.md")) if root.exists() else []:
            rel = p.relative_to(root)
            if rel != Path("INDEX.md") and rel not in wanted:
                p.unlink()
        for d in sorted((d for d in root.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True) if root.exists() else []:
            if not any(d.iterdir()):
                d.rmdir()
        _atomic_write(root / "INDEX.md", self.index_markdown(rows))
        return len(rows)

    @staticmethod
    def index_markdown(rows: list[sqlite3.Row]) -> str:
        lines = ["# Shared memory (switchboard export)", "", "Generated file: edits here are not read back. Write through the memory_* tools.", ""]
        current = None
        for r in rows:
            head = f"{r['scope']} / {r['namespace']}"
            if head != current:
                current = head
                lines += ["", f"## {head}", ""]
            rel = Memory.rel_path(r["scope"], r["namespace"], r["key"], r["id"]).as_posix()
            lines.append(f"- [{r['title']}]({rel}) ({r['source']}, {iso(r['updated_at'])})")
        return "\n".join(lines) + "\n"

    def _sync_mirror(self) -> None:
        """Rewrite the mirror after a change. A burst collapses into one export, at most debounce x 10 after its first change."""
        if not self.mirror_dir:
            return
        if self.mirror_debounce_s <= 0:
            self.flush_mirror()
            return
        with self._mirror_lock:
            now = time.monotonic()
            if self._mirror_timer:
                if now - self._mirror_pending_since >= self.mirror_debounce_s * 10:
                    return
                self._mirror_timer.cancel()
            else:
                self._mirror_pending_since = now
            self._mirror_timer = threading.Timer(self.mirror_debounce_s, self.flush_mirror)
            self._mirror_timer.daemon = True
            self._mirror_timer.start()

    schedule_mirror = _sync_mirror

    def flush_mirror(self) -> None:
        with self._mirror_lock:
            if self._mirror_timer:
                self._mirror_timer.cancel()
            self._mirror_timer = None
        if not self.mirror_dir:
            return
        try:
            self.export(self.mirror_dir)
        except OSError:
            log.exception("memory mirror export to %s failed", self.mirror_dir)

    @property
    def mirror_pending(self) -> bool:
        return self._mirror_timer is not None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == text:
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
