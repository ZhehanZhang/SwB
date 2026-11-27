import time

import pytest

from switchboard.memory import Memory, MemoryError_
from switchboard.projects import ProjectError, Projects
from switchboard.store import Store
from conftest import STATIC_KEY, Clock, call, error_text, payload, sign_in


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "m.db", clock=Clock())


@pytest.fixture
def mem(store):
    projects = Projects(store)
    projects.create(slug="switchboard", name="Switchboard")
    projects.create(slug="notifier", name="notifier")
    return Memory(store, projects)


def test_crud_and_key_upsert(mem):
    a = mem.write(source="claude", scope="general", confirm_general=True, namespace="profile", key="editor", title="Editor", body="Uses VS Code", tags=["tools"])
    assert a["action"] == "create" and a["scope"] == "general"
    b = mem.write(source="chatgpt", scope="general", confirm_general=True, namespace="profile", key="editor", title="Editor", body="Uses Zed now")
    assert b["id"] == a["id"] and b["action"] == "replace" and b["source"] == "chatgpt"
    mem.write(source="capy", scope="general", confirm_general=True, id=a["id"], namespace="ignored", title="", body="Also vim", mode="append")
    e = mem.read(scope="general", namespace="profile", key="editor")
    assert e["body"] == "Uses Zed now\n\nAlso vim" and e["tags"] == ["tools"] and e["title"] == "Editor"
    assert [h["source"] for h in e["history"]] == ["capy", "chatgpt", "claude"]
    mem.delete(source="claude", id=a["id"])
    with pytest.raises(MemoryError_):
        mem.read(id=a["id"])


def test_write_without_scope_is_rejected(mem):
    for scope in (None, "", "  "):
        with pytest.raises(MemoryError_, match="scope is required"):
            mem.write(source="t", scope=scope, namespace="notes", title="x", body="y")
    with pytest.raises(MemoryError_):
        mem.write(source="t", scope="project:nope", namespace="notes", title="x", body="y")
    with pytest.raises(MemoryError_):
        mem.write(source="t", scope="team", namespace="notes", title="x", body="y")


def test_scope_isolation(mem):
    mem.write(source="claude", scope="project:switchboard", namespace="notes", key="port", title="Port", body="8870 on example-host")
    mem.write(source="claude", scope="project:notifier", namespace="notes", key="port", title="Port", body="socket mode, no port")
    a = mem.read(scope="project:switchboard", namespace="notes", key="port")
    b = mem.read(scope="project:notifier", namespace="notes", key="port")
    assert a["id"] != b["id"] and "8870" in a["body"]
    only = mem.search(query="port", scopes=mem.read_scopes("notifier", include_general=False))["items"]
    assert [h["scope"] for h in only] == ["project:notifier"]
    assert mem.search(query="8870", scopes=mem.read_scopes(None))["items"] == []


def test_project_and_general_merge_ranks_project_first(mem):
    mem.write(source="claude", scope="general", confirm_general=True, namespace="decisions", title="Deploy style", body="Docker compose behind a tunnel")
    mem.write(source="claude", scope="project:switchboard", namespace="decisions", title="Deploy target", body="example-host behind a cloudflared tunnel")
    mem.write(source="claude", scope="project:notifier", namespace="decisions", title="Deploy notifier", body="tunnel not needed")
    hits = mem.search(query="tunnel", scopes=mem.read_scopes("switchboard"))["items"]
    assert [h["scope"] for h in hits] == ["project:switchboard", "general"]
    listed = mem.list(scopes=mem.read_scopes("switchboard"))
    assert {e["scope"] for e in listed["items"]} == {"project:switchboard", "general"}


