import asyncio
import json

import httpx
import uvicorn

from switchboard.app import create_app

from switchboard.origins.mcp import WaitCaps
from conftest import CAPTAIN, CHANNEL_KEY, SECOND_KEY, STATIC_KEY, acall, arpc, base_config, eventually, payload


async def test_capy_wait_returns_early_on_a_stream_event(amake, capy):
    core, c = await amake()
    out = payload(await acall(c, STATIC_KEY, "capy_delegate", {"task": "Do it"}))
    await eventually(lambda: capy.streams(CAPTAIN))
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    wait = asyncio.create_task(acall(c, STATIC_KEY, "capy_wait", {"handle": out["handle"], "timeout_s": 20}))
    await asyncio.sleep(0.1)
    capy.ack(CAPTAIN)
    capy.emit(CAPTAIN, "transcript", {"id": "x", "source": "assistant", "text": "Working on it."}, id="01B")
    got = payload(await wait)
    assert [i["text"] for i in got["items"]] == ["Working on it."] and got["timed_out"] is False and got["handle_status"] == "following"
    assert loop.time() - t0 < 5
    again = payload(await acall(c, STATIC_KEY, "capy_wait", {"handle": out["handle"], "timeout_s": 0.1}))
    assert again["items"] == [] and again["timed_out"] is True and again["next"] == "Check again with wait or inbox."
    owner = payload(await acall(c, STATIC_KEY, "wait", {"conversation": out["handle"], "timeout_s": 0, "owner_facing": True}))
    assert owner["timed_out"] is True and owner["next"].startswith("Tell the owner")


async def test_capy_wait_is_capped_per_agent(amake):
    caps = WaitCaps({"default_s": 60, "max_s": 200, "agents": {"claude-code": {"default_s": 45, "max_s": 55}}})
    assert caps.clamp("claude", None) == 60 and caps.clamp("claude", 999) == 200
    assert caps.clamp("claude-code", None) == 45 and caps.clamp("claude-code", 120) == 55 and caps.clamp("chatgpt", -5) == 0
    core, c = await amake()
    me = payload(await acall(c, STATIC_KEY, "switchboard_status"))
    assert me["capy_wait"] == {"default_s": 60.0, "max_s": 200.0}


async def test_delegate_wait_s_and_inbox(amake, capy):
    core, c = await amake()
    call = asyncio.create_task(acall(c, STATIC_KEY, "capy_delegate", {"task": "Quick one", "wait_s": 10}))
    await eventually(lambda: capy.streams(CAPTAIN))
    capy.ack(CAPTAIN)
    capy.emit(CAPTAIN, "transcript", {"id": "x", "source": "assistant", "text": "Answer"}, id="01B")
    out = payload(await call)
    assert [i["text"] for i in out["replies"]] == ["Answer"]
    inbox = payload(await acall(c, STATIC_KEY, "capy_inbox"))
    assert inbox["items"] == [] and [h["handle"] for h in inbox["open"]] == [out["handle"]]
    assert [i["text"] for i in payload(await acall(c, STATIC_KEY, "capy_inbox", {"since_cursor": 0}))["items"]] == ["Answer"]


async def test_tasks_extension_end_to_end_and_across_restart(amake, capy):
    core, c = await amake()
    disc = (await arpc(c, STATIC_KEY, "server/discover", modern=True)).json()["result"]
    assert "io.modelcontextprotocol/tasks" in disc["capabilities"]["extensions"]
    created = await acall(c, STATIC_KEY, "capy_delegate", {"task": "Long job", "wait_s": 30}, tasks=True)
    assert created["resultType"] == "task" and created["status"] == "working" and created["taskId"].startswith("h_")
    assert created["ttlMs"] == 7 * 86400 * 1000 and created["pollIntervalMs"] > 0
    tid = created["taskId"]
    got = (await arpc(c, STATIC_KEY, "tasks/get", {"taskId": tid}, modern=True)).json()["result"]
    assert got["resultType"] == "complete" and got["status"] == "working" and CAPTAIN in got["statusMessage"]
    assert (await arpc(c, SECOND_KEY, "tasks/get", {"taskId": tid}, modern=True)).json()["error"]["code"] == -32602
    await eventually(lambda: capy.streams(CAPTAIN))
    capy.ack(CAPTAIN)
    capy.emit(CAPTAIN, "transcript", {"id": "x", "source": "assistant", "text": "Report attached."}, id="01B")
    capy.emit(CAPTAIN, "status", {"threadId": CAPTAIN, "status": "idle"})
    await eventually(lambda: not core.inbox.handle(tid)["open"])
    await core._test_close()
    core2, c2 = await amake()
    done = (await arpc(c2, STATIC_KEY, "tasks/get", {"taskId": tid}, modern=True)).json()["result"]
    assert done["status"] == "completed" and done["result"]["isError"] is False
    assert done["result"]["content"][0]["text"].startswith("Report attached.") and done["result"]["structuredContent"]["thread_id"] == CAPTAIN


