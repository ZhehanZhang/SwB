import hashlib
import json
import logging

import pytest

from conftest import OWNER, PUBLIC, SECOND_KEY, STATIC_KEY, acall, base_config, call, error_text, payload, rpc
from switchboard.blocks.mcp.policy import MEMORY_NOT_FOUND
from switchboard.config import INSTANCE_DB
from switchboard.connect import Connections
from switchboard.memory import Memory
from switchboard.projects import Projects
from switchboard.refusals import MEMORY_WRITER_REQUIRED, OWNER_REQUIRED, SCOPE_REQUIRED
from switchboard.store import Store


def pair(client, agent, project=None):
    args = {"agent": agent, "project": project}
    code = payload(call(client, STATIC_KEY, "connect_code", args))["code"]
    response = client.post("/connect", json={"code": code})
    assert response.status_code == 200
    return response.json()


def write(client, credential, scope, **extra):
    return call(client, credential, "memory_write", {
        "scope": scope, "namespace": "notes", "title": "Fixture memory", "body": "Searchable policy fixture", **extra,
    })


@pytest.fixture
def workspace(make):
    cfg = base_config(version=2, blocks={"mcp": {}})
    cfg["auth"]["key_scopes"] = {
        "second": ["project:demo", "project:other"],
        "scoped": ["project:demo", "project:secretproj", "general"],
    }
    core, client = make(cfg)
    for slug in ("demo", "other", "secretproj"):
        core.projects.create(slug=slug, name=slug, private=slug == "secretproj")
    agent = pair(client, "alpha", "demo")
    sibling = pair(client, "alpha", "demo")
    scoped = pair(client, "scoped")
    default = pair(client, "default")
    return core, client, agent, sibling, scoped, default


@pytest.mark.parametrize("name", ["memory_read", "memory_search", "memory_list", "context_pack"])
def test_r4c6_reads_follow_the_grant(workspace, name):
    core, client, agent, _, scoped, default = workspace
    demo = payload(write(client, STATIC_KEY, "project:demo"))["id"]
    private = payload(write(client, STATIC_KEY, "project:secretproj"))["id"]
    general = payload(write(client, STATIC_KEY, "general", confirm_general=True))["id"]

    def args(ident, project):
        if name == "memory_read":
            return {"id": ident}
        return {"project": project, **({"query": "Searchable"} if name == "memory_search" else {})}

    for ident, project in ((demo, "demo"), (private, "secretproj"), (general, "general")):
        request = args(ident, project)
        for key in (STATIC_KEY, scoped["key"]):
            assert not call(client, key, name, request).get("isError")
        assert error_text(call(client, default["key"], name, request)) == MEMORY_NOT_FOUND
        response = call(client, agent["key"], name, request)
        if project == "demo":
            assert not response.get("isError")
            assert general not in json.dumps(response)
        else:
            assert error_text(response) == MEMORY_NOT_FOUND
        stranger = rpc(client, "unknown-fixture-credential", "tools/call", {"name": name, "arguments": request})
        assert stranger.status_code == 401
    rows = core.audit.recent(action="tool_refused", limit=20)
    assert len(rows) == 5
    assert all((r["target"], r["detail"]) == (name, MEMORY_NOT_FOUND) for r in rows)
    assert all(value not in json.dumps(rows) for value in (demo, private, general, default["key"]))


def test_project_list_hides_private_metadata_in_default_and_hint_reads(workspace):
    _, client, agent, _, scoped, default = workspace
    for key, expected in ((STATIC_KEY, True), (scoped["key"], True), (agent["key"], False),
                          (default["key"], False), (SECOND_KEY, False)):
        listed = payload(call(client, key, "project_list"))["projects"]
        assert ("secretproj" in {row["slug"] for row in listed}) is expected
        matches = payload(call(client, key, "project_list", {"hint": "secretproj"}))["matches"]
        assert bool(matches) is expected
    assert rpc(client, "unknown-fixture-credential", "tools/call", {"name": "project_list"}).status_code == 401


