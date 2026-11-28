import sqlite3

from conftest import Clock, base_config
from switchboard.config import INSTANCE_DB
from switchboard.store import Store, memory_hash
from switchboard.trust import unframe_memory
from test_memory_files import mcp

RELEASE_MEMORY = """
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'general',
  namespace TEXT NOT NULL,
  key TEXT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  deleted_at REAL
);
CREATE INDEX IF NOT EXISTS memories_updated ON memories(updated_at);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(title, body, tags, namespace, content='memories', content_rowid='rowid', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, title, body, tags, namespace) VALUES (new.rowid, new.title, new.body, new.tags, new.namespace);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, title, body, tags, namespace) VALUES ('delete', old.rowid, old.title, old.body, old.tags, old.namespace);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, title, body, tags, namespace) VALUES ('delete', old.rowid, old.title, old.body, old.tags, old.namespace);
  INSERT INTO memories_fts(rowid, title, body, tags, namespace) VALUES (new.rowid, new.title, new.body, new.tags, new.namespace);
END;
CREATE TABLE IF NOT EXISTS memory_revisions (
  memory_id TEXT NOT NULL,
  at REAL NOT NULL,
  source TEXT NOT NULL,
  action TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_revisions_id ON memory_revisions(memory_id, at);
ALTER TABLE memories ADD COLUMN writer_credential TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS memories_scope_key ON memories(scope, namespace, key) WHERE key IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope, updated_at);
"""


def release_database(path):
    initial = 1_795_664_019
    slots = [
        *(("project:context", "decisions", key) for key in ("ownership", "switchboard", "canvas-mcp", "routing", "testing", "releases")),
        *(("project:context", "notes/state", key) for key in ("launch", "e2e", "queue", "build", "docs", "progress")),
        *(("general", "profile", key) for key in ("timezone", "editor", "owner")),
        ("agent:notes-bot", "notes", "private"),
        ("project:context", "notes/state", None),
        ("project:context", "decisions", "retired"),
    ]
    with sqlite3.connect(path) as db:
        db.executescript(RELEASE_MEMORY)
        for i, (scope, namespace, key) in enumerate(slots):
            ident = "mem_1795664019333a3f6da94" if not i else f"mem_1795664019{i:03d}{i:08x}"
            title, body = f"Canary {key or 'unkeyed'}", f"Canary fact {key or 'unkeyed'}\nEntry {i}."
            created, updated = initial + i, initial + i + 100
            writer = "legacy-notes-writer-fixture" if scope == "agent:notes-bot" else None
            deleted = updated if key == "retired" else None
            source = "codex" if key == "ownership" else "admin:local" if deleted else "claude"
            db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (ident, scope, namespace, key, title, body, "canary,legacy", source, created, updated, deleted, writer))
            if key != "progress":
                db.execute("INSERT INTO memory_revisions VALUES (?,?,?,?,?,?)", (ident, created, "claude", "create", title, body))
            if key == "ownership":
                db.execute("INSERT INTO memory_revisions VALUES (?,?,?,?,?,?)",
                           (ident, updated - 1, "chatgpt", "replace", title, body))
                db.execute("INSERT INTO memory_revisions VALUES (?,?,?,?,?,?)",
                           (ident, updated, source, "move general -> project:context", title, body))
            if deleted:
                db.execute("INSERT INTO memory_revisions VALUES (?,?,?,?,?,?)", (ident, updated, source, "delete", title, body))
        old = db.execute("SELECT rowid, * FROM memories ORDER BY rowid").fetchall()
        revisions = db.execute("SELECT rowid, * FROM memory_revisions ORDER BY rowid").fetchall()
        assert len(db.execute("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'canary'").fetchall()) == 18
    return old, revisions


def test_release_memory_migration_preserves_every_row_and_is_idempotent(tmp_path, make):
    path = tmp_path / "data" / INSTANCE_DB
    path.parent.mkdir()
    old, revisions = release_database(path)
    store = Store(path)
    rows = store.q("SELECT rowid, * FROM memories ORDER BY rowid")
    histories = store.q("SELECT rowid, * FROM memory_revisions ORDER BY rowid")
    assert len(rows) == 18
    assert [tuple(row)[:13] for row in rows] == old
    assert [tuple(row)[:7] for row in histories] == revisions
    assert all(r["rev"] == r["rowid"] for r in histories)
    assert all(r["created_by"] == "claude" and r["content_trust"] == "agent" for r in rows)
    assert all(r["content_hash"] == memory_hash(r["body"]) and r["sync_source"] is None and r["sync_key"] is None for r in rows)
    assert {r["key"]: r["deleted_by"] for r in rows if r["deleted_by"]} == {"retired": "admin:local"}
    assert rows[0]["content_by"] == "chatgpt" and rows[0]["source"] == "codex"
    assert next(r for r in rows if r["key"] == "progress")["content_by"] == "claude"
    assert len(store.q("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'canary'")) == 18
    trigger = store.one("SELECT sql FROM sqlite_master WHERE name = 'memories_au'")["sql"]
    assert "AFTER UPDATE OF title, body, tags, namespace" in trigger
    store.close()
    reopened = Store(path)
    assert reopened.db.total_changes == 0
    assert reopened.q("SELECT rowid, * FROM memories ORDER BY rowid") == rows
    assert reopened.q("SELECT rowid, * FROM memory_revisions ORDER BY rowid") == histories
    reopened.close()

    core, client = make(base_config(version=2, blocks={"mcp": {}}), clock=Clock(1_796_000_000))
    core.projects.create(slug="context", name="Context")
    old_file = "/memories/projects/context/decisions/ownership.md"
    [hit] = mcp(client, "memory_search", query="ownership", project="context")["items"]
    assert hit["id"] == rows[0]["id"] and hit["path"] == old_file and "Canary fact ownership" in hit["snippet"]
    assert "Canary fact ownership" in mcp(client, "memory_view", path=old_file)
    history = mcp(client, "memory_history", path=old_file)["history"]
    assert [rev["action"] for rev in history] == ["move general -> project:context", "replace", "create"]
    mcp(client, "memory_insert", path=old_file, insert_line=0, insert_text="Fresh line")
    assert unframe_memory(mcp(client, "memory_read", path=old_file)["body"]).startswith("Fresh line\n")
    changed = "/memories/projects/context/notes/state/ownership.md"
    mcp(client, "memory_rename", old_path=old_file, new_path=changed)
    mcp(client, "memory_restore", path=changed, rev=history[-1]["rev"])
    after = mcp(client, "memory_read", path=changed)
    assert after["id"] == rows[0]["id"] and after["tags"] == ["canary", "legacy"]
    assert unframe_memory(after["body"]) == rows[0]["body"]
    keyless = next(r for r in rows if r["key"] is None)
    assert mcp(client, "memory_read", path=f"/memories/projects/context/notes/state/{keyless['id']}.md")["id"] == keyless["id"]
    retired = "/memories/projects/context/decisions/retired.md"
    rev = mcp(client, "memory_history", path=retired)["history"][-1]["rev"]
    assert mcp(client, "memory_restore", path=retired, rev=rev)["id"] == rows[-1]["id"]
    assert len(core.store.q("SELECT id FROM memories WHERE deleted_at IS NULL")) == 18
