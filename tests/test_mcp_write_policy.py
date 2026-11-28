import json
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from conftest import CHANNEL_KEY, OWNER, STATIC_KEY, base_config, call, error_text, payload, rpc
from switchboard.blocks import BLOCKS
from switchboard.blocks.mcp import PRINCIPAL_SCOPE, WRITE
from switchboard.blocks.mcp.policy import (
    BLOCK_ROLES, CHAT_ROLES, CONFIRMATION_REQUIRED, CONVERSATION_REQUIRED, MUTATION_ROLES, OWNER_REQUIRED, POLICY_REQUIRED, ROUTE_REQUIRED,
    TARGET_REQUIRED,
)

PROVIDER_ROLES = {f"{block}_{tool}": role for block, tools in {**BLOCK_ROLES, "discord": CHAT_ROLES, "telegram": CHAT_ROLES}.items()
                  for tool, role in tools.items()}
CURSOR_TOOLS = {"wait", "inbox", "capy_wait", "capy_inbox", "slack_wait"}
SECRET = "fixture-argument-that-must-stay-out-of-audit"


@pytest.fixture
def server(make):
    core, client = make(config=base_config(version=2, blocks={use: {"use": use} for use in BLOCKS}))
    return core, client, core.blocks["mcp"].mcp


@pytest.fixture
def paired(server):
    _, client, _ = server
    return pair(client, "paired")


def pair(client, agent):
    code = payload(call(client, STATIC_KEY, "connect_code", {"agent": agent}))["code"]
    return client.post("/connect", json={"code": code}).json()["key"]


async def test_every_mutating_tool_declares_a_role(server):
    _, _, mcp = server
    annotated = {t.name for t in await mcp.list_tools() if not (t.annotations and t.annotations.read_only_hint)}
    assert set(mcp.roles) == annotated | CURSOR_TOOLS


def test_tools_list_and_dispatch_share_owner_and_key_roles(server, paired):
    core, client, mcp = server
    core.cfg.key_scopes["scoped"] = ("project:example",)
    scoped = pair(client, "scoped")
    core.projects.create(slug="example", name="Example")

    def names(key):
        response = rpc(client, key, "tools/list")
        assert response.status_code == 200
        return {tool["name"] for tool in response.json()["result"]["tools"]}

    owner = names(STATIC_KEY)
    assert owner == {tool.name for tool in client.portal.call(mcp.list_tools)}
    owner_only = {name for name, role in mcp.roles.items() if role == "owner"}
    owner_only |= {name for name, (role, _) in mcp.read_roles.items() if role == "owner"}
    assert {"connect_list", "visibility_list", "login_link", "project_create", "space_mode", "telegram_provision"} <= owner_only
    paired_names = names(paired)
    assert names(scoped) == paired_names == owner - owner_only
    assert {"routes_set", "reply", "inbox", "memory_read", "memory_write", "github_comment"} <= paired_names
    for key in (paired, scoped):
        for name in owner_only:
            assert OWNER_REQUIRED in error_text(call(client, key, name))
    payload(call(client, scoped, "memory_write", {
        "scope": "project:example", "namespace": "notes", "title": "Fixture", "body": "Project note",
    }))

    core.cfg.key_tools["scoped"] = ("visibility_list", "memory_search")
    assert names(scoped) == {"memory_search"}
    assert OWNER_REQUIRED in error_text(call(client, scoped, "visibility_list"))
    assert "may call only" in error_text(call(client, scoped, "memory_write"))

    denied = rpc(client, CHANNEL_KEY, "tools/list")
    assert denied.status_code == 403 and denied.json()["error"] == "insufficient_scope"
    assert "result" not in denied.json()


@pytest.mark.parametrize("block", ["webhook", "custom"])
def test_webhook_tools_are_hidden_and_refused_for_non_owner(make, block):
    core, client = make(base_config(version=2, blocks={block: {"use": "webhook"}}))
    paired = pair(client, "paired")
    args = {"name": "receiver", "direction": "out"}
    names = {f"{block}_{action}" for action in ("create", "list", "rotate", "revoke", "test")}
    assert names <= {t["name"] for t in rpc(client, STATIC_KEY, "tools/list").json()["result"]["tools"]}
    assert names.isdisjoint(t["name"] for t in rpc(client, paired, "tools/list").json()["result"]["tools"])
    assert OWNER_REQUIRED in error_text(call(client, paired, f"{block}_create", args))
    assert OWNER_REQUIRED in error_text(call(client, paired, f"{block}_list"))
    assert {row["target"] for row in core.audit.recent(action="tool_refused")} == {f"{block}_create", f"{block}_list"}
    assert payload(call(client, STATIC_KEY, f"{block}_list")) == {"webhooks": []}
    created = payload(call(client, STATIC_KEY, f"{block}_create", args))
    assert created["address"] == f"{block}:out:receiver"
    assert [row["name"] for row in payload(call(client, STATIC_KEY, f"{block}_list"))["webhooks"]] == ["receiver"]
    assert payload(call(client, STATIC_KEY, "switchboard_status"))["destinations"][block]["waiting"] == ["receiver"]
    assert payload(call(client, paired, "switchboard_status"))["destinations"][block] == {
        "online": True, "configured": True, "state": "connected",
    }


