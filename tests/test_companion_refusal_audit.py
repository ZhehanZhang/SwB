from __future__ import annotations

from starlette.testclient import TestClient

from conftest import PUBLIC, Clock, base_config, new_core
from switchboard.app import create_app
from switchboard.audit import REFUSAL_AUDIT_INTERVAL_S


def test_refusal_audit_deduplicates_each_client_and_reason_for_one_interval(make):
    clock = Clock()
    core, _ = make(clock=clock)
    refusal = {"origin": "companion", "destination": "core", "action": "companion_emit", "target": "cmp_test"}

    def record(client, detail):
        core.audit.record_refusal(**refusal, client=client, detail=detail)

    def cases():
        return [(row["client"], row["detail"]) for row in core.audit.recent(action="companion_emit", outcome="refused")]

    first = ("192.0.2.1", "First reason.")
    other_ip = ("192.0.2.2", first[1])
    other_reason = (first[0], "Second reason.")
    for case in (first, other_ip, other_reason, first, other_ip, other_reason):
        record(*case)
    clock.t += REFUSAL_AUDIT_INTERVAL_S - 1
    record(*first)
    assert cases() == [other_reason, other_ip, first]
    clock.t += 1
    record(*first)
    assert cases() == [first, other_reason, other_ip, first]


def test_wrong_companion_keys_audit_each_trusted_client_address(tmp_path, capy):
    core = new_core(tmp_path, capy, config=base_config(version=2, blocks={"mcp": {}}),
                    env={"SWB_TRUSTED_PROXIES": "192.0.2.0/24"})
    with TestClient(create_app(core), base_url=PUBLIC, client=("192.0.2.1", 10000)) as client:
        for address in ("198.51.100.1", "198.51.100.2", "198.51.100.1"):
            response = client.post("/companion/hello", headers={
                "X-Forwarded-For": address,
                "Authorization": "Bearer swbd_invalid",
            })
            assert response.status_code == 401
        for action in ("auth_failure", "companion_refused"):
            rows = core.audit.recent(action=action, outcome="refused")
            assert [row["client"] for row in rows] == ["198.51.100.2", "198.51.100.1"]
            assert {row["target"] for row in rows} == {"/companion"}


def test_owner_companion_api_refusal_audits_request_path(make):
    core, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    code = core.companions.mint(PUBLIC, origin="test", source="test", client="test")["code"]
    paired = client.post("/companion/pair", json={"code": code, "name": "Device", "platform": "linux", "capabilities": []}).json()
    headers = {"Authorization": f"Bearer {paired['device_key']}"}
    for path in ("/healthz", "/api/companions", "/api/companions"):
        assert client.get(path, headers=headers).status_code == 401
    rows = core.audit.recent(action="companion_refused", outcome="refused")
    assert [(row["target"], row["ref"]) for row in rows] == [
        (paired["companion_id"], "/api/companions"),
        (paired["companion_id"], None),
    ]