def test_existing_rows_migrate_to_general(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE memories (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, key TEXT, title TEXT NOT NULL, body TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '', source TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, deleted_at REAL);
        CREATE UNIQUE INDEX memories_ns_key ON memories(namespace, key) WHERE key IS NOT NULL AND deleted_at IS NULL;
        INSERT INTO memories VALUES ('mem_1', 'profile', 'tz', 'Timezone', 'New York', '', 'claude', 1, 1, NULL);""")
    db.commit()
    db.close()
    store = Store(path, clock=Clock())
    store.x("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")
    mem = Memory(store, Projects(store))
    assert mem.read(id="mem_1")["scope"] == "general"
    assert mem.search(query="new york", scopes=["general"])["items"][0]["id"] == "mem_1"


def test_projects_rename_moves_memories_and_archive_blocks_writes(mem, store):
    mem.write(source="claude", scope="project:switchboard", namespace="notes", title="t", body="b")
    p = Projects(store).update("switchboard", new_slug="sb", links={"repos": ["example-org/switchboard"]})
    assert p.slug == "sb" and p.links["repos"] == ["example-org/switchboard"]
    assert mem.list(scopes=["project:sb"])["items"]
    Projects(store).update("sb", archived=True)
    with pytest.raises(MemoryError_, match="archived"):
        mem.write(source="t", scope="project:sb", namespace="notes", title="x", body="y")
    with pytest.raises(ProjectError):
        Projects(store).create(slug="general", name="nope")


def test_markdown_export_is_one_folder_per_scope(tmp_path, store):
    out = tmp_path / "mirror"
    projects = Projects(store)
    projects.create(slug="switchboard", name="Switchboard")
    mem = Memory(store, projects, mirror_dir=str(out))
    a = mem.write(source="claude", scope="project:switchboard", namespace="notes", key="auth", title="Auth", body="PKCE", tags=["oauth"])
    mem.write(source="claude", scope="general", confirm_general=True, namespace="profile", key="tz", title="TZ", body="NY")
    md = (out / "projects" / "switchboard" / "notes" / "auth.md").read_text()
    assert md.startswith("---\nid: ") and "scope: project:switchboard" in md and "# Auth\n\nPKCE" in md
    assert (out / "general" / "profile" / "tz.md").exists()
    mem.delete(source="claude", id=a["id"])
    assert not (out / "projects" / "switchboard" / "notes" / "auth.md").exists()


def test_memory_and_project_tools_over_mcp(make):
    _, c = make()
    tok = sign_in(c)["access_token"]
    assert "scope is required" in error_text(call(c, tok, "memory_write", {"scope": "", "namespace": "decisions", "title": "x", "body": "y"}))
    payload(call(c, tok, "project_create", {"slug": "switchboard", "name": "Switchboard", "links": {"repos": ["example-org/switchboard"], "claude_project": "Switchboard"}}))
    w = payload(call(c, tok, "memory_write", {"scope": "project:switchboard", "namespace": "decisions", "title": "Use SQLite", "body": "FTS5 for search"}))
    r = payload(call(c, STATIC_KEY, "memory_read", {"id": w["id"]}))
    assert r["source"] == "claude" and r["scope"] == "project:switchboard"
    assert payload(call(c, STATIC_KEY, "memory_search", {"query": "fts5"}))["items"] == []
    assert payload(call(c, STATIC_KEY, "memory_search", {"query": "fts5", "project": "switchboard"}))["items"][0]["id"] == w["id"]
    assert payload(call(c, STATIC_KEY, "project_list", {"hint": "Switchboard"}))["matches"][0]["slug"] == "switchboard"
    assert payload(call(c, STATIC_KEY, "project_update", {"slug": "switchboard", "archived": True}))["archived"] is True


def test_memory_write_reports_field_names_without_echoing_arguments(make):
    _, client = make()
    args = {"scope": "general", "title": "A note", "body": "private sample text"}
    assert error_text(call(client, STATIC_KEY, "memory_write", args)) == "Provide namespace."
    assert error_text(call(client, STATIC_KEY, "memory_write", {**args, "namespace": ["private sample text"]})) == "Check namespace."


def test_mirror_debounces_bursts_and_follows_renames(tmp_path, store):
    out = tmp_path / "mirror"
    projects = Projects(store)
    projects.create(slug="old", name="Old")
    mem = Memory(store, projects, mirror_dir=str(out), mirror_debounce_s=0.2)
    assert (out / "INDEX.md").exists()
    exports = []
    real = mem.export
    mem.export = lambda root: exports.append(root) or real(root)
    for i in range(5):
        mem.write(source="t", scope="project:old", namespace="notes", key=f"n{i}", title=f"N{i}", body="x")
    assert exports == [] and mem.mirror_pending
    time.sleep(0.6)
    assert len(exports) == 1 and not mem.mirror_pending
    assert len(list((out / "projects" / "old" / "notes").glob("*.md"))) == 5
    projects.update("old", new_slug="new")
    mem.schedule_mirror()
    mem.flush_mirror()
    assert (out / "projects" / "new" / "notes" / "n0.md").exists()
    assert not (out / "projects" / "old").exists()
    assert not list(out.rglob(".tmp-*"))


def test_mirror_can_be_switched_off(tmp_path):
    from switchboard.config import load_config
    path = tmp_path / "c.yaml"
    path.write_text("public_url: https://sb.example.test\nmemory:\n  mirror_dir: /mirror\n  mirror: false\n")
    assert load_config(path, env={}).mirror_dir is None
    path.write_text("public_url: https://sb.example.test\nmemory:\n  mirror_dir: /mirror\n")
    assert load_config(path, env={}).mirror_dir == "/mirror"
