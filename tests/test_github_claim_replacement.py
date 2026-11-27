import hmac
import json
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import OWNER, STATIC_KEY, base_config, call, error_text, payload
from test_claims import form_csrf

SECRET = "GITHUB_WEBHOOK_SECRET"
APP_ID = "GITHUB_APP_ID"
PRIVATE_KEY = "GITHUB_APP_PRIVATE_KEY"
INITIAL = "0123456789abcdef" * 4
REPLACEMENT = "fedcba9876543210" * 4


def connect(client, choose=None, *, token=STATIC_KEY):
    return payload(call(client, token, "connect", {"what": "github", "choose": choose}))


def claim_steps(core, result):
    return {core.claims.inspect(step["link"].rsplit("/", 1)[-1]): step for step in result["next"] if "link" in step}


def save(client, step, value):
    path = urlsplit(step["link"]).path
    assert client.get(path).status_code == 303
    page = client.get(path, headers=OWNER)
    assert page.status_code == 200
    saved = client.post(path, headers=OWNER, data={"csrf": form_csrf(page), "token": value})
    assert saved.status_code == 200 and value not in saved.text
    assert client.get(path, headers=OWNER).status_code == 410


def ping(client, secret):
    body = b'{"zen":"Test delivery"}'
    return client.post("/github/webhook", content=body, headers={
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": "sha256=" + hmac.digest(secret.encode(), body, "sha256").hex(),
    })


def test_github_replace_secret_recovers_webhook_after_restart(make):
    cfg = base_config(version=2, blocks={"mcp": {}, "github": {"spaces": {"acme/app": {"mode": "watch"}}}})
    core, client = make(cfg)
    initial = claim_steps(core, connect(client))[SECRET]
    save(client, initial, INITIAL)

    core, client = make(cfg)
    assert ping(client, INITIAL).status_code == 200
    assert ping(client, REPLACEMENT).status_code == 401
    assert SECRET not in claim_steps(core, connect(client))

    rotations = [claim_steps(core, connect(client, {"replace": option})) for option in ("secret", "webhook_secret")]
    assert all(steps.keys() == {SECRET} for steps in rotations)
    first, second = [steps[SECRET] for steps in rotations]
    assert len({initial["link"], first["link"], second["link"]}) == 3
    assert "Restart SwB" in second["do"]
    assert core.secret(SECRET) == INITIAL
    save(client, second, REPLACEMENT)
    assert core.secret(SECRET) == REPLACEMENT
    assert ping(client, INITIAL).status_code == 200
    assert ping(client, REPLACEMENT).status_code == 401

    pending = connect(client)
    assert pending["next"][0] == {"by": "person", "do": "Restart SwB to apply the saved GitHub webhook secret."}
    assert SECRET not in claim_steps(core, pending)
    assert all(secret not in json.dumps([first, second, pending, core.audit.recent()]) for secret in (INITIAL, REPLACEMENT))

    _, client = make(cfg)
    assert ping(client, INITIAL).status_code == 401
    assert ping(client, REPLACEMENT).json() == {"ok": True, "zen": "Test delivery"}


def test_github_replace_all_reissues_the_saved_app_credentials(make):
    core, client = make(base_config(version=2, blocks={"mcp": {}, "github": {}}))
    previous = claim_steps(core, connect(client, {"credential": "app"}))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    for name, value in ((SECRET, INITIAL), (APP_ID, "12345"), (PRIVATE_KEY, key)):
        save(client, previous[name], value)
    assert not claim_steps(core, connect(client))

    replacements = claim_steps(core, connect(client, {"replace": "all"}))
    assert replacements.keys() == previous.keys()
    assert all(step["link"] != previous[name]["link"] for name, step in replacements.items())
    assert core.blocks["github"].api.auth_type() == "app"


def test_github_replace_all_uses_the_saved_personal_access_token(make):
    core, client = make(base_config(version=2, blocks={"mcp": {}, "github": {}}))
    previous = claim_steps(core, connect(client, {"credential": "pat"}))
    save(client, previous["GITHUB_TOKEN"], "github_pat_fixture_token_value")
    replacements = claim_steps(core, connect(client, {"replace": "all"}))
    assert set(replacements) == {SECRET, "GITHUB_TOKEN"}
    assert replacements["GITHUB_TOKEN"]["link"] != previous["GITHUB_TOKEN"]["link"]


def test_github_replace_all_points_to_configuration_for_overrides(make):
    env = {"SWITCHBOARD_KEY_OWNER": STATIC_KEY, "CUSTOM_HOOK_SECRET": INITIAL, "CUSTOM_APP_KEY": "fixture-key"}
    cfg = base_config(version=2, blocks={"mcp": {}, "github": {
        "secret_env": "CUSTOM_HOOK_SECRET", "app_id": 12345, "private_key_env": "CUSTOM_APP_KEY",
    }})
    core, client = make(cfg, env=env)
    steps = connect(client, {"replace": "all"})["next"]
    assert steps == [{"by": "person", "do": instruction} for instruction in (
        "Change CUSTOM_HOOK_SECRET in .env and restart SwB.",
        "Change blocks.github.app_id in config/switchboard.yaml and restart SwB.",
        "Change CUSTOM_APP_KEY in .env and restart SwB.",
    )]
    assert not core.store.q("SELECT 1 FROM claim_links")


@pytest.mark.parametrize("invalid", ["true", True, ["secret"]])
def test_github_replace_rejects_invalid_choices_before_minting(make, invalid):
    core, client = make(base_config(version=2, blocks={"mcp": {}, "github": {}}))
    result = call(client, STATIC_KEY, "connect", {"what": "github", "choose": {"replace": invalid}})
    assert "Use secret, webhook_secret or all for choose.replace." in error_text(result)
    assert not core.store.q("SELECT 1 FROM claim_links")


def test_github_replace_keeps_claim_links_owner_only(make):
    core, client = make(base_config(version=2, blocks={"mcp": {}, "github": {}}))
    pair = payload(call(client, STATIC_KEY, "connect_code", {"agent": "github-test"}))
    paired = client.post("/connect", json={"code": pair["code"]}).json()["key"]
    out = connect(client, {"replace": "all"}, token=paired)
    assert out["next"] == [{"by": "person", "do": "Run connect for github from the owner's sign-in."}]
    assert not core.store.q("SELECT 1 FROM claim_links")
