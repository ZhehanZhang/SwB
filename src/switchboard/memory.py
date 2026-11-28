from __future__ import annotations

import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .audit import iso
from .config import NAMESPACE_RE, SLUG_RE
from .connect import AGENT_RE
from .projects import ProjectError, Projects
from .refusals import CONFIRMATION_REQUIRED, PolicyRefusal
from .store import Store, fts_query, memory_hash, new_id
from .trust import MEMORY_IMPORT_PREFIX, MEMORY_TRUNCATION, MEMORY_TRUST, memory_label, unframe_memory, wrap_memory

MAX_BODY = 20_000
MAX_TITLE = 200
MAX_VIEW = 16_000
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
GENERAL = "general"
SYNC_SOURCES = frozenset(("claude-memory", "chatgpt-memory", "grok-memory", "gemini-memory", "claude-code-auto-memory",
                          "claude-md", "agents-md", "codex-memory", "capy-drive", "other"))
log = logging.getLogger("switchboard.memory")
CONFIRM_GENERAL = ("Save project facts in project:<slug>. For facts that apply to every project, "
                   "confirm the write with the owner and pass confirm_general=true.")


class MemoryError_(ValueError):
    pass


class MemoryNotFound(MemoryError_):
    pass


class MemoryRefused(PolicyRefusal, MemoryError_):
    pass


def missing_path(path: str) -> str:
    return f"Error: The path {path} does not exist. Please provide a valid path."


def scope_path(scope: str) -> str:
    if scope == GENERAL:
        return "general"
    kind, _, name = scope.partition(":")
    return f"{'agents' if kind == 'agent' else 'projects'}/{name}"


def slug_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).lstrip("._-")[:80] or "memory"


@dataclass(frozen=True)
class MemoryPath:
    path: str
    scope: str | None
    namespace: str = ""
    key: str | None = None

    @classmethod
    def parse(cls, path: str) -> MemoryPath:
        decoded = unquote(path, errors="replace")
        pieces = decoded.split("/")
        if (pieces[:2] != ["", "memories"] or len(pieces) > 8 or decoded.count("/") != path.count("/")
                or any(p in ("", ".", "..") for p in pieces[2:])
                or any(c in "\\%\ufffd" or ord(c) < 32 or ord(c) == 127 for c in decoded)):
            raise MemoryError_("Error: Use a valid path under /memories.")
        tail = pieces[2:]
        if not tail or tail in (["projects"], ["agents"]):
            return cls(decoded, None)
        kind, *rest = tail
        if kind == GENERAL:
            scope = GENERAL
        elif kind in ("projects", "agents") and rest:
            name, *rest = rest
            if not (SLUG_RE if kind == "projects" else AGENT_RE).fullmatch(name) or (kind == "projects" and name == GENERAL):
                raise MemoryError_("Error: Use a valid scope under /memories.")
            scope = f"{'project' if kind == 'projects' else 'agent'}:{name}"
        else:
            raise MemoryError_("Error: Use general, projects/<slug>, or agents/<name>.")
        key = None
        if rest and rest[-1].lower().endswith((".md", ".txt")):
            filename = rest.pop()
            if not rest:
                raise MemoryError_(f"Error: Put the file in a topic folder, such as /memories/{scope_path(scope)}/notes/{filename}.")
            key = slug_key(filename[:-3] if filename.lower().endswith(".md") else filename[:-4])
        namespace = "/".join(rest)
        if namespace and not NAMESPACE_RE.fullmatch(namespace):
            raise MemoryError_("Error: Use one to three lowercase topic folders.")
        return cls(decoded, scope, namespace, key)

    @property
    def normalized(self) -> str:
        if self.scope is None:
            return self.path
        return "/".join(filter(None, ("/memories", scope_path(self.scope), self.namespace, f"{self.key}.md" if self.key else None)))


def row_path(row: sqlite3.Row) -> str:
    return f"/memories/{Memory.rel_path(row['scope'], row['namespace'], row['key'], row['id']).as_posix()}"


def frame_entry(entry: dict[str, Any], text: str, *, by: str | None = None, at: str | None = None,
                trust: str | None = None) -> str:
    return wrap_memory(text, entry["path"], by or entry["content_by"], at or entry["updated_at"] or "1970-01-01T00:00:00+00:00",
                       trust or entry["content_trust"])


