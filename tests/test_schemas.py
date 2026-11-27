from conftest import STATIC_KEY, rpc

CORE = {"memory_write", "memory_search", "memory_read", "memory_list", "memory_delete", "context_pack", "project_list", "project_create",
        "project_update", "project_sync", "memory_move", "memory_promote", "routes_list", "routes_set", "routes_remove", "routes_resolve", "switchboard_status", "agent_send",
        "modes_status", "space_mode"}
GENERIC = {"send", "reply", "wait", "inbox", "get_message", "status", "result", "cancel", "targets", "connect", "connect_code", "connect_list", "connect_revoke", "visibility_list", "visibility_set"}
CAPY = {"capy_delegate", "capy_wait", "capy_inbox", "capy_status", "capy_list", "capy_reply", "capy_result", "capy_captain_digest", "capy_captains", "capy_cancel"}


def tools(c):
    return {t["name"]: t for t in rpc(c, STATIC_KEY, "tools/list").json()["result"]["tools"]}


def test_tool_set_and_schemas(make):
    _, c = make()
    t = tools(c)
    assert set(t) == CORE | GENERIC | CAPY
    for name, tool in t.items():
        assert 40 <= len(tool["description"]) <= 700, name
        assert tool["inputSchema"]["type"] == "object"
        assert "ctx" not in tool["inputSchema"].get("properties", {}), name
        assert "annotations" in tool, name
    d = t["capy_delegate"]["inputSchema"]
    assert d["required"] == ["task"] and {"task", "project", "title", "target", "repos", "files", "new_thread", "wait_s"} == set(d["properties"])
    assert set(t["memory_write"]["inputSchema"]["required"]) == {"scope", "namespace", "title", "body"}
    assert t["get_message"]["inputSchema"]["required"] == ["message_id"]
    assert "capy_wait" in t["capy_delegate"]["description"]


def test_capy_tools_only_with_the_capy_destination(make):
    _, c = make(config={"public_url": "https://switchboard.example.test", "auth": {"owner_emails": ["o@example.test"]}})
    assert set(tools(c)) == CORE | GENERIC


def test_annotations_mark_reads_and_writes(make):
    _, c = make()
    t = tools(c)
    for name in ("memory_search", "memory_read", "memory_list", "capy_status", "capy_result", "capy_list", "capy_captain_digest", "context_pack",
                 "switchboard_status", "routes_list", "routes_resolve", "capy_wait", "capy_inbox", "get_message", "modes_status"):
        assert t[name]["annotations"]["readOnlyHint"] is True, name
    for name in ("memory_write", "memory_delete", "capy_delegate", "capy_reply", "routes_set", "routes_remove", "connect", "space_mode"):
        assert t[name]["annotations"]["readOnlyHint"] is False, name
    assert t["memory_delete"]["annotations"]["destructiveHint"] is True


def test_initialize_advertises_instructions(make):
    _, c = make()
    r = rpc(c, STATIC_KEY, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}).json()
    assert "capy_wait" in r["result"]["instructions"] and r["result"]["serverInfo"]["name"] == "switchboard"
