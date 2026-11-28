"""Portable memory snapshots and owner-only archive imports."""

from __future__ import annotations

import io
import json
import math
import re
import sqlite3
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .audit import iso
from .config import NAMESPACE_RE, SLUG_RE
from .connect import AGENT_RE
from .memory import GENERAL, KEY_RE, Memory, MemoryError_
from .refusals import OWNER_REQUIRED, PolicyRefusal
from .store import memory_hash
from .trust import MEMORY_IMPORT_PREFIX, MEMORY_TRUST

if TYPE_CHECKING:
    from .auth import Principal
    from .core import Switchboard

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
MIME = "application/zip"
FIELDS = ("scope", "namespace", "key", "title", "body", "tags", "created_at", "updated_at",
          "created_by", "content_by", "content_trust", "content_hash", "sync_source", "sync_key")
REV_FIELDS = ("at", "source", "action", "title", "body", "scope", "namespace", "key", "tags", "trust")


def require_owner(principal: Principal) -> None:
    if not principal.is_memory_owner:
        raise PolicyRefusal(OWNER_REQUIRED)


def _tags(value: str | None) -> list[str] | None:
    return [tag for tag in value.split(",") if tag] if value is not None else None


def _path(row: Any) -> str:
    return Memory.rel_path(row["scope"], row["namespace"], row["key"], row["id"]).as_posix()


@dataclass
class Snapshot:
    manifest: dict[str, Any]
    rows: list[sqlite3.Row]
    changes: list[sqlite3.Row]
    last_rev: int

    def files(self) -> dict[str, str]:
        files = {}
        for row, entry in zip(self.rows, self.manifest["entries"]):
            files[entry["path"]] = Memory.to_markdown({**entry, "source": row["source"], "updated_at": iso(row["updated_at"])})
        files["INDEX.md"] = Memory.index_markdown(self.rows)
        files["manifest.json"] = json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n"
        return files


def snapshot(core: Switchboard, after_rev: int | None = None) -> Snapshot:
    def read(db: sqlite3.Connection) -> Snapshot:
        rows = db.execute("SELECT * FROM memories WHERE deleted_at IS NULL ORDER BY scope, namespace, updated_at DESC, id").fetchall()
        revisions = db.execute(
            "SELECT r.rowid AS rev, r.* FROM memory_revisions r JOIN memories m ON m.id = r.memory_id"
            " WHERE m.deleted_at IS NULL ORDER BY r.rowid"
        ).fetchall()
        histories: dict[str, list[dict[str, Any]]] = {}
        for row in revisions:
            histories.setdefault(row["memory_id"], []).append(
                {"rev": row["rev"], **{k: row[k] for k in REV_FIELDS}, "tags": _tags(row["tags"])}
            )
        entries = []
        for row in rows:
            history = histories.get(row["id"], [])
            entries.append({"id": row["id"], **{k: row[k] for k in FIELDS}, "path": _path(row),
                            "tags": _tags(row["tags"]), "created_by": row["created_by"] or (history[0]["source"] if history else row["source"]),
                            "content_by": row["content_by"] or next((r["source"] for r in reversed(history)
                                                                      if r["action"] in ("create", "replace", "append")), row["source"]),
                            "content_hash": memory_hash(row["body"]), "history": history})
        manifest = {"format": "swb-memory", "version": 1, "exported_at": iso(core.clock()),
                    "instance": core.cfg.public_url or "swb", "entries": entries}
        changes = [] if after_rev is None else db.execute(
            "SELECT r.rowid AS rev, r.*, m.scope AS current_scope, m.namespace AS current_namespace, m.key AS current_key, "
            "p.rowid AS previous_rev, p.scope AS previous_scope, p.namespace AS previous_namespace, p.key AS previous_key "
            "FROM memory_revisions r JOIN memories m ON m.id = r.memory_id "
            "LEFT JOIN memory_revisions p ON p.rowid = (SELECT MAX(q.rowid) FROM memory_revisions q "
            "WHERE q.memory_id = r.memory_id AND q.rowid < r.rowid) WHERE r.rowid > ? ORDER BY r.rowid", (after_rev,)
        ).fetchall()
        last_rev = db.execute("SELECT COALESCE(MAX(rowid), 0) FROM memory_revisions").fetchone()[0]
        return Snapshot(manifest, rows, changes, last_rev)

    return core.store.tx(read)