def _tags(tags: list[str] | None) -> str:
    return ",".join(sorted({t.strip().lower().replace(",", " ") for t in (tags or []) if t.strip()}))


def since_time(since: str) -> float:
    try:
        value = datetime.fromisoformat(since)
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, OverflowError) as e:
        raise MemoryError_("Use an ISO date or time for since.") from e


def _numbered(lines: list[str], start: int) -> str:
    return "\n".join(f"{i:6}\t{line}" for i, line in enumerate(lines, start))


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
        "path": row_path(r),
        "created_by": r["created_by"],
        "content_by": r["content_by"],
        "content_trust": r["content_trust"],
        "sync_source": r["sync_source"],
        "deleted_by": r["deleted_by"],
        "deleted_at": iso(r["deleted_at"]),
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
    """Scoped memory with namespaces inside each general, project or agent scope."""

    def __init__(self, store: Store, projects: Projects, mirror_dir: str | None = None, mirror_debounce_s: float = 0):
        self.store = store
        self.projects = projects
        self.mirror_dir = Path(mirror_dir) if mirror_dir else None
        self.mirror_debounce_s = mirror_debounce_s
        self.listeners: list[Callable[[], None]] = []
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
            raise MemoryError_("scope is required: 'general', 'project:<slug>' or 'agent:<name>'")
        if s == GENERAL or (s.startswith("agent:") and AGENT_RE.fullmatch(s[6:])):
            return s
        if not s.startswith("project:"):
            raise MemoryError_("scope must be 'general', 'project:<slug>' or 'agent:<name>'")
        try:
            return self.projects.get(s[8:], allow_archived=False).scope
        except ProjectError as e:
            raise MemoryError_(str(e)) from e

    def explicit_scopes(self, scopes: list[str], project: str | None) -> list[str]:
        """Scopes a caller named on purpose, the named project first."""
        named = f"project:{project.strip().lower().removeprefix('project:')}" if project else None
        out: list[str] = []
        for raw in scopes:
            s = (raw or "").strip().lower()
            if s != GENERAL and not (s.startswith("agent:") and AGENT_RE.fullmatch(s[6:])):
                if not s.startswith("project:"):
                    raise MemoryError_("scopes are 'general', 'project:<slug>' or 'agent:<name>'")
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

    def scope_of(self, id: str | None, *, scope: str | None = None, namespace: str | None = None, key: str | None = None) -> str:
        return self._find("scope", id, scope, namespace, key)["scope"]

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
        if not NAMESPACE_RE.fullmatch(namespace or ""):
            raise MemoryError_("namespace must be lowercase segments like 'profile', 'decisions' or 'notes/meetings' (max 3 levels)")
        if key is not None and not KEY_RE.fullmatch(key):
            raise MemoryError_("key must be a lowercase slug (a-z, 0-9, . _ -), max 80 chars")
        if not title.strip() or len(title) > MAX_TITLE:
            raise MemoryError_(f"title is required, max {MAX_TITLE} chars")
        if len(body) > MAX_BODY:
            raise MemoryError_(f"body is limited to {MAX_BODY} chars; split it into several entries")

    def write(self, *, source: str, scope: str | None, namespace: str, title: str, body: str, key: str | None = None,
              tags: list[str] | None = None, id: str | None = None, mode: str = "replace", confirm_general: bool = False,
              project: str | None = None, writer_credential: str | None = None, content_trust: str = "agent",
              path: MemoryPath | None = None, overwrite: bool = False,
              check_writer: Callable[[str, str | None], None] | None = None) -> dict[str, Any]:
        scope = self.write_scope(scope)
        if project and scope.startswith("project:"):
            named = project.strip().lower().removeprefix("project:")
            if scope != f"project:{named}":
                raise MemoryError_(f"scope {scope} is not the project you named ({named!r}): write to project:{named}, or drop project if {scope} is right")
        if scope == GENERAL and not confirm_general:
            raise MemoryRefused(CONFIRMATION_REQUIRED, CONFIRM_GENERAL)
        namespace = (namespace or "").strip().strip("/").lower()
        key = key.strip().lower() if key else None
        if mode not in ("replace", "append"):
            raise MemoryError_("mode must be replace or append")
        tag_s, body = _tags(tags), unframe_memory(body)
        now = self.store.clock()

        def run(db: sqlite3.Connection) -> tuple[sqlite3.Row, str]:
            existing = None
            if path:
                existing = self._path_row(db, path)
                if existing is not None and not overwrite:
                    raise MemoryError_(f"Error: File {path.path} already exists")
            elif id:
                existing = db.execute("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (id,)).fetchone()
                if existing is None:
                    raise MemoryError_(f"no memory with id {id}")
            elif key:
                existing = db.execute(
                    "SELECT * FROM memories WHERE scope = ? AND namespace = ? AND key = ? AND deleted_at IS NULL", (scope, namespace, key)
                ).fetchone()
            if existing is None:
                self.validate(namespace, key, title, body)
                if (path or not key) and (duplicate := self._duplicate(db, scope, memory_hash(body))):
                    return duplicate, "duplicate"
                mid = self._create(db, source, now, scope, namespace, key, title.strip(), body, tag_s, writer_credential, content_trust)
                action = "create"
            else:
                if check_writer is not None:
                    check_writer(existing["scope"], existing["writer_credential"])
                if existing["scope"] != scope:
                    raise MemoryError_(f"memory {id} is in scope {existing['scope']}, not {scope}")
                mid = existing["id"]
                new_body = (existing["body"].rstrip() + "\n\n" + body) if mode == "append" else body
                new_title = title.strip() or existing["title"]
                self.validate(existing["namespace"], existing["key"], new_title, new_body)
                self._replace(db, existing, source, now, new_title, new_body, tag_s or existing["tags"], writer_credential, content_trust)
                action = mode
            self.revision(db, mid, now, source, action)
            return db.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone(), action

        row, action = self.store.tx(run)
        if action != "duplicate":
            self.changed()
        entry = _row(row, body=False)
        if action == "duplicate":
            return {"action": action, "id": row["id"], "path": entry["path"]}
        return {"action": action, **{k: entry[k] for k in ("id", "path", "scope", "namespace", "key", "title", "updated_at", "source")}}

    @staticmethod
    def revision(db: sqlite3.Connection, id: str, now: float, source: str, action: str) -> None:
        db.execute(
            "INSERT INTO memory_revisions(memory_id, at, source, action, title, body, scope, namespace, key, tags, trust)"
            " SELECT id, ?, ?, ?, title, body, scope, namespace, key, tags, content_trust FROM memories WHERE id = ?",
            (now, source, action, id),
        )

    @staticmethod
    def _duplicate(db: sqlite3.Connection, scope: str, digest: str, id: str = "") -> sqlite3.Row | None:
        return db.execute("SELECT * FROM memories WHERE scope = ? AND content_hash = ? AND deleted_at IS NULL AND id != ?"
                          " ORDER BY created_at, id LIMIT 1", (scope, digest, id)).fetchone()

    @staticmethod
    def _create(db: sqlite3.Connection, source: str, now: float, scope: str, namespace: str, key: str | None,
                title: str, body: str, tags: str, writer: str | None, trust: str, *, sync_source: str | None = None,
                sync_key: str | None = None) -> str:
        mid = new_id("mem")
        db.execute(
            "INSERT INTO memories(id, scope, namespace, key, title, body, tags, source, created_at, updated_at, writer_credential,"
            " created_by, content_by, content_trust, content_hash, sync_source, sync_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, scope, namespace, key, title, body, tags, source, now, now, writer, source, sync_source or source, trust,
             memory_hash(body), sync_source, sync_key),
        )
        return mid

    @staticmethod
    def _replace(db: sqlite3.Connection, row: sqlite3.Row, source: str, now: float, title: str, body: str, tags: str,
                 writer: str | None, trust: str, *, content_by: str | None = None) -> None:
        changed = (title, body) != (row["title"], row["body"])
        db.execute(
            "UPDATE memories SET title = ?, body = ?, tags = ?, source = ?, updated_at = ?, writer_credential = ?,"
            " content_by = ?, content_trust = ?, content_hash = ?, deleted_at = NULL, deleted_by = NULL WHERE id = ?",
            (title, body, tags, source, now, writer, (content_by or source) if changed else row["content_by"],
             trust if changed else row["content_trust"], memory_hash(body), row["id"]),
        )

    @staticmethod
    def _path_row(db: sqlite3.Connection | Store, path: MemoryPath, *, deleted: bool = False) -> sqlite3.Row | None:
        if path.key is None:
            return None
        sql = ("SELECT * FROM memories WHERE scope = ? AND namespace = ? AND COALESCE(key, id) = ?"
               + ("" if deleted else " AND deleted_at IS NULL")
               + " ORDER BY deleted_at IS NOT NULL, updated_at DESC, rowid DESC LIMIT 1")
        args = (path.scope, path.namespace, path.key)
        return db.one(sql, args) if isinstance(db, Store) else db.execute(sql, args).fetchone()

    @classmethod
    def locate(cls, db: sqlite3.Connection | Store, id: str | None = None, path: MemoryPath | None = None,
               *, deleted: bool = False) -> sqlite3.Row:
        if path:
            row = cls._path_row(db, path, deleted=deleted)
        elif id:
            sql = "SELECT * FROM memories WHERE id = ?" + ("" if deleted else " AND deleted_at IS NULL")
            row = db.one(sql, (id,)) if isinstance(db, Store) else db.execute(sql, (id,)).fetchone()
        else:
            raise MemoryError_("Pass id or path.")
        if row is None:
            raise MemoryNotFound(missing_path(path.path) if path else "memory not found")
        return row

    def _find(self, fields: str, id: str | None, scope: str | None, namespace: str | None, key: str | None) -> Any:
        if id:
            r = self.store.one(f"SELECT {fields} FROM memories WHERE id = ? AND deleted_at IS NULL", (id,))
        elif scope and namespace and key:
            r = self.store.one(
                f"SELECT {fields} FROM memories WHERE scope = ? AND namespace = ? AND key = ? AND deleted_at IS NULL",
                (scope.strip().lower(), namespace.strip("/").lower(), key.lower()),
            )
        else:
            raise MemoryError_("pass id, or scope, namespace and key")
        if r is None:
            raise MemoryError_("memory not found")
        return r

    def read(self, *, id: str | None = None, scope: str | None = None, namespace: str | None = None, key: str | None = None,
             path: MemoryPath | None = None, deleted: bool = False, history_limit: int = 5) -> dict[str, Any]:
        r = self.locate(self.store, id, path, deleted=deleted) if path or id else self._find("*", id, scope, namespace, key)
        out = _row(r)
        out["history"] = self.history(r["id"], history_limit)
        return out

    def history(self, id: str, limit: int) -> list[dict[str, Any]]:
        rows = self.store.q("SELECT rowid AS revision, * FROM memory_revisions WHERE memory_id = ? ORDER BY at DESC, rowid DESC LIMIT ?",
                            (id, max(1, min(limit, 200))))
        return [{"rev": r["revision"], "at": iso(r["at"]), **{k: r[k] for k in ("source", "action", "title", "scope", "namespace", "key", "trust")},
                 "tags": [t for t in (r["tags"] or "").split(",") if t], "excerpt": r["body"][:500]} for r in rows]

    def latest_writers(self, ids: list[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = self.store.q("SELECT id, content_by FROM memories WHERE id IN (" + ",".join("?" for _ in ids) + ")", ids)
        return {r["id"]: r["content_by"] for r in rows}

    def delete(self, *, source: str, id: str | None = None, path: MemoryPath | None = None,
               check_writer: Callable[[str, str | None], None] | None = None) -> dict[str, Any]:
        now = self.store.clock()

        def run(db: sqlite3.Connection) -> str:
            r = self.locate(db, id, path)
            if check_writer is not None:
                check_writer(r["scope"], r["writer_credential"])
            self._delete(db, r["id"], source, now)
            return r["id"]

        mid = self.store.tx(run)
        self.changed()
        return {"deleted": mid}

    def _delete(self, db: sqlite3.Connection, id: str, source: str, now: float, *, by: str | None = None) -> None:
        db.execute("UPDATE memories SET deleted_at = ?, deleted_by = ?, updated_at = ?, source = ? WHERE id = ?",
                   (now, by or source, now, source, id))
        self.revision(db, id, now, source, "delete")

    def move(self, *, source: str, id: str, to_scope: str, check_writer: Callable[[str, str | None], None] | None = None) -> dict[str, Any]:
        """Move one entry to another scope, keeping its id and revision history."""
        to = self.write_scope(to_scope)
        now = self.store.clock()

        def run(db: sqlite3.Connection) -> str:
            r = db.execute("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (id,)).fetchone()
            if r is None:
                raise MemoryNotFound("memory not found")
            if check_writer is not None:
                check_writer(r["scope"], r["writer_credential"])
            if r["scope"] == to:
                raise MemoryError_(f"memory {id} is already in {to}")
            slot = r["key"] or id
            if self._path_row(db, MemoryPath("", to, r["namespace"], slot)):
                raise MemoryError_(f"{to} already has {r['namespace']}/{slot}; merge or delete one of them first")
            db.execute("UPDATE memories SET scope = ?, source = ?, updated_at = ? WHERE id = ?", (to, source, now, id))
            self.revision(db, id, now, source, f"move {r['scope']} -> {to}")
            return r["scope"]

        frm = self.store.tx(run)
        self.changed()
        return {"moved": id, "from": frm, "to": to}

    def view(self, path: MemoryPath, scopes: list[str], view_range: list[int] | None = None) -> str:
        if path.key is not None:
            row = self.locate(self.store, path=path)
            lines = row["body"].split("\n")
            start, end = 1, len(lines)
            if view_range is not None:
                if len(view_range) != 2:
                    raise MemoryError_("Error: Pass view_range as [start, end].")
                start, end = view_range
                if end == -1:
                    end = len(lines)
                if not 1 <= start <= end <= len(lines):
                    raise MemoryError_(f"Error: Use line numbers from 1 to {len(lines)}.")
            entry = _row(row, body=False)
            heading = f"Here's the content of {entry['path']} with line numbers:"
            text = f"{heading}\n{_numbered(lines[start - 1:end], start)}"
            framed = frame_entry(entry, text)
            if len(framed) <= MAX_VIEW:
                return framed
            return frame_entry(entry, text[:MAX_VIEW - len(framed) + len(text) - len(MEMORY_TRUNCATION)]) + MEMORY_TRUNCATION

        prefix = path.normalized
        children: dict[str, int | None] = {}

        def add(full: str, size: int | None) -> None:
            if not full.startswith(prefix + "/"):
                return
            names = full[len(prefix) + 1:].split("/")
            if any(n.startswith(".") or n == "node_modules" for n in names):
                return
            for i in range(1, min(2, len(names)) + 1):
                children[prefix + "/" + "/".join(names[:i])] = size if i == len(names) else None

        for scope in scopes:
            add(f"/memories/{scope_path(scope)}", None)
        where, args = self._filter(scopes, None)
        for row in self.store.q(f"SELECT id, scope, namespace, key, length(CAST(body AS BLOB)) AS size FROM memories m"
                                f" WHERE deleted_at IS NULL{where}", args):
            add(row_path(row), row["size"])
        if not children and prefix != "/memories" and not any(f"/memories/{scope_path(s)}" == prefix for s in scopes):
            raise MemoryNotFound(missing_path(path.path))
        heading = f"Here're the files and directories up to 2 levels deep in {prefix}, excluding hidden items and node_modules:"
        return "\n".join([heading, *(f"{size if size is not None else '4.0K'}\t{name}" for name, size in sorted(children.items()))])

    def edit(self, *, source: str, writer: str | None, trust: str, action: str,
             check_writer: Callable[[str, str | None], None], change: Callable[[sqlite3.Row], tuple[str, str, str]] | None = None,
             id: str | None = None, path: MemoryPath | None = None, rev: int | None = None) -> dict[str, Any]:
        now = self.store.clock()

        def run(db: sqlite3.Connection) -> sqlite3.Row:
            row = self.locate(db, id, path, deleted=rev is not None)
            check_writer(row["scope"], row["writer_credential"])
            restored_source, restored_trust = source, trust
            if rev is not None:
                version = db.execute("SELECT title, body, tags, source, trust FROM memory_revisions WHERE memory_id = ? AND rowid = ?",
                                     (row["id"], rev)).fetchone()
                if version is None:
                    raise MemoryNotFound("Memory revision not found.")
                slot = MemoryPath("", row["scope"], row["namespace"], row["key"] or row["id"])
                if row["deleted_at"] is not None and self._path_row(db, slot):
                    raise MemoryError_(f"Error: The destination {slot.normalized} already exists")
                title, body = version["title"], version["body"]
                tags = version["tags"] if version["tags"] is not None else row["tags"]
                if version["source"].startswith(MEMORY_IMPORT_PREFIX):
                    restored_source = version["source"]
                    restored_trust = min(trust, version["trust"] or "agent", key=MEMORY_TRUST.index)
            else:
                assert change is not None
                title, body, tags = change(row)
            self.validate(row["namespace"], row["key"], title, body)
            self._replace(db, row, source, now, title.strip(), body, tags, writer, restored_trust, content_by=restored_source)
            self.revision(db, row["id"], now, restored_source, action)
            return db.execute("SELECT * FROM memories WHERE id = ?", (row["id"],)).fetchone()

        row = self.store.tx(run)
        self.changed()
        return _row(row)

    def replace_text(self, path: MemoryPath, old_str: str, new_str: str, **actor: Any) -> str:
        first = last = 0

        def change(row: sqlite3.Row) -> tuple[str, str, str]:
            nonlocal first, last
            body = row["body"]
            if not old_str or (index := body.find(old_str)) < 0:
                raise MemoryError_(f"No replacement was performed, old_str `{old_str}` did not appear verbatim in {path.path}.")
            if body.find(old_str, index + 1) >= 0:
                lines = [str(body.count("\n", 0, hit.start()) + 1) for hit in re.finditer(f"(?={re.escape(old_str)})", body)]
                raise MemoryError_(f"No replacement was performed. Multiple occurrences of old_str `{old_str}` in lines: "
                                   f"{', '.join(dict.fromkeys(lines))}. Please ensure it is unique")
            first = body.count("\n", 0, index)
            text = unframe_memory(new_str)
            last = first + text.count("\n")
            return row["title"], body[:index] + text + body[index + len(old_str):], row["tags"]

        entry = self.edit(path=path, action="edit", change=change, **actor)
        start = max(0, first - 4)
        snippet = _numbered(entry["body"].split("\n")[start:last + 5], start + 1)
        return "The memory file has been edited.\n" + frame_entry(entry, snippet)

    def insert(self, path: MemoryPath, insert_line: int, insert_text: str, **actor: Any) -> str:
        def change(row: sqlite3.Row) -> tuple[str, str, str]:
            lines = row["body"].split("\n")
            if not 0 <= insert_line <= len(lines):
                raise MemoryError_(f"Error: Invalid `insert_line` parameter: {insert_line}. "
                                   f"It should be within the range of lines of the file: [0, {len(lines)}]")
            lines.insert(insert_line, unframe_memory(insert_text).removesuffix("\n"))
            return row["title"], "\n".join(lines), row["tags"]

        self.edit(path=path, action="insert", change=change, **actor)
        return f"The file {path.normalized} has been edited."

    def edit_entry(self, id: str, title: str, body: str, tags: list[str] | None, **actor: Any) -> dict[str, Any]:
        return self.edit(id=id, action="edit", **actor,
                         change=lambda row: (title, unframe_memory(body), _tags(tags) if tags is not None else row["tags"]))

    def rename(self, *, source: str, new: MemoryPath, check_writer: Callable[[str, str | None], None],
               check_target: Callable[[str, str], None], old: MemoryPath | None = None, id: str | None = None) -> str:
        now = self.store.clock()
        if not new.scope or not new.namespace or (not id and (not old or not old.scope or not old.namespace)):
            raise MemoryError_("Error: Rename a memory file or topic folder.")
        file = id is not None or (old is not None and old.key is not None)
        if file != (new.key is not None):
            raise MemoryError_("Error: Rename a file to a file, or a folder to a folder.")

        def run(db: sqlite3.Connection) -> tuple[str, bool]:
            if file:
                rows = [self.locate(db, id, old)]
                before = row_path(rows[0])
            else:
                rows = self._topic_rows(db, old)
                if not rows:
                    raise MemoryNotFound(missing_path(old.path))
                before = old.normalized
            for row in rows:
                check_writer(row["scope"], row["writer_credential"])
            check_target(rows[0]["scope"], new.scope)
            self.write_scope(new.scope)
            if before == new.normalized:
                return before, False
            if (self._path_row(db, new) if file else self._topic_rows(db, new)):
                raise MemoryError_(f"Error: The destination {new.path} already exists")
            for row in rows:
                namespace = new.namespace if new.key is not None else new.namespace + row["namespace"][len(old.namespace):]
                key = row["key"] if new.key is None or (row["key"] is None and new.key == row["id"]) else new.key
                self.validate(namespace, key, row["title"], row["body"])
                db.execute("UPDATE memories SET scope = ?, namespace = ?, key = ?, updated_at = ?, source = ? WHERE id = ?",
                           (new.scope, namespace, key, now, source, row["id"]))
                self.revision(db, row["id"], now, source, "rename")
            return before, True

        before, changed = self.store.tx(run)
        if changed:
            self.changed()
        return f"Successfully renamed {before} to {new.normalized}"

    @staticmethod
    def _topic_rows(db: sqlite3.Connection, path: MemoryPath) -> list[sqlite3.Row]:
        return db.execute("SELECT * FROM memories WHERE scope = ? AND deleted_at IS NULL AND"
                          " (namespace = ? OR substr(namespace, 1, ?) = ?) ORDER BY namespace, id",
                          (path.scope, path.namespace, len(path.namespace) + 1, path.namespace + "/")).fetchall()

    def metadata_summary(self, scopes: list[str], limit_chars: int, sync_status: str) -> str:
        where, args = self._filter(scopes, None)
        rows = self.store.q(f"SELECT id, scope, namespace, key, title, updated_at, content_by FROM memories m"
                            f" WHERE deleted_at IS NULL{where} ORDER BY scope, updated_at DESC, rowid DESC", args)
        n_scopes = len({r["scope"] for r in rows})
        tail = f"{len(rows)} {'entry' if len(rows) == 1 else 'entries'} in {n_scopes} {'scope' if n_scopes == 1 else 'scopes'}.\n{sync_status}"
        if len(tail) > limit_chars:
            return sync_status if len(sync_status) <= limit_chars else ""
        groups, lines, shown = None, [], 0
        remaining = max(0, limit_chars - len(tail) - 2)
        for r in rows:
            heading = f"\n{r['scope']}\n" if r["scope"] != groups else ""
            line = f"{heading}- {row_path(r)} | {memory_label(r['title'])} | {iso(r['updated_at'])[:10]} | by {memory_label(r['content_by'])}"
            if len(line) + 1 > remaining:
                break
            lines.append(line)
            remaining -= len(line) + 1
            groups, shown = r["scope"], shown + 1
        note = f"\n{len(rows) - shown} more." if shown < len(rows) and remaining >= len(str(len(rows) - shown)) + 7 else ""
        index = "\n".join(lines).lstrip() + note
        return (f"{index}\n\n" if index else "") + tail

    def sync_item(self, *, source: str, sync_source: str, scope: str, namespace: str, item: dict[str, Any],
                  writer: str | None, check_scope: Callable[[str], bool],
                  check_writer: Callable[[str, str | None], None]) -> dict[str, Any]:
        source_key = item["source_key"]
        result = {"source_key": source_key}
        if not source_key.strip():
            return {**result, "action": "skipped", "reason": "Provide a source key."}
        if not check_scope(scope):
            return {**result, "action": "skipped", "reason": "The target scope is outside this key's grants."}
        try:
            self.write_scope(scope)
        except MemoryError_ as exc:
            return {**result, "action": "skipped", "reason": str(exc)}
        now = self.store.clock()

        def run(db: sqlite3.Connection) -> dict[str, Any]:
            row = db.execute("SELECT * FROM memories WHERE sync_source = ? AND sync_key = ?", (sync_source, source_key)).fetchone()
            if row is not None and not check_scope(row["scope"]):
                return {**result, "action": "skipped", "reason": "The target scope is outside this key's grants."}
            current = {**result, "id": row["id"], "path": row_path(row)} if row is not None else result
            if row is not None and row["deleted_at"] is not None and row["deleted_by"] != sync_source:
                return {**current, "action": "skipped", "reason": "The owner deleted this memory."}
            if row is not None and row["scope"] != scope:
                return {**current, "action": "conflict", "reason": "The memory moved to another scope."}
            deleting = item.get("deleted", False)
            if deleting and (row is None or row["deleted_at"] is not None):
                return {**current, "action": "unchanged"}
            body = unframe_memory(item.get("body", ""))
            digest = memory_hash(body)
            if row is not None:
                if not deleting and row["deleted_at"] is None and digest == row["content_hash"]:
                    return {**current, "action": "unchanged"}
                if row["content_by"] != sync_source:
                    return {**current, "action": "conflict"}
                try:
                    if scope != GENERAL or writer != row["writer_credential"]:
                        check_writer(scope, row["writer_credential"])
                except MemoryRefused:
                    return {**current, "action": "conflict"}
                if deleting:
                    self._delete(db, row["id"], source, now, by=sync_source)
                    return {**current, "action": "deleted"}
            key = (row["key"] or row["id"]) if row is not None else re.sub(r"[^a-z0-9]+", "-", source_key.lower()).strip("-")[:70] or "memory"
            namespace_to_write = row["namespace"] if row is not None else namespace
            title = (item.get("title") or "").strip() or key
            self.validate(namespace_to_write, key, title, body)
            if duplicate := self._duplicate(db, scope, digest, row["id"] if row is not None else ""):
                return {**result, "action": "duplicate", "id": duplicate["id"], "path": row_path(duplicate)}
            tags = _tags(item["tags"]) if item.get("tags") is not None else row["tags"] if row is not None else ""
            if row is not None and row["deleted_at"] is None:
                mid = row["id"]
            else:
                stem, i = key, 0
                while self._path_row(db, MemoryPath("", scope, namespace_to_write, key)):
                    salt = source_key if i == 0 else f"{source_key}:{i}"
                    key = f"{stem[:63]}-{sha256(salt.encode()).hexdigest()[:6]}"
                    i += 1
                mid = self._create(db, source, now, scope, namespace_to_write, key, title, body, tags, writer, "agent",
                                   sync_source=sync_source, sync_key=source_key) if row is None else row["id"]
                if row is not None and key != row["key"]:
                    db.execute("UPDATE memories SET key = ? WHERE id = ?", (key, mid))
            if row is not None:
                self._replace(db, row, source, now, title, body, tags, writer, "agent", content_by=sync_source)
            self.revision(db, mid, now, source, "sync")
            path = MemoryPath("", scope, namespace_to_write, key).normalized
            return {**result, "action": "created" if row is None else "updated", "id": mid, "path": path}

        try:
            out = self.store.tx(run)
        except MemoryError_ as exc:
            return {**result, "action": "skipped", "reason": str(exc)}
        if out["action"] in ("created", "updated", "deleted"):
            self.changed()
        return out

    def list(self, *, scopes: list[str] | None, namespace: str | None = None, limit: int = 50, offset: int = 0,
             summaries: bool = False, since: float | None = None) -> dict[str, Any]:
        where, args = self._filter(scopes, namespace)
        if since is not None:
            where += " AND m.updated_at >= ?"
            args.append(since)
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
               summaries: bool = False, framed: bool = False) -> dict[str, Any]:
        """Full-text search; with several scopes, matches in the first (the project) rank ahead of general."""
        limit = max(1, min(limit, 50))
        where, args = self._filter(scopes, namespace)
        rank, rargs = self._scope_rank(scopes)
        match = fts_query(query)
        if not match:
            raise MemoryError_("query has no searchable words")
        highlight = "'', ''" if framed else "'[', ']'"
        rows = self.store.q(
            f"""SELECT m.*, snippet(memories_fts, 1, {highlight}, ' … ', 24) AS snip
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
        items = []
        for r in rows:
            entry = _row(r, body=False)
            snippet = r["snip"]
            if framed:
                entry["title"] = memory_label(entry["title"])
                snippet = frame_entry(entry, snippet)
            items.append({**entry, "snippet": snippet, **({"summary": summary(r["body"])} if summaries else {})})
        return {"scopes": scopes, "items": items}

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
        return Path(scope_path(scope), namespace, f"{key or id}.md")

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
        self._schedule_mirror()
        for listener in tuple(self.listeners):
            try:
                listener()
            except Exception as exc:
                log.error("Memory change listener failed: %s", type(exc).__name__)

    def _schedule_mirror(self) -> None:
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

    changed = schedule_mirror = _sync_mirror

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
