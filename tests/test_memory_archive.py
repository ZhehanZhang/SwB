import base64
import io
import json
import zipfile
from dataclasses import replace

import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY, call, error_text, new_core, payload, principal, rpc
from switchboard.memory import MemoryError_
from switchboard.memory_archive import export_zip, import_manifest, import_zip
from switchboard.refusals import OWNER_REQUIRED, SCOPE_REQUIRED, PolicyRefusal
from switchboard.store import memory_hash
from switchboard.trust import OPEN, unframe_memory


def unzip(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in archive.infolist())
        return {name: archive.read(name) for name in archive.namelist()}


def pack(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


def write(core, *, scope="general", key="note", body="Body", source="claude"):
    return core.memory.write(source=source, scope=scope, namespace="notes", title="Title", body=body, key=key,
                             tags=["tag"], confirm_general=True)["id"]


async def test_archive_round_trip(make, tmp_path, capy):
    core, _ = make()
    core.projects.create(slug="archive", name="Archive")
    mid = write(core, scope="project:archive")
    core.memory.write(source="chatgpt", scope="project:archive", namespace="notes", title="Changed", body="Revised", id=mid)
    core.store.x("UPDATE memories SET content_trust = 'owner', sync_source = ?, sync_key = ? WHERE id = ?",
                 ("claude/other", "native-id", mid))
    keyless = write(core, scope="agent:claude", key=None)
    core.memory.delete(source="claude", id=write(core))
    data, count, _ = export_zip(core, principal())
    files = unzip(data)
    assert count == 2
    assert set(files) == {"INDEX.md", "manifest.json", f"agents/claude/notes/{keyless}.md", "projects/archive/notes/note.md"}
    assert export_zip(core, principal())[0] == data
    assert b"Revised" in files["projects/archive/notes/note.md"]

    dest = new_core(tmp_path, capy, data=tmp_path / "fresh")
    try:
        dest.projects.create(slug="archive", name="Archive")
        assert import_zip(dest, principal(), data) == dict(created=2, updated=0, unchanged=0, conflict=0, skipped=0, problems=[])
        assert import_zip(dest, principal(), data)["unchanged"] == 2
        assert dest.memory.search(query="Revised", scopes=["project:archive"])["items"][0]["id"] == mid
        restored = unzip(export_zip(dest, principal())[0])
        assert set(restored) == set(files)
        original = json.loads(files["manifest.json"])
        imported = json.loads(restored["manifest.json"])
        assert imported["exported_at"] == original["exported_at"]
        for before, after in zip(original["entries"], imported["entries"], strict=True):
            for field in ("id", "path", "title", "body", "tags", "created_at", "updated_at"):
                assert after[field] == before[field]
            assert (after["created_by"], after["content_by"], after["content_trust"]) == ("claude", "import:claude", "agent")
            assert (after["sync_source"], after["sync_key"]) == (None, None)
            assert [r["body"] for r in after["history"]] == [r["body"] for r in before["history"]] + [after["body"]]
            assert all(r["source"] == "import:claude" and r["action"] == "imported from file" for r in after["history"])
    finally:
        await dest.aclose()


def test_archive_update_keeps_previous_content_in_history(make):
    core, _ = make()
    owner = principal("owner")
    first = write(core)
    data = export_zip(core, owner)[0]
    write(core, body="Current copy")
    latest = core.store.one("SELECT MAX(rowid) AS rev FROM memory_revisions")["rev"]
    core.store.x("UPDATE memory_revisions SET tags = NULL WHERE rowid = ?", (latest,))
    assert import_zip(core, owner, data)["problems"] == [
        {"id": first, "action": "conflict", "reason": "Use overwrite to replace an existing memory"},
    ]
    assert core.memory.read(id=first)["body"] == "Current copy"
    assert core.store.one("SELECT MAX(rowid) AS rev FROM memory_revisions")["rev"] == latest
    result = import_zip(core, owner, data, overwrite=True)
    assert result["updated"] == 1
    assert core.memory.read(id=first)["body"] == "Body"
    row = core.store.one("SELECT body, tags FROM memory_revisions WHERE rowid = ?", (latest,))
    assert dict(row) == {"body": "Current copy", "tags": None}
    snapshot = core.store.one("SELECT * FROM memory_revisions WHERE memory_id = ? AND action = 'import snapshot'", (first,))
    assert snapshot["body"] == "Current copy" and snapshot["tags"] == "tag"
    revision = core.store.one("SELECT * FROM memory_revisions WHERE memory_id = ? AND action = 'imported from file'", (first,))
    assert (revision["title"], revision["body"], revision["tags"], revision["trust"]) == ("Title", "Body", "tag", "agent")
    assert import_zip(core, owner, data)["unchanged"] == 1


def test_archive_conflict_unknown_project_and_recomputed_hash(make):
    core, _ = make()
    owner = principal()
    mid = write(core)
    manifest = json.loads(unzip(export_zip(core, owner)[0])["manifest.json"])
    entry = manifest["entries"][0]
    manifest["entries"] = [
        {**entry, "id": "different-id"},
        {**entry, "id": "project-id", "path": "projects/unknown/notes/note.md", "scope": "project:unknown"},
        {**entry, "body": "Updated on import", "content_hash": "incorrect"},
    ]
    result = import_manifest(core, owner, json.dumps(manifest).encode(), overwrite=True)
    assert result == {
        "created": 0, "updated": 1, "unchanged": 0, "conflict": 1, "skipped": 1,
        "problems": [
            {"id": "different-id", "action": "conflict", "reason": "The memory path already exists."},
            {"id": "project-id", "action": "skipped", "reason": "no project 'unknown'; see project_list, or create it with project_create"},
        ],
    }
    assert core.store.one("SELECT content_hash FROM memories WHERE id = ?", (mid,))[0] == memory_hash("Updated on import")


def test_import_caps_forged_provenance_and_uses_verified_importer(make):
    core, _ = make()
    owner = replace(principal(), source="admin:owner", kind="owner_session")
    core.projects.create(slug="private", name="Private", private=True)
    mid = write(core)
    manifest = json.loads(unzip(export_zip(core, owner)[0])["manifest.json"])
    entry = manifest["entries"][0]
    entry.update(body="Imported body", created_by="owner", content_by="forged", content_trust="owner")
    for rev in entry["history"]:
        rev.update(source="forged", action="rename forged", trust="owner")
    manifest["entries"] += [
        {**entry, "id": "other-agent", "scope": "agent:beta", "path": "agents/beta/notes/note.md"},
        {**entry, "id": "private-project", "scope": "project:private", "path": "projects/private/notes/note.md"},
    ]
    result = import_manifest(core, owner, manifest)
    assert (result["created"], result["conflict"]) == (2, 1)
    assert core.memory.read(id=mid)["body"] == "Body"
    assert import_manifest(core, owner, manifest, overwrite=True)["updated"] == 1
    for ident in (mid, "other-agent", "private-project"):
        restored = core.read_memory(owner, id=ident)
        assert "imported by admin:owner" in restored["body"] and f"\n{OPEN}\n" in restored["body"]
        assert unframe_memory(restored["body"]) == "Imported body"
        assert restored["content_trust"] == "agent"
        assert restored["source"] == "admin:owner"
        assert restored["content_by"] == "import:admin:owner"
        assert restored["created_by"] == ("claude" if ident == mid else "admin:owner")
        assert restored["history"][0]["action"] == "imported from file"
        assert all("forged" not in rev["source"] and "forged" not in rev["action"] for rev in restored["history"])

    imported_rev = core.read_memory(owner, id="other-agent")["history"][0]["rev"]
    core.memory.write(source=owner.source, scope="agent:beta", namespace="notes", title="Local", body="Local body",
                      id="other-agent", content_trust="owner")
    core.restore_memory(owner, imported_rev, id="other-agent")
    restored = core.read_memory(owner, id="other-agent")
    assert "imported by admin:owner" in restored["body"] and restored["content_trust"] == "agent"


def test_archive_owner_opt_in_respects_scope_and_existing_trust(make):
    core, _ = make()
    owner = principal()
    write(core)
    manifest = json.loads(unzip(export_zip(core, owner)[0])["manifest.json"])
    entry = manifest["entries"][0]
    manifest["entries"] = [
        {**entry, "id": "general-owner", "key": "trusted", "path": "general/notes/trusted.md", "content_trust": "owner"},
        {**entry, "id": "own-agent", "scope": "agent:claude", "path": "agents/claude/notes/note.md", "content_trust": "owner"},
        {**entry, "id": "other-agent", "scope": "agent:beta", "path": "agents/beta/notes/note.md", "content_trust": "owner"},
        {**entry, "id": "agent-content", "key": "lower", "path": "general/notes/lower.md", "content_trust": "agent"},
        {**entry, "id": "external-content", "key": "external", "path": "general/notes/external.md", "content_trust": "external"},
    ]
    non_owner = replace(owner, trust="agent")
    with pytest.raises(PolicyRefusal, match=OWNER_REQUIRED):
        import_manifest(core, non_owner, manifest, trust_owner=True)
    assert import_manifest(core, owner, manifest, trust_owner=True)["created"] == 5
    for ident, trust in (("general-owner", "owner"), ("own-agent", "owner"), ("other-agent", "agent"),
                         ("agent-content", "agent"), ("external-content", "external")):
        restored = core.read_memory(owner, id=ident)
        assert restored["content_trust"] == trust
        assert (f"\n{OPEN}\n" in restored["body"]) == (trust != "owner")
        assert "imported by claude" in restored["body"]
        assert unframe_memory(restored["body"]) == entry["body"]


def test_archive_checks_target_and_existing_scope(make):
    core, _ = make()
    owner = principal()
    core.projects.create(slug="private", name="Private", private=True)
    core.projects.create(slug="archived", name="Archived")
    mid = write(core, scope="project:private")
    manifest = json.loads(unzip(export_zip(core, owner)[0])["manifest.json"])
    entry = manifest["entries"][0]
    manifest["entries"] = [
        {**entry, "id": "blocked", "scope": "project:private"},
        {**entry, "id": "archived", "scope": "project:archived", "path": "projects/archived/notes/note.md"},
        {**entry, "scope": "general", "path": "general/notes/note.md", "body": "Move by overwrite"},
    ]
    core.projects.update("archived", archived=True)
    limited = replace(owner, kind="owner_session", scopes=("general", "project:archived"))
    result = import_manifest(core, limited, manifest, overwrite=True)
    assert result["problems"] == [
        {"id": "blocked", "action": "skipped", "reason": SCOPE_REQUIRED},
        {"id": "archived", "action": "skipped", "reason": "project 'archived' is archived"},
        {"id": mid, "action": "skipped", "reason": SCOPE_REQUIRED},
    ]
    assert core.memory.read(id=mid)["scope"] == "project:private"


@pytest.mark.parametrize("path", ["/absolute.md", "../outside.md", "a/../outside", "a//b", "C:/outside", "a\\..\\outside"])
def test_archive_rejects_unsafe_zip_names(make, path):
    core, _ = make()
    with pytest.raises(MemoryError_, match="safe, unique archive paths"):
        import_zip(core, principal(), pack({path: "outside", "manifest.json": "{}"}))


def test_archive_caps_compressed_size_expanded_size_and_entry_count(make, monkeypatch):
    core, _ = make()
    owner = principal()
    monkeypatch.setattr("switchboard.memory_archive.MAX_ARCHIVE_BYTES", 250)
    with pytest.raises(MemoryError_, match="50 MB"):
        import_zip(core, owner, b"x" * 251)
    data = pack({"manifest.json": b" " * 251})
    assert len(data) < 250
    with pytest.raises(MemoryError_, match="smaller"):
        import_zip(core, owner, data)
    monkeypatch.setattr("switchboard.memory_archive.MAX_ARCHIVE_BYTES", 10_000)
    monkeypatch.setattr("switchboard.memory_archive.MAX_ARCHIVE_ENTRIES", 2)
    with pytest.raises(MemoryError_, match="smaller"):
        import_zip(core, owner, pack({"manifest.json": "{}", "one.md": "", "two.md": ""}))


def test_owner_can_download_upload_and_call_mcp_archive_tools(make):
    core, client = make()
    write(core)
    csrf = client.get("/auth/session", headers=OWNER).json()["csrf"]
    headers = {**OWNER, "Origin": PUBLIC, "X-CSRF-Token": csrf}
    assert client.get("/api/memory/export").status_code == 401
    download = client.get("/api/memory/export", headers=OWNER)
    assert download.headers["content-type"] == "application/zip"
    assert download.headers["content-disposition"].startswith('attachment; filename="swb-memory-')
    assert client.post("/api/memory/import", headers={**OWNER, "Origin": PUBLIC}, content=download.content).status_code == 403
    assert client.post("/api/memory/import", headers=headers, content=download.content).json()["unchanged"] == 1
    assert client.post("/api/memory/import", headers=headers, files={"file": ("memory.zip", download.content)}).json()["unchanged"] == 1

    tools = {"memory_export", "memory_import", "memory_backup_status", "memory_backup_configure", "memory_backup_now", "memory_backup_restore"}
    paired = payload(call(client, STATIC_KEY, "connect_code", {"agent": "claude"}))["code"]
    paired = client.post("/connect", json={"code": paired}).json()["key"]
    assert not tools & {t["name"] for t in rpc(client, paired, "tools/list").json()["result"]["tools"]}
    assert all(OWNER_REQUIRED in error_text(call(client, paired, tool)) for tool in tools)
    content = call(client, STATIC_KEY, "memory_export")["content"]
    assert "1 memory entries" in content[0]["text"]
    assert content[1]["type"] == "resource"
    resource = content[1]["resource"]
    assert resource["mimeType"] == "application/zip"
    assert resource["uri"].startswith("swb://memory/export/")
    assert base64.b64decode(resource["blob"]) == download.content
    assert payload(call(client, STATIC_KEY, "memory_import", {"zip_base64": resource["blob"]}))["unchanged"] == 1


def test_console_and_mcp_import_accept_explicit_flags(make):
    core, client = make()
    mid = write(core)
    owner = principal()
    manifest = json.loads(unzip(export_zip(core, owner)[0])["manifest.json"])
    entry = manifest["entries"][0]
    csrf = client.get("/auth/session", headers=OWNER).json()["csrf"]
    headers = {**OWNER, "Origin": PUBLIC, "X-CSRF-Token": csrf}
    entry.update(body="Console import", content_trust="owner")
    upload = ("memory.zip", pack({"manifest.json": json.dumps(manifest)}))
    path = "/api/memory/import"
    assert client.post(path, headers=headers, files={"file": upload}).json()["conflict"] == 1
    assert client.post(path + "?overwrite=true&overwrite=false", headers=headers, files={"file": upload}).status_code == 400
    assert client.post(path + "?overwrite=true&trust_owner=true", headers=headers, files={"file": upload}).json()["updated"] == 1
    assert core.memory.read(id=mid)["content_trust"] == "owner"

    entry["body"] = "MCP import"
    encoded = base64.b64encode(pack({"manifest.json": json.dumps(manifest)})).decode()
    args = {"zip_base64": encoded, "trust_owner": True}
    assert payload(call(client, STATIC_KEY, "memory_import", args))["conflict"] == 1
    assert payload(call(client, STATIC_KEY, "memory_import", {**args, "overwrite": True}))["updated"] == 1
    assert core.memory.read(id=mid)["content_trust"] == "owner"
