import pytest

from capy_bridge.memory import Memory, MemoryError_
from capy_bridge.store import Store
from conftest import STATIC_KEY, call, payload, sign_in, Clock


@pytest.fixture
def mem(tmp_path):
    return Memory(Store(tmp_path / "m.db", clock=Clock()))


def test_crud_and_key_upsert(mem):
    a = mem.write(source="claude", namespace="profile", key="editor", title="Editor", body="Uses VS Code", tags=["tools"])
    assert a["action"] == "create"
    b = mem.write(source="chatgpt", namespace="profile", key="editor", title="Editor", body="Uses Zed now")
    assert b["id"] == a["id"] and b["action"] == "replace" and b["source"] == "chatgpt"
    mem.write(source="capy", id=a["id"], namespace="ignored", title="", body="Also vim", mode="append")
    e = mem.read(namespace="profile", key="editor")
    assert e["body"] == "Uses Zed now\n\nAlso vim" and e["tags"] == ["tools"] and e["title"] == "Editor"
    assert [h["source"] for h in e["history"]] == ["capy", "chatgpt", "claude"]
    mem.delete(source="claude", id=a["id"])
    with pytest.raises(MemoryError_):
        mem.read(id=a["id"])
    again = mem.write(source="claude", namespace="profile", key="editor", title="Editor", body="fresh")
    assert again["action"] == "create" and again["id"] != a["id"]


def test_validation(mem):
    assert mem.write(source="t", namespace="/Profile/", title="x", body="y")["namespace"] == "profile"
    for ns in ("a/b/c/d", "", "../x", "with space", "projects//x"):
        with pytest.raises(MemoryError_):
            mem.write(source="t", namespace=ns, title="x", body="y")
    with pytest.raises(MemoryError_):
        mem.write(source="t", namespace="notes", key="Bad Key", title="x", body="y")
    with pytest.raises(MemoryError_):
        mem.write(source="t", namespace="notes", title="x", body="y" * 20001)
    with pytest.raises(MemoryError_):
        mem.write(source="t", namespace="notes", id="mem_nope", title="x", body="y")


def test_search_and_list(mem):
    mem.write(source="claude", namespace="decisions", title="Deploy target", body="capy-bridge runs on your-host behind a cloudflared tunnel")
    mem.write(source="chatgpt", namespace="projects/capy-bridge", title="Auth", body="OAuth 2.1 with PKCE and DCR")
    mem.write(source="capy", namespace="projects/slackgate", title="Socket mode", body="slackgate uses Socket Mode")
    hits = mem.search(query="tunnel your-host")["items"]
    assert [h["title"] for h in hits] == ["Deploy target"] and "[" in hits[0]["snippet"]
    assert mem.search(query="deploy", namespace="projects")["items"] == []
    assert [h["title"] for h in mem.search(query="mode", namespace="projects/slackgate")["items"]] == ["Socket mode"]
    assert mem.search(query='"unbalanced AND (')["items"] == []
    listed = mem.list(namespace="projects")
    assert listed["namespaces"] == {"projects/capy-bridge": 1, "projects/slackgate": 1}
    assert mem.list()["namespaces"]["decisions"] == 1


def test_markdown_export_and_mirror(tmp_path):
    out = tmp_path / "mirror"
    mem = Memory(Store(tmp_path / "m.db", clock=Clock()), mirror_dir=str(out))
    a = mem.write(source="claude", namespace="projects/capy-bridge", key="auth", title="Auth", body="PKCE", tags=["oauth"])
    md = (out / "projects" / "capy-bridge" / "auth.md").read_text()
    assert md.startswith("---\nid: ") and "source: claude" in md and "# Auth\n\nPKCE" in md
    assert "projects/capy-bridge" in (out / "INDEX.md").read_text()
    mem.delete(source="claude", id=a["id"])
    assert not (out / "projects" / "capy-bridge" / "auth.md").exists()


def test_memory_tools_over_mcp_record_source(make):
    _, c = make()
    tok = sign_in(c)["access_token"]
    w = payload(call(c, tok, "memory_write", {"namespace": "decisions", "title": "Use SQLite", "body": "FTS5 for search"}))
    r = payload(call(c, STATIC_KEY, "memory_read", {"id": w["id"]}))
    assert r["source"] == "claude" and r["body"] == "FTS5 for search"
    s = payload(call(c, STATIC_KEY, "memory_search", {"query": "fts5"}))
    assert s["items"][0]["id"] == w["id"]
    err = call(c, tok, "memory_write", {"namespace": "BAD NS", "title": "x", "body": "y"})
    assert err["isError"] is True and "namespace" in err["content"][0]["text"]
