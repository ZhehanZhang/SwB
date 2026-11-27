import json
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from conftest import CAPTAIN, CAPTAIN2, OWNER, STATIC_KEY, base_config, call, error_text, payload, rpc
from switchboard.blocks import BLOCKS, BlockError
from switchboard.blocks.mcp import PRINCIPAL_SCOPE, READ
from switchboard.blocks.mcp.policy import CONVERSATION_NOT_FOUND, MEMORY_NOT_FOUND, MESSAGE_NOT_FOUND, READ_ROLES, REFERENCE_ARGS

MAILBOX_TOOLS = {
    "get_message": "message_id", "status": "conversation", "result": "conversation", "inbox": "conversation", "wait": "conversation",
    "capy_wait": "handle", "capy_inbox": "handle",
}
PROVIDER_TOOLS = {"capy_result": "thread_id", "capy_status": "thread_id", "status": "conversation", "result": "conversation"}
PRIVATE_TEXT = "Only the conversation participants can read this"


def pair(client, name):
    code = payload(call(client, STATIC_KEY, "connect_code", {"agent": name}))["code"]
    response = client.post("/connect", json={"code": code})
    assert response.status_code == 200
    return response.json()["key"]


@pytest.fixture
def workspace(make):
    cfg = base_config()
    cfg["auth"]["key_scopes"] = {"scoped": ["project:example"]}
    core, client = make(cfg)
    core.projects.create(slug="example", name="Example")
    core.projects.create(slug="private", name="Private")
    keys = {name: pair(client, name) for name in ("alpha", "beta", "gamma", "scoped")}
    return core, client, keys


def access(client, token, name, args):
    result = call(client, token, name, args)
    assert not result.get("isError"), result
    return payload(result)


def mailbox(client, keys, to="beta", project=None):
    return access(client, keys["alpha"], "send", {"to": f"agent:{to}", "task": PRIVATE_TEXT, "project": project})["conversation"]


def assert_refused(core, client, keys, name, args, missing, reason):
    for identity in ("gamma", "scoped"):
        assert error_text(call(client, keys[identity], name, args)) == reason
        assert error_text(call(client, keys[identity], name, missing)) == reason
    rows = core.audit.recent(action="tool_refused", limit=4)
    assert [(row["source"], row["target"], row["detail"]) for row in rows] == [
        (source, name, reason) for source in ("scoped", "scoped", "gamma", "gamma")
    ]
    assert all(value not in json.dumps(rows) for value in [PRIVATE_TEXT, *args.values()] if isinstance(value, str))
    stranger = rpc(client, "invalid-fixture-key", "tools/call", {"name": name, "arguments": args})
    assert stranger.status_code == 401
    assert core.audit.recent(action="auth_failure")[0]["outcome"] == "refused"


async def test_every_id_taking_read_tool_has_a_role(make):
    core, _ = make(base_config(version=2, blocks={use: {"use": use} for use in BLOCKS}))
    mcp = core.blocks["mcp"].mcp
    declared = {t.name: t for t in await mcp.list_tools()}
    id_reads = {name for name, tool in declared.items()
                if tool.annotations and tool.annotations.read_only_hint and REFERENCE_ARGS.intersection(tool.input_schema["properties"])}
    assert id_reads == {name for name, (_, arg) in READ_ROLES.items() if arg in REFERENCE_ARGS}
    assert all(name in READ_ROLES or name in mcp.roles for name, tool in declared.items()
               if REFERENCE_ARGS.intersection(tool.input_schema["properties"]))
    assert all(arg is None or arg in declared[name].input_schema["properties"] for name, (_, arg) in READ_ROLES.items())

    @mcp.tool(annotations=READ)
    def unclassified_read(message_id: str):
        raise AssertionError("A read without a role must not run")

    context = SimpleNamespace(request_context=SimpleNamespace(
        request=SimpleNamespace(scope={PRINCIPAL_SCOPE: core.auth.verify_bearer(STATIC_KEY)})))
    with pytest.raises(ToolError, match="Conversation not found"):
        await mcp.call_tool("unclassified_read", {"message_id": "fixture"}, context)