def export_zip(core: Switchboard, principal: Principal) -> tuple[bytes, int, str]:
    require_owner(principal)
    snap = snapshot(core)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, text in sorted(snap.files().items()):
            archive.writestr(zipfile.ZipInfo(name), text.encode(), compress_type=zipfile.ZIP_DEFLATED)
    count = len(snap.rows)
    core.audit_memory(principal, "memory_export", "archive", detail=f"{count} entries")
    return buf.getvalue(), count, datetime.fromtimestamp(core.clock(), timezone.utc).strftime("%Y%m%d")


def _read_manifest(data: bytes | dict[str, Any]) -> dict[str, Any]:
    manifest = data
    if isinstance(data, bytes):
        if len(data) > MAX_ARCHIVE_BYTES:
            raise MemoryError_("Use a memory archive up to 50 MB.")
        try:
            manifest = json.loads(data)
        except (ValueError, RecursionError):
            raise MemoryError_("Use a valid memory manifest.") from None
    if (not isinstance(manifest, dict) or manifest.get("format") != "swb-memory" or type(manifest.get("version")) is not int
            or manifest["version"] != 1 or not isinstance(manifest.get("entries"), list)):
        raise MemoryError_("Use a version 1 SwB memory archive.")
    if len(manifest["entries"]) > MAX_ARCHIVE_ENTRIES:
        raise MemoryError_("Use a smaller memory archive.")
    return manifest