async def test_without_the_extension_delegate_is_a_plain_result(amake):
    core, c = await amake()
    plain = await acall(c, STATIC_KEY, "capy_delegate", {"task": "x"}, modern=True)
    assert plain.get("resultType") == "complete" and payload(plain)["handle"].startswith("h_")
    other = await acall(c, STATIC_KEY, "memory_list", {}, tasks=True)
    assert other.get("resultType") == "complete"


async def test_tasks_cancel(amake):
    core, c = await amake()
    tid = (await acall(c, STATIC_KEY, "capy_delegate", {"task": "x"}, tasks=True))["taskId"]
    assert (await arpc(c, STATIC_KEY, "tasks/cancel", {"taskId": tid}, modern=True)).json()["result"]["resultType"] == "complete"
    got = (await arpc(c, STATIC_KEY, "tasks/get", {"taskId": tid}, modern=True)).json()["result"]
    assert got["status"] == "cancelled"


async def test_tasks_cancel_honors_the_tool_allowlist(amake):
    cfg = base_config()
    cfg["auth"]["key_tools"] = {"capy": ["capy_delegate"]}
    core, c = await amake(config=cfg)
    tid = (await acall(c, STATIC_KEY, "capy_delegate", {"task": "Test task policy"}, tasks=True))["taskId"]
    response = (await arpc(c, STATIC_KEY, "tasks/cancel", {"taskId": tid}, modern=True)).json()
    assert response["error"]["code"] == -32602
    assert core.inbox.handle(tid)["open"]
    [row] = core.audit.recent(action="tool_refused")
    assert (row["target"], row["detail"]) == ("cancel", "Use a key granted this tool.")


async def test_channel_key_reads_claude_codes_inbox_only(amake, capy):
    core, c = await amake()
    channel = {"Authorization": f"Bearer {CHANNEL_KEY}"}
    head = core.inbox.head()
    poll = asyncio.create_task(c.get("/inbox/poll", params={"after": head, "timeout": 10}, headers=channel))
    await asyncio.sleep(0.1)
    payload(await acall(c, STATIC_KEY, "capy_delegate", {"task": "x"}))
    await eventually(lambda: capy.streams(CAPTAIN))
    capy.ack(CAPTAIN)
    capy.emit(CAPTAIN, "transcript", {"id": "x", "source": "assistant", "text": "Hello from Capy"}, id="01B")
    await eventually(lambda: core.inbox.head() > head)
    sent = payload(await acall(c, STATIC_KEY, "agent_send", {"to": "claude-code", "text": "for claude code"}))
    r = await poll
    assert r.status_code == 200 and [(i["owner"], i["text"]) for i in r.json()["items"]] == [("claude-code", "for claude code")]
    assert sent["delivered"] and [i["text"] for i in payload(await acall(c, STATIC_KEY, "capy_inbox"))["items"]] == ["Hello from Capy"]
    assert (await c.get("/inbox/poll", params={"agent": "capy", "timeout": 0}, headers=channel)).status_code == 403
    assert (await c.post("/mcp", headers={**channel, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                         content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))).status_code == 403
    assert (await c.get("/inbox/poll")).status_code == 401


async def test_inbox_events_streams_items_over_sse(tmp_path, capy):
    from conftest import new_core

    core = new_core(tmp_path, capy)
    server = uvicorn.Server(uvicorn.Config(create_app(core), host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve())
    await eventually(lambda: server.started, timeout=10)
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=base, timeout=20) as c:
            frames = []
            async with c.stream("GET", "/inbox/events", headers={"Authorization": f"Bearer {CHANNEL_KEY}"}) as r:
                assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
                lines = r.aiter_lines()
                async for line in lines:
                    if line.startswith("event: ready"):
                        break
                out = payload(await acall(c, STATIC_KEY, "agent_send", {"to": "claude-code", "text": "pushed"}))
                event_id = None
                async for line in lines:
                    if line.startswith("id: "):
                        event_id = line[4:]
                    if line.startswith("data: ") and event_id:
                        frames.append(json.loads(line[6:]))
                        break
            assert frames[0]["text"] == "pushed" and frames[0]["handle"] == out["handle"] and int(event_id) == frames[0]["cursor"]
            pair = core.connections.mint(agent="claude-code", project=None, client=None, by="owner")
            key = core.connections.redeem(pair["code"])["key"]
            [item] = payload(await acall(c, key, "inbox"))["items"]
            assert item["message_id"] == frames[0]["message_id"]
            looked_up = payload(await acall(c, key, "get_message", {"message_id": item["message_id"]}))
            assert looked_up == item
    finally:
        server.should_exit = True
        await task