@pytest.mark.parametrize(("name", "field"), MAILBOX_TOOLS.items())
def test_mailbox_read_policy_for_owner_participants_scoped_and_stranger(workspace, name, field):
    core, client, keys = workspace
    handle = mailbox(client, keys)
    access(client, keys["beta"], "reply", {"conversation": handle, "text": "An interim reply", "final": False})
    original = core.store.one("SELECT id FROM inbox WHERE handle = ?", (handle,))["id"]
    identifier = core.inbox.message_id(original) if name == "get_message" else handle
    extra = {"timeout_s": 0} if name.endswith("wait") else {}
    args = {**extra, field: identifier}
    for key in (keys["alpha"], keys["beta"], STATIC_KEY):
        result = access(client, key, name, args)
        if name == "get_message":
            assert result["text"] == PRIVATE_TEXT and result["handle"] == handle
    missing = "msg_9223372036854775807" if name == "get_message" else "h_missing"
    reason = MESSAGE_NOT_FOUND if name == "get_message" else CONVERSATION_NOT_FOUND
    assert_refused(core, client, keys, name, args, {**extra, field: missing}, reason)


@pytest.mark.parametrize(("name", "field"), PROVIDER_TOOLS.items())
@pytest.mark.parametrize("external", [False, True])
def test_provider_read_requires_local_conversation_or_owner(workspace, name, field, external):
    core, client, keys = workspace
    handle = core.inbox.open_handle(owner="alpha", destination="capy", ref=CAPTAIN, project="example", title="Fixture", meta={})
    args = {field: CAPTAIN if external else handle}
    access(client, keys["alpha"], name, args)
    access(client, STATIC_KEY, name, args)
    assert error_text(call(client, keys["beta"], name, args)) == CONVERSATION_NOT_FOUND
    assert_refused(core, client, keys, name, args, {field: "jam_missing" if external else "h_missing"}, CONVERSATION_NOT_FOUND)


def test_scope_limits_own_conversation_and_inbox_feeds(workspace):
    core, client, keys = workspace
    outside = mailbox(client, keys, to="scoped", project="private")
    inside = mailbox(client, keys, to="scoped", project="example")
    for handle, allowed in ((outside, False), (inside, True)):
        mid = core.store.one("SELECT id FROM inbox WHERE handle = ?", (handle,))["id"]
        result = call(client, keys["scoped"], "get_message", {"message_id": core.inbox.message_id(mid)})
        assert result.get("isError", False) == (not allowed)
        if not allowed:
            assert error_text(result) == MESSAGE_NOT_FOUND
    headers = {"Authorization": f"Bearer {keys['scoped']}"}
    batch = client.get("/inbox/poll", headers=headers, params={"after": 0, "timeout": 0})
    assert [item["handle"] for item in batch.json()["items"]] == [inside]
    assert [item["handle"] for item in access(client, keys["scoped"], "inbox", {"since_cursor": 0})["items"]] == [inside]


def test_rest_and_inbox_poll_cannot_read_someone_elses_message(workspace):
    _, client, keys = workspace
    handle = mailbox(client, keys)
    item = access(client, keys["beta"], "inbox", {"since_cursor": 0})["items"][0]
    assert item["handle"] == handle
    for token in (*keys.values(), "invalid-fixture-key"):
        headers = {"Authorization": f"Bearer {token}"}
        foreign = client.get(f"/api/conversations/{handle}", headers=headers)
        missing = client.get("/api/conversations/h_missing", headers=headers)
        assert foreign.status_code == missing.status_code == 401
        assert foreign.json() == missing.json()
    assert PRIVATE_TEXT in client.get(f"/api/conversations/{handle}", headers=OWNER).text
    gamma = {"Authorization": f"Bearer {keys['gamma']}"}
    assert client.get("/inbox/poll", headers=gamma, params={"after": item["cursor"] - 1, "timeout": 0}).json()["items"] == []
    assert client.get("/inbox/poll", headers=gamma, params={"agent": "beta", "timeout": 0}).status_code == 403
    assert client.get("/inbox/events", headers=gamma, params={"agent": "beta"}).status_code == 403


@pytest.mark.parametrize("name", ["memory_read", "memory_search", "memory_list", "context_pack"])
def test_memory_reads_enforce_scopes_and_audit_refusals(workspace, name):
    core, client, keys = workspace
    ids = {slug: core.memory.write(source="owner", scope=f"project:{slug}", namespace="notes", title="Fixture",
                                   body=PRIVATE_TEXT, key="fixture")["id"] for slug in ("example", "private")}

    def args(slug):
        if name == "memory_read":
            return {"id": ids[slug]}
        return {"project": slug, **({"query": "Fixture"} if name == "memory_search" else {})}

    for key in (STATIC_KEY, keys["alpha"], keys["beta"]):
        access(client, key, name, args("private"))
    access(client, keys["scoped"], name, args("example"))
    assert error_text(call(client, keys["scoped"], name, args("private"))) == MEMORY_NOT_FOUND
    if name == "memory_read":
        assert error_text(call(client, keys["scoped"], name, {"id": "missing"})) == MEMORY_NOT_FOUND
        keyed = {"namespace": "notes", "key": "fixture"}
        assert error_text(call(client, keys["scoped"], name, {"scope": "project:private", **keyed})) == MEMORY_NOT_FOUND
        assert error_text(call(client, keys["scoped"], name, {"scope": "project:example", **keyed, "key": "missing"})) == MEMORY_NOT_FOUND
    row = core.audit.recent(action="tool_refused")[0]
    assert (row["source"], row["target"], row["detail"]) == ("scoped", name, MEMORY_NOT_FOUND)
    assert PRIVATE_TEXT not in json.dumps(row) and ids["private"] not in json.dumps(row)
    assert rpc(client, "invalid-fixture-key", "tools/call", {"name": name, "arguments": args("private")}).status_code == 401