@pytest.mark.parametrize(("name", "role"), sorted({**MUTATION_ROLES, **PROVIDER_ROLES}.items()))
def test_mutation_roles_before_dispatch(server, paired, monkeypatch, name, role):
    core, client, _ = server
    reached = []

    async def dispatch(self, name, arguments, context):
        reached.append(name)
        return CallToolResult(content=[])

    monkeypatch.setattr(MCPServer, "call_tool", dispatch)
    args = {"agent": "other", "note": SECRET} if role == "route" else {"text": SECRET}
    assert not call(client, STATIC_KEY, name, args).get("isError")
    result = call(client, paired, name, args)
    if role in ("owner", "route"):
        assert (OWNER_REQUIRED if role == "owner" else ROUTE_REQUIRED) in error_text(result)
        [row] = core.audit.recent(action="tool_refused")
        assert (row["target"], row["source"], row["outcome"]) == (name, "paired", "refused")
        assert SECRET not in json.dumps(row) and paired not in json.dumps(row)
        assert reached == [name]
    else:
        assert not result.get("isError")
        assert reached == [name, name]
    assert rpc(client, "invalid-fixture-key", "tools/call", {"name": name, "arguments": args}).status_code == 401
    assert len(reached) == (1 if role in ("owner", "route") else 2)


async def test_unclassified_writes_fail_closed_even_for_owner(server):
    core, _, mcp = server

    @mcp.tool(annotations=WRITE)
    def unclassified():
        raise AssertionError("An unclassified tool must never run")

    context = SimpleNamespace(request_context=SimpleNamespace(
        request=SimpleNamespace(scope={PRINCIPAL_SCOPE: core.auth.verify_bearer(STATIC_KEY)})))
    with pytest.raises(ToolError, match=POLICY_REQUIRED):
        await mcp.call_tool("unclassified", {}, context)


def test_paired_agent_can_change_its_own_routes_and_read_projects(server, paired):
    core, client, _ = server
    payload(call(client, STATIC_KEY, "project_create", {"slug": "example", "name": "Example"}))
    global_args = {"target": "agent:second"}
    owner = payload(call(client, STATIC_KEY, "routes_set", global_args))
    foreign = payload(call(client, STATIC_KEY, "routes_set", {**global_args, "agent": "claude"}))
    assert error_text(call(client, paired, "routes_set", {"target": "agent:paired", "agent": "claude"})) == ROUTE_REQUIRED
    (refusal,) = core.audit.recent(action="tool_refused")
    assert (refusal["target"], refusal["source"], refusal["detail"]) == ("routes_set", "paired", ROUTE_REQUIRED)
    assert error_text(call(client, paired, "routes_set", {"target": "agent:paired"})) == ROUTE_REQUIRED
    assert error_text(call(client, paired, "routes_set", {"target": "agent:paired", "project": "example"})) == ROUTE_REQUIRED
    assert error_text(call(client, paired, "routes_remove", {"block": "agent"})) == ROUTE_REQUIRED
    args = {"agent": " PAIRED ", "project": "example", "target": "agent:second"}
    new = payload(call(client, paired, "routes_set", args))
    assert new["agent"] == "paired" and new["project"] == "example"
    updated = payload(call(client, paired, "routes_set", {**args, "note": "Updated by the source agent"}))
    assert updated["id"] == new["id"] and updated["note"]
    assert payload(call(client, paired, "routes_set", {**args, "target": "agent:paired"}))["target"]["agent"] == "paired"
    removed = payload(call(client, paired, "routes_remove", {"agent": "paired", "project": "example", "block": "agent"}))
    assert removed["removed"]["id"] == new["id"]
    assert [r.id for r in core.routes.list()] == [owner["id"], foreign["id"]]
    assert payload(call(client, paired, "project_list"))["projects"][0]["name"] == "Example"
    for change in ({"name": "Other"}, {"new_slug": "other"}, {"archived": True}, {"private": True}, {"links": {"repos": []}}):
        assert OWNER_REQUIRED in error_text(call(client, paired, "project_update", {"slug": "example", **change}))
    assert not core.projects.get("example").archived and not core.projects.get("example").private


