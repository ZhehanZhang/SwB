from __future__ import annotations

import base64
import hmac
import logging
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from conftest import STATIC_KEY, call, payload
from switchboard import __main__, cli, web
from switchboard.app import create_app
from switchboard.config import ConfigError, load_config
from switchboard.core import Switchboard
from switchboard.deployment import OWNER_KEY, PLATFORM, serve_env, volume_owner_key

SETUP_CODE = bytes(range(32)).hex()
SETUP_URL = "https://swb.example.test"


@pytest.mark.parametrize(("platform", "name", "value", "url"), [
    ("fly", "FLY_APP_NAME", "test-swb", "https://test-swb.fly.dev"),
    ("koyeb", "KOYEB_PUBLIC_DOMAIN", "test-swb.example.test", "https://test-swb.example.test"),
    ("railway", "RAILWAY_PUBLIC_DOMAIN", "test-swb.example.test", "https://test-swb.example.test"),
    ("render", "RENDER_EXTERNAL_URL", "https://test-swb.example.test", "https://test-swb.example.test"),
    ("compose", "SWITCHBOARD_PUBLIC_URL", "https://test-swb.example.test", "https://test-swb.example.test"),
])
def test_platform_sign_in(tmp_path, platform, name, value, url):
    env = {PLATFORM: platform, name: value, "SWITCHBOARD_DATA": str(tmp_path)}
    setup = serve_env(env)
    key = setup[OWNER_KEY]
    assert hmac.compare_digest(key, serve_env(env)[OWNER_KEY])
    assert hmac.compare_digest(key, volume_owner_key(env))
    assert (tmp_path / "owner-key").stat().st_mode & 0o777 == 0o600

    with TestClient(create_app(Switchboard(env=setup)), base_url=url) as client:
        home = client.get("/", follow_redirects=False)
        assert home.status_code == 303
        assert home.headers["Location"] == "/login?next=%2F"
        assert client.get("/healthz").is_success
        link = urlsplit(payload(call(client, key, "login_link"))["url"])
        assert link._replace(fragment="").geturl() == f"{url}/login"
        token = link.fragment.removeprefix("t=")
        assert client.post("/auth/link", json={"token": token}, headers={"Origin": url}).status_code == 204
        assert client.get("/auth/session").json()["signed_in"]
        assert client.post("/auth/link", json={"token": token}, headers={"Origin": url}).status_code == 401


def test_managed_values_allow_explicit_overrides(tmp_path):
    url = "https://custom.example.test"
    env = {PLATFORM: "render", "RENDER_EXTERNAL_URL": "https://test-swb.example.test",
           "SWITCHBOARD_PUBLIC_URL": url, OWNER_KEY: STATIC_KEY, "SWITCHBOARD_DATA": str(tmp_path)}
    setup = serve_env(env)
    assert setup["SWITCHBOARD_PUBLIC_URL"] == url
    assert hmac.compare_digest(setup[OWNER_KEY], STATIC_KEY)
    assert not (tmp_path / "owner-key").exists()


def test_managed_setup_is_opt_in_and_requires_a_public_url(tmp_path):
    env = {"SWITCHBOARD_DATA": str(tmp_path), "FLY_APP_NAME": "test-swb"}
    assert serve_env(env) is None
    assert volume_owner_key(env) is None
    with pytest.raises(ConfigError, match="requires a public URL"):
        serve_env({**env, PLATFORM: "render"})
    assert not (tmp_path / "owner-key").exists()