@pytest.mark.parametrize("scope", ["delegated", "captain", "project"])
def test_capy_work_lists_only_the_callers_conversations(workspace, monkeypatch, scope):
    core, client, keys = workspace
    api = core.blocks["capy"].api
    core.projects.update("example", links={"capy_project": "project_fixture"})
    for owner, ref in (("alpha", CAPTAIN), ("beta", CAPTAIN2)):
        core.inbox.open_handle(owner=owner, destination="capy", ref=ref, project="example", title=f"{owner} delegation", meta={})
    entries = [{"id": CAPTAIN, "title": "First conversation"}, {"id": CAPTAIN2, "title": PRIVATE_TEXT}]

    async def crew(_):
        return entries

    async def threads(_, **_kwargs):
        return {"items": entries}

    monkeypatch.setattr(api, "captain_crew", crew)
    monkeypatch.setattr(api, "list_threads", threads)
    args = {"scope": scope, "project": "example"}
    mine = access(client, keys["alpha"], "capy_list", args)["items"]
    assert [item.get("thread_id", item.get("id")) for item in mine] == [CAPTAIN]
    assert access(client, keys["gamma"], "capy_list", args)["items"] == []
    assert len(access(client, STATIC_KEY, "capy_list", args)["items"]) == 2


def test_capy_project_list_finds_the_callers_threads_past_the_provider_page(workspace, monkeypatch):
    core, client, keys = workspace
    api = core.blocks["capy"].api
    core.projects.update("example", links={"capy_project": "project_fixture"})
    core.inbox.open_handle(owner="alpha", destination="capy", ref=CAPTAIN, project="example", title="alpha delegation", meta={})
    entries = [{"id": CAPTAIN2, "title": PRIVATE_TEXT}, {"id": CAPTAIN, "title": "First conversation"}]

    async def threads(_, limit=20):
        return {"items": entries[:limit]}

    async def thread(ref):
        return next(t for t in entries if t["id"] == ref)

    monkeypatch.setattr(api, "list_threads", threads)
    monkeypatch.setattr(api, "get_thread", thread)
    args = {"scope": "project", "project": "example", "limit": 1}
    mine = access(client, keys["alpha"], "capy_list", args)["items"]
    assert [item["id"] for item in mine] == [CAPTAIN]
    assert PRIVATE_TEXT not in json.dumps(mine)
    assert [item["id"] for item in access(client, STATIC_KEY, "capy_list", args)["items"]] == [CAPTAIN2]


def test_briefing_filters_open_work_and_captain_digest(workspace):
    core, client, keys = workspace
    for owner, title in (("alpha", "Alpha delegation"), ("beta", PRIVATE_TEXT)):
        core.inbox.open_handle(owner=owner, destination="capy", ref=CAPTAIN, project="example", title=title, meta={})
    args = {"project": "example"}
    mine = access(client, keys["alpha"], "context_pack", args)
    owner = access(client, STATIC_KEY, "context_pack", args)
    assert "Alpha delegation" in mine and PRIVATE_TEXT not in mine
    assert PRIVATE_TEXT in owner
    digest = {"captain": CAPTAIN}
    assert "owner's sign-in" in error_text(call(client, keys["alpha"], "capy_captain_digest", digest))
    assert access(client, STATIC_KEY, "capy_captain_digest", digest)["captain_id"] == CAPTAIN


def test_paired_status_hides_owner_key_and_follower_details(workspace):
    core, client, keys = workspace
    for name in ("alpha", "beta"):
        core.inbox.open_handle(owner=name, destination="capy", ref=CAPTAIN, project=None, title="Fixture", meta={})
    paired = access(client, keys["alpha"], "switchboard_status", {})
    owner = access(client, STATIC_KEY, "switchboard_status", {})
    assert paired["connected"] == ["alpha"] and paired["static_keys"] == []
    assert paired["destinations"]["capy"] == {"online": True, "open_handles": 1}
    assert len(owner["connected"]) > 1 and owner["static_keys"]
    assert {"following", "send_keys"} <= owner["destinations"]["capy"].keys()


