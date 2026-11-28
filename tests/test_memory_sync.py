import json

import pytest

from conftest import STATIC_KEY, base_config, call, error_text, rpc, sign_in
from switchboard.auth import Principal
from switchboard.memory import MemoryRefused
from switchboard.refusals import OWNER_REQUIRED
from test_api_pages import csrf
from test_memory_files import mcp
from test_paired_memory_grants import workspace as workspace


def sync(client, key, *items, **options):
    return mcp(client, "memory_sync", key, source="claude-memory", items=list(items), **options)


def test_sync_actions_idempotency_delete_and_audit(workspace):
    core, client, agent, *_ = workspace
    key = agent["key"]
    item = {"source_key": "Native Paths/A.txt", "title": "Fact", "body": "One fact."}
    [created] = sync(client, key, item)["items"]
    path = "/memories/agents/alpha/synced/claude-memory/native-paths-a-txt.md"
    assert created == {"source_key": item["source_key"], "action": "created", "id": created["id"], "path": path}
    assert core.memory.read(id=created["id"])["sync_source"] == "alpha/claude-memory"
    assert sync(client, key, item)["items"] == [{**created, "action": "unchanged"}]
    assert sync(client, key, {**item, "body": " One  Fact. "})["counts"] == {"unchanged": 1}
    result = sync(client, key, {**item, "body": "Second fact", "tags": ["Learned"]})
    assert result == {"items": [{**created, "action": "updated"}], "counts": {"updated": 1}}
    assert sync(client, key, {**item, "source_key": "another", "body": "SECOND FACT"})["items"] == [
        {**created, "source_key": "another", "action": "duplicate"}
    ]
    row = core.memory.read(id=created["id"])
    assert row["tags"] == ["learned"] and row["content_by"] == row["sync_source"]
    assert [rev["action"] for rev in row["history"]] == ["sync", "sync"]
    assert sync(client, key, {"source_key": item["source_key"], "deleted": True})["items"] == [{**created, "action": "deleted"}]
    assert sync(client, key, {"source_key": item["source_key"], "deleted": True})["counts"] == {"unchanged": 1}
    history = mcp(client, "memory_history", key, path=path)["history"]
    assert history[0]["action"] == "delete" and history[0]["scope"] == "agent:alpha"
    assert core.store.one("SELECT deleted_by FROM memories WHERE id = ?", (created["id"],))["deleted_by"] == "alpha/claude-memory"
    assert sync(client, key, item)["items"] == [{**created, "action": "updated"}]
    audit = core.audit.recent(action="memory_sync")
    assert len(audit) == 8
    assert [json.loads(r["detail"]) for r in reversed(audit)] == [
        {"created": 1}, {"unchanged": 1}, {"unchanged": 1}, {"updated": 1}, {"duplicate": 1}, {"deleted": 1}, {"unchanged": 1}, {"updated": 1},
    ]
    assert {r["target"] for r in audit} == {"alpha/claude-memory"}


@pytest.mark.parametrize("editor", ["console", "agent", "another agent"])
def test_sync_does_not_overwrite_manual_edits(workspace, editor):
    core, client, agent, *_ = workspace
    key = agent["key"]
    item = {"source_key": "stable", "title": "Fact", "body": "Native body"}
    [created] = sync(client, key, item, project="demo")["items"]
    ident, path = created["id"], created["path"]
    updated = "SwB copy"
    if editor == "console":
        response = client.post(f"/api/memory/{ident}/edit", headers=csrf(client), json={"title": "Owner correction", "body": updated})
        assert response.status_code == 200
    else:
        mcp(client, "memory_str_replace", key if editor == "agent" else STATIC_KEY, path=path, old_str=item["body"], new_str=updated)
    assert sync(client, key, {**item, "body": "Native changed"}, project="demo")["items"] == [{**created, "action": "conflict"}]
    assert sync(client, key, {"source_key": item["source_key"], "deleted": True}, project="demo")["counts"] == {"conflict": 1}
    assert core.memory.read(id=ident)["body"] == updated
    assert client.delete(f"/api/memory/{ident}", headers=csrf(client)).status_code == 200
    result = sync(client, key, item, project="demo")
    assert result["items"] == [{**created, "action": "skipped", "reason": "The owner deleted this memory."}]
    assert core.store.one("SELECT deleted_by FROM memories WHERE id = ?", (ident,))["deleted_by"] == "admin:owner@example.test"


