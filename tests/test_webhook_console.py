from urllib.parse import urlsplit

import pytest
from conftest import OWNER, STATIC_KEY, call, payload
from test_claims import form_csrf

from switchboard.blocks.webhook import _secret_names


def test_webhook_console_reports_hooks_and_credential_metadata(make):
    core, client = make()
    incoming = payload(call(client, STATIC_KEY, "webhook_create", {"name": "alerts", "verify": "path", "to": "agent:claude"}))
    outgoing = payload(call(client, STATIC_KEY, "webhook_create", {"name": "receiver", "direction": "out"}))
    inbound_secret, _, _ = _secret_names(incoming["id"])
    outbound_secret, previous, outbound_url = _secret_names(outgoing["id"])
    values = {
        inbound_secret: "fixture-inbound-path-secret",
        outbound_secret: "fixture-outbound-signing-secret",
        previous: "fixture-previous-signing-secret",
        outbound_url: "https://example.com/fixture-receiver",
    }
    for name, value in values.items():
        core.claims.save_secret(name, value)

    connection = client.get("/api/connections/webhook", headers=OWNER)
    assert connection.status_code == 200
    assert connection.json()["details"] == payload(call(client, STATIC_KEY, "webhook_list"))
    alerts, receiver = connection.json()["details"]["webhooks"]
    assert alerts["url"].endswith("/<secret>") and alerts["secret"] == "set"
    assert "url" not in receiver and receiver["address"] == "webhook:out:receiver" and receiver["secret"] == "set"

    tokens = client.get("/api/tokens", headers=OWNER)
    assert tokens.status_code == 200
    assert all(value not in connection.text and value not in tokens.text for value in values.values())
    assert {item["label"]: (item["name"], item["used_by"]) for item in tokens.json()["stored"] if item["kind"] == "webhook"} == {
        inbound_secret: ("Webhook secret", ["alerts"]),
        outbound_secret: ("Webhook signing secret", ["receiver"]),
        previous: ("Webhook signing secret", ["receiver"]),
        outbound_url: ("Webhook URL", ["receiver"]),
    }

    assert payload(call(client, STATIC_KEY, "webhook_revoke", {"name": "receiver"}))["revoked"] is True
    assert [row["name"] for row in client.get("/api/connections/webhook", headers=OWNER).json()["details"]["webhooks"]] == ["alerts"]
    assert [row["label"] for row in client.get("/api/tokens", headers=OWNER).json()["stored"] if row["kind"] == "webhook"] == [inbound_secret]


@pytest.mark.parametrize("direction", ["in", "out"])
def test_revoking_a_webhook_invalidates_open_claim_forms(make, direction):
    core, client = make()
    params = {"verify": "bearer", "to": "agent:claude"} if direction == "in" else {}
    hook = payload(call(client, STATIC_KEY, "webhook_create", {"name": "alerts", "direction": direction, **params}))
    other = payload(call(client, STATIC_KEY, "webhook_create", {"name": "other", "direction": direction, **params}))
    path = urlsplit(hook["next"][0]["link"]).path
    other_path = urlsplit(other["next"][0]["link"]).path
    current, _, url = _secret_names(hook["id"])
    page = client.get(path, headers=OWNER)
    assert page.status_code == 200
    fields = {"token": "fixture-credential-value"} if direction == "in" else {
        current: "fixture-credential-value", url: "https://example.com/receiver",
    }
    form = {"csrf": form_csrf(page), **fields}

    assert payload(call(client, STATIC_KEY, "webhook_revoke", {"name": "alerts"}))["revoked"]
    assert client.post(path, headers=OWNER, data=form).status_code == 410
    assert client.get(other_path, headers=OWNER).status_code == 200
    assert all(core.secret(name) is None for name in _secret_names(hook["id"]))
