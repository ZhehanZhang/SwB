from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import NAMESPACE_RE
from .store import Store, fts_query, new_id

MAX_BODY = 20_000
MAX_TITLE = 200
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class MemoryError_(ValueError):
    pass


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _row(r: sqlite3.Row, body: bool = True, limit: int | None = None) -> dict[str, Any]:
    out = {
        "id": r["id"],
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
        if limit and len(text) > limit:
            text = text[:limit] + " …"
        out["body"] = text
    return out


def _ns_filter(namespace: str | None) -> tuple[str, list[Any]]:
    if not namespace:
        return "", []
    ns = namespace.strip().strip("/")
    return " AND (m.namespace = ? OR m.namespace LIKE ?)", [ns, ns + "/%"]


class Memory:
    def __init__(self, store: Store, mirror_dir: str | None = None):
        self.store = store
        self.mirror_dir = Path(mirror_dir) if mirror_dir else None

    def validate(self, namespace: str, key: str | None, title: str, body: str) -> None:
        if not NAMESPACE_RE.match(namespace or ""):
            raise MemoryError_("namespace must be lowercase segments like 'profile', 'decisions' or 'projects/capy-bridge' (max 3 levels)")
        if key is not None and not KEY_RE.match(key):
            raise MemoryError_("key must be a lowercase slug (a-z, 0-9, . _ -), max 80 chars")
        if not title.strip() or len(title) > MAX_TITLE:
            raise MemoryError_(f"title is required, max {MAX_TITLE} chars")
        if len(body) > MAX_BODY:
            raise MemoryError_(f"body is limited to {MAX_BODY} chars; split it into several entries")

    def write(
        self,
        *,
        source: str,
        namespace: str,
        title: str,
        body: str,
        key: str | None = None,
        tags: list[str] | None = None,
        id: str | None = None,
        mode: str = "replace",
    ) -> dict[str, Any]:
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
            elif key:
                existing = db.execute(
                    "SELECT * FROM memories WHERE namespace = ? AND key = ? AND deleted_at IS NULL", (namespace, key)
                ).fetchone()
            if existing is None:
                self.validate(namespace, key, title, body)
                mid = new_id("mem")
                db.execute(
                    "INSERT INTO memories(id, namespace, key, title, body, tags, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (mid, namespace, key, title.strip(), body, tag_s, source, now, now),
                )
                action = "create"
            else:
                mid = existing["id"]
                ns = existing["namespace"] if id else namespace
                new_body = (existing["body"].rstrip() + "\n\n" + body) if mode == "append" else body
                new_title = title.strip() or existing["title"]
                self.validate(ns, existing["key"], new_title, new_body)
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
        return {"action": action, **{k: entry[k] for k in ("id", "namespace", "key", "title", "updated_at", "source")}}

    def read(self, *, id: str | None = None, namespace: str | None = None, key: str | None = None) -> dict[str, Any]:
        if id:
            r = self.store.one("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (id,))
        elif namespace and key:
            r = self.store.one(
                "SELECT * FROM memories WHERE namespace = ? AND key = ? AND deleted_at IS NULL",
                (namespace.strip("/").lower(), key.lower()),
            )
        else:
            raise MemoryError_("pass id, or namespace and key")
        if r is None:
            raise MemoryError_("memory not found")
        out = _row(r)
        revs = self.store.q(
            "SELECT at, source, action FROM memory_revisions WHERE memory_id = ? ORDER BY at DESC LIMIT 5", (r["id"],)
        )
        out["history"] = [{"at": iso(v["at"]), "source": v["source"], "action": v["action"]} for v in revs]
        return out

    def delete(self, *, source: str, id: str) -> dict[str, Any]:
        now = self.store.clock()

        def run(db: sqlite3.Connection) -> None:
            r = db.execute("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (id,)).fetchone()
            if r is None:
                raise MemoryError_("memory not found")
            db.execute("UPDATE memories SET deleted_at = ?, updated_at = ?, source = ? WHERE id = ?", (now, now, source, id))
            db.execute(
                "INSERT INTO memory_revisions(memory_id, at, source, action, title, body) VALUES (?,?,?,?,?,?)",
                (id, now, source, "delete", r["title"], r["body"]),
            )

        self.store.tx(run)
        self._sync_mirror()
        return {"deleted": id}

    def list(self, *, namespace: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        where, args = _ns_filter(namespace)
        limit = max(1, min(limit, 200))
        rows = self.store.q(
            f"SELECT * FROM memories m WHERE m.deleted_at IS NULL{where} ORDER BY m.updated_at DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        )
        counts = self.store.q(
            f"SELECT m.namespace AS ns, COUNT(*) AS n FROM memories m WHERE m.deleted_at IS NULL{where} GROUP BY m.namespace ORDER BY m.namespace",
            args,
        )
        return {
            "namespaces": {c["ns"]: c["n"] for c in counts},
            "items": [_row(r, body=False) for r in rows],
            "next_offset": offset + limit if len(rows) == limit else None,
        }

    def search(self, *, query: str, namespace: str | None = None, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(limit, 50))
        where, args = _ns_filter(namespace)
        match = fts_query(query)
        if not match:
            raise MemoryError_("query has no searchable words")
        rows = self.store.q(
            f"""SELECT m.*, snippet(memories_fts, 1, '[', ']', ' … ', 24) AS snip, bm25(memories_fts, 4.0, 1.0, 2.0, 1.0) AS rank
                FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid
                WHERE memories_fts MATCH ? AND m.deleted_at IS NULL{where}
                ORDER BY rank LIMIT ?""",
            [match, *args, limit],
        )
        if not rows:
            words = re.findall(r"\w+", query.lower())[:6]
            if words:
                cond = " AND ".join(["lower(m.title || ' ' || m.body || ' ' || m.tags) LIKE ?"] * len(words))
                rows = self.store.q(
                    f"SELECT m.*, substr(m.body, 1, 200) AS snip, 0 AS rank FROM memories m WHERE m.deleted_at IS NULL{where} AND ({cond}) ORDER BY m.updated_at DESC LIMIT ?",
                    [*args, *[f"%{w}%" for w in words], limit],
                )
        return {"items": [{**_row(r, body=False), "snippet": r["snip"]} for r in rows]}

    def recent(self, *, namespace: str | None = None, since: float | None = None, limit: int = 10, body_limit: int = 600) -> list[dict[str, Any]]:
        where, args = _ns_filter(namespace)
        if since:
            where += " AND m.updated_at >= ?"
            args.append(since)
        rows = self.store.q(
            f"SELECT * FROM memories m WHERE m.deleted_at IS NULL{where} ORDER BY m.updated_at DESC LIMIT ?", [*args, limit]
        )
        return [_row(r, limit=body_limit) for r in rows]

    # Markdown export / mirror -------------------------------------------------

    @staticmethod
    def rel_path(namespace: str, key: str | None, id: str) -> Path:
        return Path(*namespace.split("/")) / f"{key or id}.md"

    @staticmethod
    def to_markdown(e: dict[str, Any]) -> str:
        front = [
            "---",
            f"id: {e['id']}",
            f"namespace: {e['namespace']}",
            *( [f"key: {e['key']}"] if e.get("key") else [] ),
            f"source: {e['source']}",
            f"updated: {e['updated_at']}",
            *( [f"tags: [{', '.join(e['tags'])}]"] if e.get("tags") else [] ),
            "---",
            "",
            f"# {e['title']}",
            "",
        ]
        return "\n".join(front) + "\n" + e["body"].rstrip() + "\n"

    def export(self, root: str | Path) -> int:
        root = Path(root)
        rows = self.store.q("SELECT * FROM memories WHERE deleted_at IS NULL ORDER BY namespace, updated_at DESC")
        wanted = set()
        for r in rows:
            e = _row(r)
            rel = self.rel_path(e["namespace"], e["key"], e["id"])
            wanted.add(rel)
            _atomic_write(root / rel, self.to_markdown(e))
        for p in root.rglob("*.md") if root.exists() else []:
            rel = p.relative_to(root)
            if rel != Path("INDEX.md") and rel not in wanted:
                p.unlink()
        _atomic_write(root / "INDEX.md", self.index_markdown(rows))
        return len(rows)

    @staticmethod
    def index_markdown(rows: list[sqlite3.Row]) -> str:
        lines = ["# Shared memory (capy-bridge export)", "", "Generated file: edits here are not read back. Write through the memory_* tools.", ""]
        current = None
        for r in rows:
            if r["namespace"] != current:
                current = r["namespace"]
                lines += ["", f"## {current}", ""]
            rel = Memory.rel_path(r["namespace"], r["key"], r["id"]).as_posix()
            lines.append(f"- [{r['title']}]({rel}) · {r['source']} · {iso(r['updated_at'])}")
        return "\n".join(lines) + "\n"

    def _sync_mirror(self) -> None:
        if self.mirror_dir:
            self.export(self.mirror_dir)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == text:
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