@pytest.mark.parametrize("link", ["bogus", "capy_project"])
def test_project_update_refuses_unknown_link_keys(make, link):
    core, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    core.projects.create(slug="example", name="Example")
    result = call(client, STATIC_KEY, "project_update", {"slug": "example", "links": {"repos": ["example/repo"], link: SECRET}})
    assert error_text(result) == "Choose a supported project link key."
    [row] = core.audit.recent(outcome="refused")
    assert (row["target"], row["detail"]) == ("project_update", "Choose a supported project link key.")
    assert SECRET not in str(row) and core.projects.get("example").links == {}


def test_paired_agent_route_honors_target_grant(server, paired):
    core, client, _ = server
    core.cfg.key_targets["paired"] = ("agent:second",)
    args = {"agent": "paired", "target": "agent:claude"}
    assert error_text(call(client, paired, "routes_set", args)) == TARGET_REQUIRED
    assert payload(call(client, paired, "routes_set", {**args, "target": "agent:second"}))["target"]["agent"] == "second"


def test_paired_mailbox_reply_and_cancel_respect_sender_and_recipient(server):
    core, client, _ = server
    sender, recipient, stranger = (pair(client, who) for who in ("alpha", "beta", "gamma"))
    sent = payload(call(client, sender, "send", {"to": "agent:beta", "task": "Check the example"}))
    args = {"conversation": sent["conversation"]}

    assert payload(call(client, recipient, "reply", {**args, "text": "Checking", "final": False}))["delivered"]
    assert error_text(call(client, stranger, "reply", {**args, "text": SECRET})) == CONVERSATION_REQUIRED
    assert error_text(call(client, recipient, "cancel", args)) == CONVERSATION_REQUIRED
    assert payload(call(client, sender, "cancel", args)) == {**args, "status": "cancelled"}
    late = payload(call(client, recipient, "reply", {**args, "text": "Finished"}))
    assert late["status"] == "cancelled" and not late["delivered"]

    refusals = core.audit.recent(action="tool_refused")
    assert [(row["source"], row["target"], row["detail"]) for row in refusals] == [
        ("beta", "cancel", CONVERSATION_REQUIRED),
        ("gamma", "reply", CONVERSATION_REQUIRED),
    ]
    assert SECRET not in json.dumps(refusals)


def test_to_owner_explains_refusal_on_an_agent_message(make):
    core, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    sender, recipient = (pair(client, who) for who in ("alpha", "beta"))
    sent = payload(call(client, sender, "send", {"to": "agent:beta", "task": "Check the example"}))
    response = call(client, recipient, "reply", {"conversation": sent["conversation"], "text": "A note", "to_owner": True})
    assert error_text(response) == "Use a forwarded Slack message or a triage conversation with owner notes enabled."
    assert core.inbox.read(owner="owner")["items"] == []


def test_general_memory_confirmation_refusal_does_not_audit_arguments(server):
    core, client, _ = server
    core.cfg.key_scopes["general-agent"] = ("general",)
    paired = pair(client, "general-agent")
    assert "confirm_general" in error_text(call(client, paired, "memory_write", {
        "scope": "general", "namespace": "notes", "title": "Fixture", "body": SECRET,
    }))
    for rows in (core.audit.recent(outcome="refused"),
                 client.get("/api/audit", headers=OWNER, params={"action": "tool_refused"}).json()["items"]):
        assert [(row["target"], row["detail"], row["outcome"]) for row in rows] == [
            ("memory_write", CONFIRMATION_REQUIRED, "refused")
        ]
        assert SECRET[:18] not in json.dumps(rows) and paired[:12] not in json.dumps(rows)


def test_ordinary_tool_errors_do_not_emit_policy_refusals(server, paired):
    core, client, _ = server
    assert "Provide" in error_text(call(client, paired, "memory_write"))
    assert "title is required" in error_text(call(client, paired, "memory_write", {
        "scope": "agent:paired", "namespace": "notes", "title": "", "body": SECRET,
    }))
    assert core.audit.recent(action="tool_refused") == []


def test_provider_conversation_refusal_audits_without_the_body(server, paired):
    core, client, _ = server
    result = call(client, paired, "github_comment", {"repo": "example/repo", "number": 1, "body": SECRET})
    assert "Use your assist or triage conversation handle" in error_text(result)
    [row] = core.audit.recent(outcome="refused")
    assert (row["destination"], row["action"], row["detail"]) == ("github", "comment", CONVERSATION_REQUIRED)
    assert SECRET[:18] not in json.dumps(row) and paired[:12] not in json.dumps(row)