def test_oauth_and_configured_keys_keep_their_memory_roles(workspace):
    core, client, _, _, _, _ = workspace
    oauth = core.auth._issue("fixture-client", core.cfg.resource, "oauth-owner")["access_token"]
    scoped_oauth = core.auth._issue("fixture-scoped-client", core.cfg.resource, "second")["access_token"]
    private = payload(write(client, STATIC_KEY, "project:secretproj"))["id"]
    assert payload(call(client, oauth, "memory_read", {"id": private}))["id"] == private
    for credential in (SECOND_KEY, scoped_oauth):
        assert error_text(call(client, credential, "memory_read", {"id": private})) == MEMORY_NOT_FOUND
        assert payload(write(client, credential, "project:demo"))["scope"] == "project:demo"
    assert payload(call(client, oauth, "memory_delete", {"id": private}))["deleted"] == private


def test_write_checks_the_grant_and_agent_scope_is_usable(workspace, tmp_path):
    core, client, agent, _, _, default = workspace
    key = default["key"]
    scope = "agent:default"
    entry = payload(write(client, key, scope, project="demo"))
    assert entry["scope"] == scope
    assert payload(call(client, key, "memory_read", {"id": entry["id"]}))["id"] == entry["id"]
    for tool, args in (("memory_search", {"query": "Searchable"}), ("memory_list", {})):
        items = payload(call(client, key, tool, {"scopes": [scope], **args}))["items"]
        assert [item["id"] for item in items] == [entry["id"]]
    assert error_text(call(client, agent["key"], "memory_read", {"id": entry["id"]})) == MEMORY_NOT_FOUND
    for scope in ("agent:alpha", "project:demo", "general"):
        assert error_text(write(client, key, scope, confirm_general=True)) == SCOPE_REQUIRED
    assert error_text(write(client, SECOND_KEY, "project:secretproj")) == SCOPE_REQUIRED
    assert rpc(client, "unknown-fixture-credential", "tools/call", {
        "name": "memory_write", "arguments": {"scope": "project:demo"},
    }).status_code == 401
    assert core.memory.export(tmp_path / "export") == 1
    assert (tmp_path / "export" / "agents" / "default" / "notes" / (entry["id"] + ".md")).is_file()


@pytest.mark.parametrize("name", ["memory_delete", "memory_move", "memory_promote"])
def test_mutations_require_exact_writer_and_both_grants(workspace, name):
    core, client, agent, sibling, scoped, default = workspace
    owner_entry = payload(write(client, STATIC_KEY, "project:demo", key="owner-note"))["id"]
    agent_entry = payload(write(client, agent["key"], "project:demo"))["id"]
    scoped_entry = payload(write(client, SECOND_KEY, "project:demo"))["id"]
    assert error_text(write(client, agent["key"], "project:demo", key="owner-note", mode="append")) == MEMORY_WRITER_REQUIRED
    assert error_text(write(client, sibling["key"], "project:demo", id=agent_entry)) == MEMORY_WRITER_REQUIRED
    assert payload(write(client, agent["key"], "project:demo", id=agent_entry, mode="append"))["action"] == "append"

    def args(ident):
        return {"id": ident, **({"to_scope": "agent:alpha"} if name == "memory_move" else {})}

    if name == "memory_promote":
        for key in (agent, sibling, default):
            assert error_text(call(client, key["key"], name, args(agent_entry))) == OWNER_REQUIRED
        assert error_text(call(client, scoped["key"], name, args(agent_entry))) == OWNER_REQUIRED
        assert payload(call(client, STATIC_KEY, name, args(owner_entry)))["to"] == "general"
    else:
        for key, ident in ((agent["key"], owner_entry), (sibling["key"], agent_entry), (default["key"], agent_entry)):
            result = call(client, key, name, args(ident))
            assert error_text(result) in (MEMORY_WRITER_REQUIRED, SCOPE_REQUIRED)
        if name == "memory_move":
            assert error_text(call(client, agent["key"], name, {"id": agent_entry, "to_scope": "project:other"})) == SCOPE_REQUIRED
            assert payload(call(client, agent["key"], name, args(agent_entry)))["to"] == "agent:alpha"
            assert payload(call(client, SECOND_KEY, name, {"id": scoped_entry, "to_scope": "project:other"}))["to"] == "project:other"
            assert payload(call(client, STATIC_KEY, name, args(owner_entry)))["to"] == "agent:alpha"
        else:
            general = payload(write(client, STATIC_KEY, "general", confirm_general=True))["id"]
            assert error_text(call(client, scoped["key"], name, {"id": general})) == OWNER_REQUIRED
            assert payload(call(client, agent["key"], name, args(agent_entry)))["deleted"] == agent_entry
            assert payload(call(client, SECOND_KEY, name, args(scoped_entry)))["deleted"] == scoped_entry
            assert payload(call(client, STATIC_KEY, name, args(owner_entry)))["deleted"] == owner_entry
    assert rpc(client, "unknown-fixture-credential", "tools/call", {"name": name, "arguments": args(owner_entry)}).status_code == 401
    rows = [row for row in core.audit.recent(action="tool_refused", limit=20) if row["target"] == name]
    assert len(rows) == 4
    assert all(row["detail"] in (MEMORY_WRITER_REQUIRED, SCOPE_REQUIRED, OWNER_REQUIRED) for row in rows)
    assert all(value not in json.dumps(rows) for value in (owner_entry, agent_entry, scoped_entry, agent["key"]))


