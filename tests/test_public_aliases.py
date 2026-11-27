import pytest
from starlette.testclient import TestClient

from conftest import OWNER, PUBLIC, base_config, call, payload, sign_in
from switchboard.config import ConfigError, load_config

ALIAS = "https://swb.example.test"


def both(make, **auth):
    config = base_config(public_url_aliases=[ALIAS])
    config["auth"].update(auth)
    core, primary = make(config)
    return core, primary, TestClient(primary.app, base_url=ALIAS, follow_redirects=False)


def test_each_host_is_its_own_issuer(make):
    _, primary, alias = both(make)
    for client, base in ((primary, PUBLIC), (alias, ALIAS)):
        assert client.get("/.well-known/oauth-authorization-server").json()["issuer"] == base
        prm = client.get("/.well-known/oauth-protected-resource").json()
        assert prm["resource"] == base + "/mcp" and prm["authorization_servers"] == [base]
        assert f'{base}/.well-known/oauth-protected-resource' in client.post("/mcp").headers["www-authenticate"]
    other = TestClient(primary.app, base_url="https://evil.example.test")
    assert other.get("/.well-known/oauth-authorization-server").json()["issuer"] == PUBLIC


def test_tokens_from_either_host_work_on_both(make):
    _, primary, alias = both(make)
    token = sign_in(primary)["access_token"]
    for client in (primary, alias):
        assert payload(call(client, token, "switchboard_status"))["you"]["auth"] == "oauth"


def test_owner_writes_check_the_origin_of_the_host_used(make):
    _, primary, alias = both(make)
    csrf = alias.get("/auth/session", headers=OWNER).json()["csrf"]
    assert alias.post("/auth/logout", headers={**OWNER, "Origin": PUBLIC, "X-CSRF-Token": csrf}).status_code == 403
    assert alias.post("/auth/logout", headers={**OWNER, "Origin": ALIAS, "X-CSRF-Token": csrf}).status_code == 204


def test_passkey_rp_id_is_shared_across_hosts(make):
    _, primary, alias = both(make, passkey_rp_id="example.test")
    for client in (primary, alias):
        assert client.post("/auth/passkey/login/options").json()["publicKey"]["rpId"] == "example.test"


def test_alias_with_default_port_matches_host_header(make):
    config = base_config(public_url_aliases=[ALIAS + ":443"])
    _, primary = make(config)
    alias = TestClient(primary.app, base_url=ALIAS)
    assert alias.get("/.well-known/oauth-authorization-server").json()["issuer"] == ALIAS + ":443"


def test_passkey_count_and_enrollment_follow_the_host(make):
    core, primary, alias = both(make)
    core.store.x("INSERT INTO owner_passkeys(id, name, public_key, sign_count, created_at, rp_id) VALUES ('a', 'Old', x'00', 0, 0, 'switchboard.example.test')")
    assert primary.get("/auth/session").json()["methods"]["passkeys"] == 1
    assert alias.get("/auth/session").json()["methods"]["passkeys"] == 0
    assert "Add a passkey" in alias.get("/", headers=OWNER).text
    assert "Add a passkey" not in primary.get("/", headers=OWNER).text


@pytest.mark.parametrize(("aliases", "rp_id"), [(["http://swb.example.test"], None), ([PUBLIC], None), ([ALIAS], "other.test"), ([ALIAS], "test")])
def test_alias_and_rp_id_validation(tmp_path, aliases, rp_id):
    import yaml

    config = base_config(public_url_aliases=aliases)
    if rp_id:
        config["auth"]["passkey_rp_id"] = rp_id
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ConfigError):
        load_config(path, env={})