def test_chat_and_slack_status_keep_per_agent_grants(make):
    core, client = make(base_config(version=2, blocks={
        "mcp": {},
        "discord": {"spaces": {"one": {"guild_id": "101"}, "two": {"guild_id": "102"}}, "agents": {"alpha": ["one"]}},
        "slack": {"workspaces": {
            "one": {"team_id": "TFIXTURE01", "identities": {"bot": {"token_env": "TEST_ONE_BOT"}}},
            "two": {"team_id": "TFIXTURE02", "identities": {"bot": {"token_env": "TEST_TWO_BOT"}}},
        }, "agents": {"alpha": {"workspaces": {"one": ["bot"]}}}},
    }))
    alpha, gamma = pair(client, "alpha"), pair(client, "gamma")
    assert set(access(client, alpha, "discord_status", {})["spaces"]) == {"one"}
    assert access(client, gamma, "discord_status", {})["spaces"] == {}
    assert "discovered" in access(client, STATIC_KEY, "discord_status", {})
    paired = access(client, alpha, "switchboard_status", {})["destinations"]["slack"]
    assert set(paired["workspaces"]) == {"one"} and "pending_approvals" not in paired
    assert "owner's sign-in" in error_text(call(client, alpha, "discord_provision"))
    with pytest.raises(BlockError, match="provisioning is the owner"):
        client.portal.call(core.invoke, core.auth.verify_bearer(alpha), "discord", core.blocks["discord"].action("provision"), {})


def test_dots_result_follows_participant_and_owner_policy(make):
    core, client = make(base_config(version=2, blocks={"mcp": {}, "dots": {"delivery": "mailbox"}}))
    alpha, gamma = pair(client, "alpha"), pair(client, "gamma")
    handle = core.inbox.open_handle(owner="alpha", destination="dots", ref="fixture", project=None, title="Fixture", meta={})
    client.portal.call(core.inbox.add, handle, "reply", {"text": PRIVATE_TEXT})
    args = {"conversation": handle}
    for key in (alpha, STATIC_KEY):
        assert access(client, key, "result", args)["final"]["text"] == PRIVATE_TEXT
    assert error_text(call(client, gamma, "result", args)) == CONVERSATION_NOT_FOUND


@pytest.mark.parametrize("tool", ["send", "agent_send", "routes_set"])
def test_target_setting_narrows_a_default_paired_agents_mailbox_access(workspace, tool):
    core, client, keys = workspace
    args = {
        "send": {"to": "agent:beta", "task": "Fixture"},
        "agent_send": {"to": "beta", "text": "Fixture"},
        "routes_set": {"agent": "alpha", "target": "agent:beta"},
    }[tool]
    access(client, keys["alpha"], tool, args)
    core.cfg.key_targets["alpha"] = ("agent:gamma",)
    assert error_text(call(client, keys["alpha"], tool, args))


def test_missing_limits_keep_default_memory_but_do_not_create_owner_access(workspace):
    core, client, keys = workspace
    p = core.auth.verify_bearer(keys["alpha"])
    assert p.scopes is p.tools is p.targets is None and not p.is_unrestricted_owner
    for name, args in (("connect_list", {}), ("visibility_list", {}), ("login_link", {})):
        assert "connected agent" in error_text(call(client, keys["alpha"], name, args))
    entry = core.memory.write(source="owner", scope="general", namespace="notes", title="General",
                              body="Shared workspace facts", confirm_general=True)
    assert access(client, keys["alpha"], "memory_read", {"id": entry["id"]})["body"] == "Shared workspace facts"


@pytest.mark.parametrize("name", ["visibility_list", "connect_list"])
def test_admin_read_denials_use_the_policy_audit(workspace, name):
    core, client, keys = workspace
    assert access(client, STATIC_KEY, name, {})
    for caller in ("alpha", "scoped"):
        assert "Use the owner's sign-in to call this tool." in error_text(call(client, keys[caller], name))
    for rows in (core.audit.recent(action="tool_refused"),
                 client.get("/api/audit", headers=OWNER, params={"action": "tool_refused"}).json()["items"]):
        assert [(row["source"], row["target"], row["detail"]) for row in rows] == [
            (caller, name, "Use the owner's sign-in to call this tool.") for caller in ("scoped", "alpha")
        ]
        assert all(keys[caller][:12] not in json.dumps(rows) for caller in ("scoped", "alpha"))