def test_owner_session_only_can_remove_a_single_paired_scope(workspace):
    core, client, agent, sibling, _, _ = workspace
    private = payload(write(client, STATIC_KEY, "project:demo"))["id"]
    path = f"/api/pairing/keys/{agent['key_id']}/memory/remove"
    args = {"scope": "project:demo"}
    assert client.post(path, headers={"Authorization": f"Bearer {agent['key']}"}, json=args).status_code == 401
    assert client.post(path, headers=OWNER, json=args).status_code == 403
    token = client.get("/auth/session", headers=OWNER).json()["csrf"]
    verified = {**OWNER, "origin": PUBLIC, "x-csrf-token": token}
    assert client.post(path, headers={**verified, "origin": "https://other.example.test"}, json=args).status_code == 403
    assert client.post(path, headers=verified, json={"scope": "*"}).status_code == 400
    old = client.get("/api/tokens", headers=OWNER)
    items = {row["id"]: row for row in old.json()["stored"]}
    assert items[agent["key_id"]]["memory_scopes"] == ["agent:alpha", "project:demo"]
    assert agent["key"] not in old.text and hashlib.sha256(agent["key"].encode()).hexdigest() not in old.text
    assert client.post(path, headers=verified, json=args).json() == {
        "id": agent["key_id"], "scopes": ["agent:alpha"], "removed": True,
    }
    assert client.post(path, headers=verified, json=args).json()["removed"] is False
    assert error_text(call(client, agent["key"], "memory_read", {"id": private})) == MEMORY_NOT_FOUND
    assert payload(call(client, sibling["key"], "memory_read", {"id": private}))["id"] == private
    assert not call(client, agent["key"], "send", {"to": "agent:scoped", "task": "Fixture", "project": "demo"}).get("isError")
    [audit] = core.audit.recent(action="memory_grant_removed")
    assert audit["target"] == agent["key_id"]


@pytest.mark.parametrize("project", ["old", "project:old"])
def test_pending_pairing_gets_the_renamed_project_grant(workspace, project):
    core, client, *_ = workspace
    core.projects.create(slug="old", name="Old")
    code = payload(call(client, STATIC_KEY, "connect_code", {"agent": "pending", "project": project}))["code"]
    core.projects.update("old", new_slug="new")
    paired = client.post("/connect", json={"code": code}).json()
    assert paired["project"] == "new"
    assert core.connections.memory_scopes(paired["key_id"]) == ("agent:pending", "project:new")


