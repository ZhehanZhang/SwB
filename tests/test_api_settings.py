import time
from importlib.metadata import version

import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY, base_config, default_env
from switchboard import api_runtime
from switchboard.config import ConfigError


def test_settings_is_owner_only_and_reports_runtime_facts_without_secrets(make, monkeypatch):
    owner_key = "swb_owner_" + "test-only-character-sequence"
    core, client = make(env={**default_env(), "SWITCHBOARD_KEY_OWNER": owner_key})
    monkeypatch.setattr(api_runtime.socket, "gethostname", lambda: "settings-test-host")
    monkeypatch.setattr(api_runtime, "_STARTED_AT", time.monotonic() - 120)

    for headers in ({}, {"Authorization": f"Bearer {STATIC_KEY}"}):
        response = client.get("/api/settings", headers=headers)
        assert response.status_code == 401 and response.json() == {"error": "signed_out"}
        assert response.headers["cache-control"] == "no-store"

    response = client.get("/api/settings", headers=OWNER)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload == {
        "version": version("swb-one"),
        "mcp_url": f"{PUBLIC}/mcp",
        "data_dir": str(core.data_dir.resolve()),
        "owner_key_masked": "••••••••",
        "host": "settings-test-host",
        "uptime_seconds": payload["uptime_seconds"],
    }
    assert payload["uptime_seconds"] >= 120
    assert owner_key not in response.text


@pytest.mark.parametrize("base,endpoint", [
    (None, "/mcp"),
    ("https://user:fixture-password@example.test", "/mcp"),
    ("https://example.test?token=fixture-password", "/mcp"),
    ("https://example.test:invalid", "/mcp"),
])
def test_settings_omits_unavailable_or_unsafe_values(make, monkeypatch, base, endpoint):
    if base is not None:
        with pytest.raises(ConfigError, match="public_url"):
            make(base_config(public_url=base))
    core, client = make(base_config(public_url=None))
    monkeypatch.setattr(core.cfg, "public_url", base)
    response = client.get("/api/settings", headers=OWNER)
    assert response.status_code == 200
    assert response.json()["owner_key_masked"] is None
    assert response.json()["mcp_url"] == endpoint
    assert "fixture-password" not in response.text


def test_settings_does_not_invent_an_mcp_endpoint_for_disabled_mcp(make):
    _, client = make(base_config(version=2, blocks={}))
    response = client.get("/api/settings", headers=OWNER)
    assert response.status_code == 200
    assert response.json()["mcp_url"] is None
