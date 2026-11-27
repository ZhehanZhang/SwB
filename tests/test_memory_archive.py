import base64
import io
import json
import zipfile

import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY, call, error_text, new_core, payload, principal, rpc
from switchboard.memory import MemoryError_
from switchboard.memory_archive import export_zip, import_manifest, import_zip
from switchboard.refusals import OWNER_REQUIRED
from switchboard.store import memory_hash


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
        assert restored == files
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
    result = import_zip(core, owner, data)
    assert result["updated"] == 1
    assert core.memory.read(id=first)["body"] == "Body"
    row = core.store.one("SELECT body, tags FROM memory_revisions WHERE rowid = ?", (latest,))
    assert dict(row) == {"body": "Current copy", "tags": None}
    snapshot = core.store.one("SELECT * FROM memory_revisions WHERE memory_id = ? AND action = 'import snapshot'", (first,))
    assert snapshot["body"] == "Current copy" and snapshot["tags"] == "tag"
    revision = core.store.one("SELECT * FROM memory_revisions WHERE memory_id = ? AND action = 'import'", (first,))
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
    result = import_manifest(core, owner, json.dumps(manifest).encode())
    assert result == {
        "created": 0, "updated": 1, "unchanged": 0, "conflict": 1, "skipped": 1,
        "problems": [
            {"id": "different-id", "action": "conflict", "reason": "The memory path already exists."},
            {"id": "project-id", "action": "skipped", "reason": "Create project unknown first."},
        ],
    }
    assert core.store.one("SELECT content_hash FROM memories WHERE id = ?", (mid,))[0] == memory_hash("Updated on import")


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