async def test_startup_migrates_active_legacy_keys_once_then_keeps_new_defaults(tmp_path, amake, caplog):
    data = tmp_path / "data"
    store = Store(data / INSTANCE_DB)
    Connections(store)
    for table in ("paired_memory_grants", "paired_memory_grants_migration"):
        store.x(f"DROP TABLE {table}")
    projects = Projects(store)
    projects.create(slug="demo", name="Demo")
    projects.create(slug="private", name="Private", private=True)
    memory = Memory(store, projects)
    entries = {scope: memory.write(source="owner", scope=scope, namespace="notes", title="Legacy memory", body=f"Saved in {scope}",
                                   confirm_general=True)["id"] for scope in ("general", "project:demo", "project:private")}
    store.x("ALTER TABLE memories DROP COLUMN writer_credential")
    keys = {agent: "swb_" + agent * 16 for agent in ("legacy", "limited", "revoked")}
    for agent, key in keys.items():
        store.x("INSERT INTO agent_keys(id, agent, key_hash, created_by, created_at, revoked_at) VALUES (?,?,?,?,?,?)",
                (f"ak_{agent}", agent, hashlib.sha256(key.encode()).hexdigest(), "owner", 1, 2 if agent == "revoked" else None))
    store.close()
    cfg = base_config(version=2, blocks={"mcp": {}})
    cfg["auth"]["key_scopes"] = {"limited": ["project:demo"]}
    with caplog.at_level(logging.INFO, logger="switchboard"):
        first, client = await amake(config=cfg)
        try:
            assert first.auth.verify_bearer(keys["legacy"]).memory_grants == ("general", "project:demo", "project:private")
            assert first.auth.verify_bearer(keys["limited"]).memory_grants == ("project:demo",)
            assert first.auth.verify_bearer(keys["revoked"]) is None
            assert first.connections.memory_scopes("ak_revoked") == ()
            for scope, ident in entries.items():
                result = payload(await acall(client, keys["legacy"], "memory_read", {"id": ident}))
                assert (result["scope"], result["body"]) == (scope, f"Saved in {scope}")
            result = await acall(client, keys["limited"], "memory_read", {"id": entries["general"]})
            assert error_text(result) == MEMORY_NOT_FOUND
            code = first.connections.mint(agent="new", project="demo", client=None, by="owner")["code"]
            fresh = first.connections.redeem(code)
            assert first.auth.verify_bearer(fresh["key"]).memory_grants == ("agent:new", "project:demo")
            assert first.connections.remove_memory_scope("ak_legacy", "general")
        finally:
            await first._test_close()
        second, client = await amake(config=cfg)
        try:
            assert second.auth.verify_bearer(fresh["key"]).memory_grants == ("agent:new", "project:demo")
            assert second.auth.verify_bearer(keys["legacy"]).memory_grants == ("project:demo", "project:private")
            result = await acall(client, keys["legacy"], "memory_read", {"id": entries["general"]})
            assert error_text(result) == MEMORY_NOT_FOUND
            counts = second.store.one("SELECT keys_migrated, grants_migrated FROM paired_memory_grants_migration WHERE version = 1")
            assert tuple(counts) == (2, 4)
            audits = second.audit.recent(action="memory_grants_migrated")
            assert {(row["target"], row["detail"]) for row in audits} == {
                ("ak_legacy", "Preserved existing paired key memory access."),
                ("ak_limited", "Preserved existing paired key memory access."),
            }
            assert len(audits) == 2 and not any(key in json.dumps(audits) for key in keys.values())
        finally:
            await second._test_close()
    assert [record.message for record in caplog.records if record.message.startswith("Paired memory grants")] == [
        "Paired memory grants migration: 2 keys, 4 grants",
    ]


def test_fresh_start_records_empty_grants_migration_without_logging(make, caplog):
    with caplog.at_level(logging.INFO, logger="switchboard"):
        core, _ = make(config=base_config(version=2, blocks={"mcp": {}}))

    counts = core.store.one("SELECT keys_migrated, grants_migrated FROM paired_memory_grants_migration WHERE version = 1")
    assert tuple(counts) == (0, 0)
    assert not any(record.message.startswith("Paired memory grants migration:") for record in caplog.records)