def test_sync_scope_priority_identity_binding_and_collisions(workspace):
    core, client, agent, sibling, scoped, default = workspace
    item = {"source_key": "same native id", "body": "First alpha memory"}
    [first] = sync(client, agent["key"], item, project="demo")["items"]
    [other] = sync(client, default["key"], {**item, "body": "First default memory"})["items"]
    assert first["id"] != other["id"]
    assert first["path"].startswith("/memories/projects/demo/") and other["path"].startswith("/memories/agents/default/")
    assert sync(client, sibling["key"], {**item, "body": "Sibling edit"}, project="demo")["counts"] == {"conflict": 1}
    assert core.memory.read(id=first["id"])["body"] == item["body"]
    [general] = sync(client, scoped["key"], {"source_key": "general fact", "body": "General native body"})["items"]
    assert general["path"] == "/memories/general/synced/claude-memory/general-fact.md"
    assert sync(client, scoped["key"], {"source_key": "general fact", "body": "General native update"})["counts"] == {"updated": 1}
    item_scope = {"source_key": "Call scope", "body": "Call scope body", "namespace": "notes/native", "scope": "agent:alpha"}
    [explicit, same_slug] = sync(client, agent["key"], item_scope, {**item_scope, "source_key": "Call/scope", "body": "Other native body"},
                                 scope="project:demo", project="secretproj")["items"]
    assert explicit["path"] == "/memories/agents/alpha/notes/native/call-scope.md"
    assert same_slug["path"].startswith("/memories/agents/alpha/notes/native/call-scope-")
    assert len(same_slug["path"].rsplit("-", 1)[1]) == 9
    denied = sync(client, default["key"], {**item, "scope": "project:secretproj"})
    assert denied == {"counts": {"skipped": 1}, "items": [
        {"source_key": item["source_key"], "action": "skipped", "reason": "The target scope is outside this key's grants."}
    ]}
    assert sync(client, STATIC_KEY, {"source_key": "top", "body": "Unrestricted general fallback"})["items"][0]["path"].startswith("/memories/general/")


def test_sync_switch_config_owner_only_catalog_cap_and_live_instructions(make):
    core, client = make(base_config(version=2, blocks={"mcp": {}}, memory={"sync": False}))
    key = core.connections.redeem(core.connections.mint(agent="notes", project=None, client=None, by="owner")["code"])["key"]
    item = {"source_key": "memo", "body": "A native memory"}
    arguments = {"source": "claude-memory", "items": [item]}
    init = {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1.0"}}
    assert "Memory sync is off." in rpc(client, key, "initialize", init).json()["result"]["instructions"]
    assert error_text(call(client, key, "memory_sync", arguments)) == "The owner turned memory sync off."
    assert core.audit.recent(action="memory_sync")[0]["detail"] == '{"skipped": 1}'
    assert mcp(client, "memory_summary", key).endswith("Memory sync is off.")
    assert "memory_settings" not in {tool["name"] for tool in rpc(client, key, "tools/list").json()["result"]["tools"]}
    assert error_text(call(client, key, "memory_settings", {"sync": True})) == f"This key belongs to a connected agent. {OWNER_REQUIRED}"
    assert mcp(client, "memory_settings", sync=True) == {"sync": True}
    assert core.store.one("SELECT value FROM settings WHERE key = 'memory.sync'")["value"] == "true"
    assert "Memory sync is on." in rpc(client, key, "initialize", init).json()["result"]["instructions"]
    assert sync(client, key, item)["counts"] == {"created": 1}
    assert error_text(call(client, key, "memory_sync", {**arguments, "items": [item] * 51})) == "Sync at most 50 items per call."
    sources = ("agents-md, capy-drive, chatgpt-memory, claude-code-auto-memory, claude-md, claude-memory, codex-memory, "
               "gemini-memory, grok-memory, muse-memory, other")
    message = f"Use one of these memory sources {sources}."
    assert error_text(call(client, key, "memory_sync", {**arguments, "source": "this-is-not-allowed"})) == message
    tool = next(tool for tool in rpc(client, key, "tools/list").json()["result"]["tools"] if tool["name"] == "memory_sync")
    assert tool["description"].endswith(message)


def test_muse_sync_uses_its_own_source_label(make):
    core, client = make(base_config(version=2, blocks={"mcp": {}}))
    key = sign_in(client, redirect="https://agent.meta.ai/api/hatch/oauth/callback")["access_token"]
    item = {"source_key": "memo", "body": "A memory from Muse"}
    result = mcp(client, "memory_sync", key, source="muse-memory", items=[item], scope="agent:muse")
    assert result["counts"] == {"created": 1}
    [created] = result["items"]
    assert created["path"] == "/memories/agents/muse/synced/muse-memory/memo.md"
    assert core.memory.read(id=created["id"])["sync_source"] == "muse/muse-memory"


def test_scoped_companion_keeps_its_writer_identity_and_respects_the_sync_switch(workspace):
    core, _, *_ = workspace
    fields = {"source": "claude-code", "kind": "companion", "origin": "companion", "trust": "device", "scopes": ("project:demo",)}
    device = Principal(client_id="companion:one", credential="companion:one", **fields)
    other = Principal(client_id="companion:two", credential="companion:two", **fields)
    item = {"source_key": "project/memory.md", "body": "Initial project note"}
    assert core.sync_memory(device, "claude-code-auto-memory", [item], project="demo")["counts"] == {"created": 1}
    item["body"] = "Updated project note"
    assert core.sync_memory(other, "claude-code-auto-memory", [item], project="demo")["counts"] == {"conflict": 1}
    assert core.sync_memory(device, "claude-code-auto-memory", [item], project="demo")["counts"] == {"updated": 1}
    core.set_memory_setting(core.auth.verify_bearer(STATIC_KEY), False)
    with pytest.raises(MemoryRefused, match=r"^The owner turned memory sync off\.$"):
        core.sync_memory(device, "claude-code-auto-memory", [item], project="demo")
