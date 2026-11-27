from mcp_types.methods import validate_server_result

from conftest import CAPTAIN, HEADERS, STATIC_KEY, TASKS, acall, arpc, base_config, eventually, payload, rpc_body

LEGACY_HEADERS = {**HEADERS, "Authorization": f"Bearer {STATIC_KEY}", "MCP-Protocol-Version": "2025-11-25"}


async def test_modern_send_task_returns_a_complete_tool_result(amake, capy):
    core, client = await amake()
    discovery = (await arpc(client, STATIC_KEY, "server/discover", modern=True)).json()["result"]
    assert discovery["capabilities"]["extensions"][TASKS] == {}

    task = await acall(client, STATIC_KEY, "send", {"task": "Check the sample"}, tasks=True)
    assert task["resultType"] == "task" and task["status"] == "working"
    task_id = task["taskId"]
    await eventually(lambda: capy.streams(CAPTAIN))
    capy.ack(CAPTAIN)
    capy.emit(CAPTAIN, "transcript", {"id": "x", "source": "assistant", "text": "Sample checked."}, id="01B")
    capy.emit(CAPTAIN, "status", {"threadId": CAPTAIN, "status": "idle"})
    await eventually(lambda: not core.inbox.handle(task_id)["open"])

    params = {"taskId": task_id}
    done = (await arpc(client, STATIC_KEY, "tasks/get", params, modern=True)).json()["result"]
    assert done["resultType"] == "complete" and done["status"] == "completed"
    validate_server_result("tools/call", "2026-07-28", done["result"])
    assert done["result"]["resultType"] == "complete"
    assert payload(done["result"])["reply"] == "Sample checked."
    legacy = (await client.post("/mcp", headers=LEGACY_HEADERS, content=rpc_body("tasks/get", params))).json()
    assert legacy["error"]["code"] == -32601


async def test_legacy_and_mailbox_sends_return_conversations(amake):
    _, client = await amake()
    params = {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}
    init = (await arpc(client, STATIC_KEY, "initialize", params)).json()["result"]
    assert init["protocolVersion"] == "2025-11-25"
    assert "tasks" not in init["capabilities"] and "extensions" not in init["capabilities"]
    tools = (await client.post("/mcp", headers=LEGACY_HEADERS, content=rpc_body("tools/list", {}))).json()["result"]["tools"]
    assert "execution" not in next(tool for tool in tools if tool["name"] == "send")
    args = {"task": "Check the sample", "to": "agent:claude-code"}
    body = rpc_body("tools/call", {"name": "send", "arguments": args, "task": {}})
    legacy = (await client.post("/mcp", headers=LEGACY_HEADERS, content=body)).json()["result"]
    assert "task" not in legacy and payload(legacy)["conversation"].startswith("h_")

    modern = await acall(client, STATIC_KEY, "send", args, tasks=True)
    assert modern["resultType"] == "complete" and payload(modern)["mailbox"] is True


async def test_send_only_block_does_not_advertise_tasks(amake):
    _, client = await amake(base_config(version=2, blocks={"mcp": {}, "muse": {}}))
    discovery = (await arpc(client, STATIC_KEY, "server/discover", modern=True)).json()["result"]
    assert TASKS not in discovery["capabilities"].get("extensions", {})
