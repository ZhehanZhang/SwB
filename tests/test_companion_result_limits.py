from __future__ import annotations

import asyncio
import json
from contextlib import aclosing

import pytest

from conftest import STATIC_KEY, acall, base_config, default_env, error_text, payload


@pytest.mark.parametrize("drop", [False, True])
async def test_companion_result_limit_reaches_device_owner_and_audit(amake, caplog, drop):
    caplog.set_level("INFO", logger="switchboard.audit")
    core, client = await amake(config=base_config(version=2, blocks={"mcp": {}}),
                               env={**default_env(), "SWB_TRUSTED_PROXIES": "127.0.0.1/32"})
    code = payload(await acall(client, STATIC_KEY, "companion_code"))["code"]
    capability, action = ("drop", "get") if drop else ("demo", "work")
    paired = (await client.post("/companion/pair", json={
        "code": code, "name": "Device", "platform": "linux",
        "capabilities": [{"id": capability, "version": "1", "title": "Demo", "actions": [action]}],
    })).json()
    device = paired["companion_id"]
    address = "192.0.2.1"
    headers = {"Authorization": f"Bearer {paired['device_key']}", "X-Forwarded-For": address}
    async with aclosing(core.companions.events(device, 0.01)) as stream, asyncio.timeout(5):
        assert await anext(stream) == ("ping", "{}")
        owner = asyncio.create_task(acall(client, STATIC_KEY, "companion_invoke", {
            "companion": device, "capability": capability, "action": action,
        }))
        async for event, data in stream:
            if event == "job":
                break
        result = {"name": "sample.txt", "content_b64": "a" * (2 * 1024 * 1024)} if drop else {"text": "é" * (1024 * 1024 // 2)}
        response = await client.post("/companion/results", headers=headers, json={
            "job_id": json.loads(data)["job_id"], "ok": True, "result": result,
        })
        owner_result = await owner
    refusals = core.audit.recent(action="companion_result", outcome="refused")
    if drop:
        assert response.status_code == 200 and response.json() == {"ok": True}
        assert payload(owner_result)["result"] == result
        assert refusals == []
    else:
        reason = "Companion output exceeds 1 MiB. Request less data."
        assert response.status_code == 413 and response.json() == {"error": reason}
        assert reason in error_text(owner_result)
        [refusal] = refusals
        assert (refusal["target"], refusal["client"], refusal["ref"], refusal["detail"]) == (device, address, "demo.work", reason)
        [entry] = [record.message for record in caplog.records if record.message.startswith("core.companion_result refused")]
        assert f"({address}) -> {device} demo.work {reason}" in entry
        [invoke] = core.audit.recent(action="companion_invoke")
        assert invoke["outcome"] == "failed"