def test_login_cli_reads_the_volume_key(tmp_path, monkeypatch, capsys):
    env = {PLATFORM: "fly", "FLY_APP_NAME": "test-swb", "SWITCHBOARD_DATA": str(tmp_path), "PORT": "8000"}
    key = serve_env(env)[OWNER_KEY]
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(OWNER_KEY, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    async def mock_call(url, actual_key, tool, arguments):
        assert url == "http://127.0.0.1:8000/mcp"
        assert hmac.compare_digest(actual_key, key)
        assert tool == "login_link"
        return {"url": "https://test-swb.fly.dev/login"}

    monkeypatch.setattr(cli, "_call", mock_call)
    cli.main(["login-link"])
    assert capsys.readouterr().out.strip() == "https://test-swb.fly.dev/login"
    with pytest.raises(SystemExit) as rejected:
        cli.main(["login-link", "--url", "https://other.example.test"])
    assert rejected.value.code == 1
    assert "SWITCHBOARD_KEY_OWNER is missing" in capsys.readouterr().err


@pytest.mark.parametrize("setup_code", [SETUP_CODE, base64.b64encode(bytes(range(32))).decode()], ids=["hex", "base64"])
def test_setup_code_signs_in_once_and_stays_disabled_after_restart(tmp_path, monkeypatch, setup_code):
    monkeypatch.setattr(web, "APP_DIST", tmp_path / "no-spa")
    env = {"SWITCHBOARD_PUBLIC_URL": SETUP_URL, "SWB_SETUP_CODE": setup_code, "SWITCHBOARD_DATA": str(tmp_path)}
    headers = {"Origin": SETUP_URL}
    with TestClient(create_app(Switchboard(env=env)), base_url=SETUP_URL) as client:
        home = client.get("/")
        assert 'id="owner-setup"' in home.text
        assert setup_code not in home.text
        session = client.get("/auth/session").json()
        assert session["methods"]["setup"] and not session["signed_in"]
        assert client.post("/auth/setup", json={"code": setup_code}).status_code == 403
        bad_origin = {"Origin": "https://other.example.test"}
        assert client.post("/auth/setup", json={"code": setup_code}, headers=bad_origin).status_code == 403
        assert client.post("/auth/setup", json={"code": "x" * 32}, headers=headers).status_code == 401
        response = client.post("/auth/setup", json={"code": setup_code}, headers=headers)
        assert response.status_code == 204
        assert response.headers["Cache-Control"] == "no-store"
        assert "HttpOnly" in response.headers["Set-Cookie"] and "Secure" in response.headers["Set-Cookie"]
        session = client.get("/auth/session").json()
        assert session["signed_in"] and session["via"] == "setup" and "setup" not in session["methods"]
        headers["X-CSRF-Token"] = session["csrf"]
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.post("/auth/setup", json={"code": setup_code}, headers=headers).status_code == 404
    with TestClient(create_app(Switchboard(env=env)), base_url=SETUP_URL) as client:
        assert "setup" not in client.get("/auth/session").json()["methods"]
        assert client.post("/auth/setup", json={"code": setup_code}, headers=headers).status_code == 404


@pytest.mark.parametrize("code", ["", "synthetic-short-code", bytes(range(16)).hex(), base64.b64encode(bytes(range(16))).decode(),
                                 "!" * 64, "A" * 42 + "B="])
def test_short_or_invalid_setup_code_stops_startup_without_logging_it(tmp_path, monkeypatch, capsys, code):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(PLATFORM, raising=False)
    monkeypatch.setenv("SWB_SETUP_CODE", code)
    with pytest.raises(SystemExit) as stopped:
        __main__.main(["serve"])
    assert stopped.value.code == 2
    out, error = capsys.readouterr()
    assert not out and error.startswith("swb: SWB_SETUP_CODE must encode 32 random bytes")
    assert len(error.splitlines()) == 1
    assert not code or code not in error
    assert not (tmp_path / "data" / "switchboard.db").exists()


def test_bad_public_url_in_yaml_stops_startup_without_usage(tmp_path, monkeypatch, capsys):
    path = tmp_path / "switchboard.yaml"
    path.write_text("public_url: 42\n")
    monkeypatch.setenv("SWITCHBOARD_CONFIG", str(path))
    monkeypatch.delenv(PLATFORM, raising=False)
    with pytest.raises(SystemExit) as stopped:
        __main__.main(["serve"])
    assert stopped.value.code == 2
    assert capsys.readouterr() == ("", "swb: public_url must be an HTTPS URL or use a loopback address for HTTP\n")


def test_setup_code_is_removed_from_application_environment():
    config = load_config(env={"SWB_SETUP_CODE": SETUP_CODE})
    assert config.owner_setup_digest
    assert "SWB_SETUP_CODE" not in config.env
    assert load_config(env={}).owner_setup_digest is None


def test_setup_code_rate_limits_failures_without_blocking_the_owner(tmp_path):
    env = {"SWITCHBOARD_PUBLIC_URL": SETUP_URL, "SWB_SETUP_CODE": SETUP_CODE, "SWITCHBOARD_DATA": str(tmp_path)}
    with TestClient(create_app(Switchboard(env=env)), base_url=SETUP_URL) as client:
        for i in range(8):
            headers = {"Origin": SETUP_URL, "CF-Connecting-IP": f"192.0.2.{i}"}
            assert client.post("/auth/setup", json={"code": "x" * 32}, headers=headers).status_code == 401
        response = client.post("/auth/setup", json={"code": "x" * 32},
                               headers={"Origin": SETUP_URL, "CF-Connecting-IP": "192.0.2.99"})
        assert response.status_code == 429 and "Retry-After" in response.headers
        assert client.post("/auth/setup", json={"code": SETUP_CODE}, headers={"Origin": SETUP_URL}).status_code == 204


def test_setup_code_counts_and_logs_throttled_failures_without_exposing_codes(tmp_path, caplog):
    env = {"SWITCHBOARD_PUBLIC_URL": SETUP_URL, "SWB_SETUP_CODE": SETUP_CODE, "SWITCHBOARD_DATA": str(tmp_path)}
    app = create_app(Switchboard(env=env))
    cap = app.limiter.limits["owner_setup_total"][0]
    for _ in range(cap):
        app.limiter.hit("owner_setup_total", "*")
    with TestClient(app, base_url=SETUP_URL) as client:
        headers = {"Origin": SETUP_URL}
        assert client.post("/auth/setup", json={"code": "x" * 2000}, headers=headers).status_code == 413
        caplog.set_level(logging.WARNING, logger="switchboard.owner_login")
        wrong = "ed" * 32
        for _ in range(2):
            assert client.post("/auth/setup", json={"code": wrong}, headers=headers).status_code == 429
        assert caplog.messages[-2:] == ["Owner setup attempt rate limited (1 total)", "Owner setup attempt rate limited (2 total)"]
        assert SETUP_CODE not in caplog.text and wrong not in caplog.text
        assert len(app.limiter.hits["owner_setup_total"]["*"]) == cap
        assert client.post("/auth/setup", json={"code": SETUP_CODE}, headers=headers).status_code == 204


def test_existing_sign_in_closes_setup(tmp_path):
    env = {"SWITCHBOARD_PUBLIC_URL": SETUP_URL, "SWB_SETUP_CODE": SETUP_CODE,
           "SWITCHBOARD_DATA": str(tmp_path), OWNER_KEY: STATIC_KEY}
    with TestClient(create_app(Switchboard(env=env)), base_url=SETUP_URL) as client:
        link = payload(call(client, STATIC_KEY, "login_link"))["url"]
        headers = {"Origin": SETUP_URL}
        code = urlsplit(link).fragment.removeprefix("t=")
        assert client.post("/auth/link", json={"token": code}, headers=headers).status_code == 204
        assert "setup" not in client.get("/auth/session").json()["methods"]
        assert client.post("/auth/setup", json={"code": SETUP_CODE}, headers=headers).status_code == 404