def import_zip(core: Switchboard, principal: Principal, data: bytes, *, trust_owner: bool = False,
               overwrite: bool = False) -> dict[str, Any]:
    require_owner(principal)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise MemoryError_("Use a memory archive up to 50 MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_ENTRIES or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
                raise MemoryError_("Use a smaller memory archive.")
            names = set()
            for member in members:
                name = member.orig_filename
                segments = name.removesuffix("/").split("/")
                if (name in names or any(c in name for c in "\\\x00:") or any(p in ("", ".", "..") for p in segments)):
                    raise MemoryError_("Use safe, unique archive paths.")
                names.add(name)
            if "manifest.json" not in names:
                raise MemoryError_("Include manifest.json in the archive.")
            manifest = archive.read("manifest.json")
    except MemoryError_:
        raise
    except (OSError, RuntimeError, ValueError, EOFError, zipfile.BadZipFile, zlib.error):
        raise MemoryError_("Use a valid memory zip file.") from None
    return import_manifest(core, principal, manifest, trust_owner=trust_owner, overwrite=overwrite)


def _text(value: Any) -> bool:
    if not isinstance(value, str) or "\0" in value:
        return False
    try:
        value.encode()
    except UnicodeError:
        return False
    return True


def _timestamp(value: Any) -> bool:
    return type(value) in (int, float) and -62135596800 <= value < 253402300799 and math.isfinite(value)


def _tag_string(value: Any) -> str:
    if not isinstance(value, list) or not all(_text(tag) and "," not in tag for tag in value):
        raise MemoryError_("Use a list of memory tags.")
    return ",".join(value)


def _entry(memory: Memory, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or any(not _text(raw.get(k)) for k in ("id", "scope", "namespace", "title", "body")):
        raise MemoryError_("Include the required memory fields.")
    scope = raw["scope"]
    if scope != GENERAL and not (scope.startswith("project:") and SLUG_RE.fullmatch(scope[8:])
                                 or scope.startswith("agent:") and AGENT_RE.fullmatch(scope[6:])):
        raise MemoryError_("Use a valid memory scope.")
    if not ID_RE.fullmatch(raw["id"]) or not NAMESPACE_RE.fullmatch(raw["namespace"]):
        raise MemoryError_("Use a valid memory ID and namespace.")
    if raw.get("key") is not None and (not _text(raw["key"]) or not KEY_RE.fullmatch(raw["key"])):
        raise MemoryError_("Use a valid memory key.")
    memory.validate(raw["namespace"], raw.get("key"), raw["title"], raw["body"])
    if not all(_timestamp(raw.get(k)) for k in ("created_at", "updated_at")):
        raise MemoryError_("Use valid memory timestamps.")
    if raw.get("content_trust") not in MEMORY_TRUST or any(
        raw.get(k) is not None and not _text(raw[k]) for k in ("created_by", "content_by", "sync_source", "sync_key")
    ) or (raw.get("sync_source") is None) != (raw.get("sync_key") is None):
        raise MemoryError_("Use valid memory provenance.")
    entry = {**raw, **{k: raw.get(k) for k in FIELDS}, "tags": _tag_string(raw.get("tags")),
             "content_hash": memory_hash(raw["body"])}
    entry["path"] = _path(entry)
    if raw.get("path") != entry["path"]:
        raise MemoryError_("Match the archive path to the memory fields.")
    history = raw.get("history")
    if not isinstance(history, list):
        raise MemoryError_("Include a memory history list.")
    revisions = []
    seen = set()
    for r in history:
        if (not isinstance(r, dict) or type(rev := r.get("rev")) is not int or not 0 < rev < 2**53 or rev in seen
                or not _timestamp(r.get("at")) or not all(_text(r.get(k)) for k in ("source", "action", "title", "body"))
                or any(r.get(k) is not None and not _text(r[k]) for k in ("scope", "namespace", "key"))
                or r.get("trust") not in (None, *MEMORY_TRUST)):
            raise MemoryError_("Use valid memory revisions.")
        seen.add(rev)
        revisions.append({"rev": rev, **{k: r.get(k) for k in REV_FIELDS},
                          "tags": _tag_string(r["tags"]) if r.get("tags") is not None else None})
    entry["history"] = sorted(revisions, key=lambda r: r["rev"])
    return entry


def _revision(db: sqlite3.Connection, mid: str, revision: dict[str, Any], rev: int | None = None) -> int:
    return db.execute(
        "INSERT INTO memory_revisions(rowid, memory_id, at, source, action, title, body, scope, namespace, key, tags, trust)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (rev, mid, *(revision[k] for k in REV_FIELDS))
    ).lastrowid


def import_manifest(core: Switchboard, principal: Principal, data: bytes | dict[str, Any], *,
                    trust_owner: bool = False, overwrite: bool = False, origin: str = "file",
                    external: bool = False) -> dict[str, Any]:
    require_owner(principal)
    manifest = _read_manifest(data)
    now = core.clock()
    counts = dict.fromkeys(("created", "updated", "unchanged", "conflict", "skipped"), 0)
    problems = []
    entries = []
    seen = set()
    action = f"imported from {origin}"
    importer = f"{MEMORY_IMPORT_PREFIX}{principal.source}"

    def problem(action: str, mid: str | None, reason: str) -> None:
        counts[action] += 1
        problems.append({"id": mid, "action": action, "reason": reason})

    for raw in manifest["entries"]:
        ident = raw.get("id") if isinstance(raw, dict) else None
        mid = ident if isinstance(ident, str) and ID_RE.fullmatch(ident) else None
        try:
            entry = _entry(core.memory, raw)
        except MemoryError_ as e:
            problem("skipped", mid, str(e))
            continue
        if mid in seen:
            problem("conflict", mid, "Use each memory ID once.")
            continue
        seen.add(mid)
        other_agent = entry["scope"].startswith("agent:") and entry["scope"] != f"agent:{principal.source}"
        ceiling = "external" if external else "owner" if trust_owner and not other_agent else "agent"
        entry.update(created_by=principal.source, content_by=importer,
                     content_trust=min(entry["content_trust"], ceiling, key=MEMORY_TRUST.index),
                     sync_source=None, sync_key=None)
        for revision in entry["history"]:
            revision.update(source=importer, action=action,
                            trust=min(revision["trust"] or "agent", ceiling, key=MEMORY_TRUST.index))
        entries.append(entry)

    def write(db: sqlite3.Connection) -> None:
        restored_history = []
        changes = []
        for entry in entries:
            mid, scope = entry["id"], entry["scope"]
            old = db.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
            try:
                core.memory.write_scope(scope)
                core.check_memory_scope(principal, scope)
                if old:
                    if old["scope"] != scope:
                        core.memory.write_scope(old["scope"])
                    core.check_memory_writer(principal, old["scope"], old["writer_credential"])
            except MemoryError_ as e:
                problem("skipped", mid, str(e))
                continue
            if old and memory_hash(old["body"]) == entry["content_hash"]:
                counts["unchanged"] += 1
                continue
            if old and not overwrite:
                problem("conflict", mid, "Use overwrite to replace an existing memory")
                continue
            if db.execute(
                "SELECT 1 FROM memories WHERE id != ? AND scope = ? AND namespace = ? AND COALESCE(key, id) = ? AND deleted_at IS NULL",
                (mid, scope, entry["namespace"], entry["key"] or mid)
            ).fetchone():
                problem("conflict", mid, "The memory path already exists.")
                continue
            if old:
                revision = db.execute("SELECT * FROM memory_revisions WHERE memory_id = ? ORDER BY rowid DESC LIMIT 1", (mid,)).fetchone()
                if revision is None or old["content_trust"] != revision["trust"] or any(
                    old[k] != revision[k] for k in ("title", "body", "tags", "scope", "namespace", "key")
                ):
                    changes.append((mid, {**dict(old), "at": old["updated_at"], "source": old["content_by"] or old["source"],
                                          "action": "import snapshot", "trust": old["content_trust"]}))
                entry["updated_at"] = now
                db.execute(
                    "UPDATE memories SET " + ", ".join(f"{k} = ?" for k in FIELDS if k not in ("created_at", "created_by"))
                    + ", source = ?, writer_credential = NULL, deleted_at = NULL, deleted_by = NULL WHERE id = ?",
                    (*(entry[k] for k in FIELDS if k not in ("created_at", "created_by")), principal.source, mid)
                )
                counts["updated"] += 1
            else:
                db.execute(
                    "INSERT INTO memories(id, source, " + ", ".join(FIELDS) + ") VALUES (" + ", ".join("?" for _ in range(len(FIELDS) + 2)) + ")",
                    (mid, principal.source, *(entry[k] for k in FIELDS))
                )
                restored_history.append((mid, entry["history"]))
                counts["created"] += 1
            changes.append((mid, {**entry, "at": now, "source": importer, "action": action, "trust": entry["content_trust"]}))

        last_rev = db.execute("SELECT COALESCE(MAX(rowid), 0) FROM memory_revisions").fetchone()[0]
        imported = sorted(
            ((r["rev"], mid, r) for mid, history in restored_history for r in history), key=lambda item: (item[0], item[1])
        )
        for rev, mid, revision in imported:
            _revision(db, mid, revision, rev if rev > last_rev else None)
            last_rev = max(rev, last_rev + 1)
        for mid, revision in changes:
            _revision(db, mid, revision)

    core.store.tx(write)
    if counts["created"] or counts["updated"]:
        core.memory.schedule_mirror()
    core.audit_memory(principal, "memory_import", "archive", detail=", ".join(f"{k} {v}" for k, v in counts.items()))
    return {**counts, "problems": problems}
