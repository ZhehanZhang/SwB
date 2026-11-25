from conftest import STATIC_KEY, rpc

EXPECTED = {
    "memory_write", "memory_search", "memory_read", "memory_list", "memory_delete", "context_pack",
    "capy_delegate", "capy_status", "capy_list", "capy_reply", "capy_result", "capy_captain_digest", "bridge_status",
}


def tools(c):
    return {t["name"]: t for t in rpc(c, STATIC_KEY, "tools/list").json()["result"]["tools"]}


def test_tool_set_and_schemas(make):
    _, c = make()
    t = tools(c)
    assert set(t) == EXPECTED
    for name, tool in t.items():
        assert 40 <= len(tool["description"]) <= 600, name
        assert tool["inputSchema"]["type"] == "object"
        assert "ctx" not in tool["inputSchema"].get("properties", {}), name
        assert "annotations" in tool, name
    d = t["capy_delegate"]["inputSchema"]
    assert d["required"] == ["task"]
    assert {"task", "title", "target", "repos", "files", "crew"} == set(d["properties"])
    assert set(t["memory_write"]["inputSchema"]["required"]) == {"namespace", "title", "body"}
    assert t["memory_write"]["inputSchema"]["properties"]["mode"]["enum"] == ["replace", "append"]
    assert t["capy_list"]["inputSchema"]["properties"]["scope"]["enum"] == ["delegated", "captain", "project"]


def test_annotations_mark_reads_and_writes(make):
    _, c = make()
    t = tools(c)
    for name in ("memory_search", "memory_read", "memory_list", "capy_status", "capy_result", "capy_list", "capy_captain_digest", "context_pack", "bridge_status"):
        assert t[name]["annotations"]["readOnlyHint"] is True, name
    for name in ("memory_write", "memory_delete", "capy_delegate", "capy_reply"):
        assert t[name]["annotations"]["readOnlyHint"] is False, name
    assert t["memory_delete"]["annotations"]["destructiveHint"] is True


def test_initialize_advertises_instructions(make):
    _, c = make()
    r = rpc(c, STATIC_KEY, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}).json()
    assert "capy_delegate" in r["result"]["instructions"]
