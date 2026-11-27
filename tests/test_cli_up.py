import logging
import os
import socket
import sys
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from conftest import CLAUDE_CB, STATIC_KEY, call, error_text, payload, pkce, register
from switchboard import __main__, cli, cli_up
from switchboard.config import load_config


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    for name in ("PORT", "SWITCHBOARD_HOST", "SWITCHBOARD_CONFIG", "SWITCHBOARD_DATA", "SWITCHBOARD_DATA_DIR",
                 "SWITCHBOARD_PUBLIC_URL", "PUBLIC_URL", "SWITCHBOARD_DEPLOY_PLATFORM", "CLOUDFLARE_ACCESS_TEAM_DOMAIN",
                 "CLOUDFLARE_ACCESS_AUD", "SWITCHBOARD_OWNER_EMAILS", "SWB_SETUP_CODE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("override", [None, "SWITCHBOARD_DATA", "SWITCHBOARD_DATA_DIR"])
def test_up_defaults_to_loopback_and_uses_env_data_override(clean_env, tmp_path, monkeypatch, capsys, override):
    data = tmp_path / "custom-data" if override else Path.home() / ".local/share/switchboard"
    if override:
        monkeypatch.setenv(override, str(data))
    calls = []
    monkeypatch.setattr(__main__, "main", lambda argv, *, announce, **_: calls.append(
        (argv, load_config().data_dir, os.environ["SWITCHBOARD_HOST"], os.environ["PORT"], announce)))

    __main__.cli_main(["up"])

    assert calls == [(["serve"], data, "127.0.0.1", "8870", True)]
    assert capsys.readouterr() == ("", "")
    assert "SWITCHBOARD_HOST" not in os.environ


@pytest.mark.parametrize("existing_home_db", [False, True])
def test_init_and_up_use_separate_data_directories(clean_env, tmp_path, monkeypatch, caplog, existing_home_db):
    home_db = Path.home() / ".local/share/switchboard/switchboard.db"
    if existing_home_db:
        home_db.parent.mkdir(parents=True)
        home_db.touch()
    opened = []

    def serve(app, **options):
        with TestClient(app):
            opened.append(app.starlette.state.core.data_dir)

    monkeypatch.setattr(__main__.uvicorn, "run", serve)
    for name in ("first", "second"):
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.chdir(folder)
        __main__.cli_main(["init"])
        assert (folder / "data").is_dir()
        __main__.cli_main(["up"])

    assert opened == [tmp_path / name / "data" for name in ("first", "second")]
    assert all((data / "switchboard.db").is_file() for data in opened)
    assert home_db.exists() is existing_home_db
    assert "Using existing data" not in caplog.text


@pytest.mark.parametrize("mode", ["empty data", "existing db", "explicit", "SWITCHBOARD_DATA_DIR", "SWITCHBOARD_DATA"])
def test_up_uses_instance_data_when_selected(clean_env, tmp_path, monkeypatch, caplog, mode):
    home_data = Path.home() / ".local/share/switchboard"
    home_data.mkdir(parents=True)
    (home_data / "switchboard.db").touch()
    config = tmp_path / cli.CONFIG
    config.parent.mkdir()
    config.write_text("version: 2\n")
    data = tmp_path / "data"
    args = ["up"]
    if mode.startswith("SWITCHBOARD_"):
        data = tmp_path / "custom-data"
        monkeypatch.setenv(mode, str(data))
    elif mode == "explicit":
        args += ["--dir", str(tmp_path)]
    elif mode in ("empty data", "existing db"):
        data.mkdir()
        if mode == "existing db":
            (data / "switchboard.db").touch()

    def serve(app, **options):
        with TestClient(app):
            assert app.starlette.state.core.data_dir == data

    monkeypatch.setattr(__main__.uvicorn, "run", serve)
    __main__.cli_main(args)

    assert (data / "switchboard.db").is_file()
    assert "Using existing data" not in caplog.text
    assert not list(home_data.glob(".instance-data-notice-*"))


def test_up_keeps_the_old_home_database_and_warns_once_per_instance(clean_env, tmp_path, monkeypatch, caplog, capsys):
    from switchboard.store import Store

    home_data = Path.home() / ".local/share/switchboard"
    home_data.mkdir(parents=True)
    store = Store(home_data / "switchboard.db")
    store.x("INSERT INTO audit (at, day, origin, source, client, destination, action, outcome) "
            "VALUES (1, '1970-01-01', 'mcp', 'owner', 'key:owner', 'core', 'saved-event', 'ok')")
    store.close()

    def serve(app, **options):
        with TestClient(app):
            assert app.starlette.state.core.data_dir == home_data

    monkeypatch.setattr(__main__.uvicorn, "run", serve)
    for name in ("first", "second"):
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.chdir(folder)
        config = folder / cli.CONFIG
        config.parent.mkdir()
        config.write_text("version: 2\n")
        if name == "second":
            __main__.cli_main(["init", "--force"])
            assert not (folder / "data").exists()
            capsys.readouterr()
        __main__.cli_main(["log"])
        assert '"action": "saved-event"' in capsys.readouterr().out
        __main__.cli_main(["up"])
        __main__.cli_main(["up"])
        notice = f"Using existing data in {home_data} instead of {folder / 'data'}. Set SWITCHBOARD_DATA_DIR"
        assert caplog.text.count(notice) == 1
        assert not (folder / "data").exists()
    assert len(list(home_data.glob(".instance-data-notice-*"))) == 2


def test_container_serve_keeps_its_data_volume(clean_env, monkeypatch):
    from switchboard import core

    monkeypatch.setenv("SWITCHBOARD_DATA", "/data")
    monkeypatch.setenv("SWITCHBOARD_HOST", "0.0.0.0")
    paths = []

    def build(**kwargs):
        assert kwargs["data_dir"] is None
        paths.append(load_config(env=kwargs["env"]).data_dir)
        raise SystemExit(0)

    monkeypatch.setattr(core, "Switchboard", build)
    with pytest.raises(SystemExit) as stopped:
        __main__.cli_main(["serve"])
    assert stopped.value.code == 0
    assert paths == [Path("/data")]


def test_client_reads_the_owner_key_from_its_instance_data(clean_env, tmp_path, monkeypatch, capsys):
    import hmac

    from switchboard.deployment import OWNER_KEY, volume_owner_key

    monkeypatch.delenv(OWNER_KEY, raising=False)
    config = tmp_path / cli.CONFIG
    config.parent.mkdir()
    config.touch()
    (tmp_path / ".env").write_text("SWITCHBOARD_DEPLOY_PLATFORM=compose\n")
    key = volume_owner_key({"SWITCHBOARD_DEPLOY_PLATFORM": "compose", "SWITCHBOARD_DATA": str(tmp_path / "data")}, create=True)

    async def call_with_volume_key(url, actual_key, tool, arguments):
        assert hmac.compare_digest(actual_key, key)
        return {"url": "http://localhost:8870/login"}

    monkeypatch.setattr(cli, "_call", call_with_volume_key)
    __main__.cli_main(["login-link"])

    assert capsys.readouterr().out == "http://localhost:8870/login\n"


@pytest.mark.parametrize(("env_host", "flag", "expected", "origin"), [
    (None, [], "localhost", "http://localhost:8870"),
    ("0.0.0.0", [], "0.0.0.0", "http://127.0.0.1:8870"),
    ("0.0.0.0", ["--host", "::1"], "::1", "http://localhost:8870"),
])
def test_up_host_precedence(clean_env, tmp_path, monkeypatch, capsys, env_host, flag, expected, origin):
    (tmp_path / ".env").write_text("SWITCHBOARD_HOST=localhost\n")
    if env_host:
        monkeypatch.setenv("SWITCHBOARD_HOST", env_host)
    calls = []
    monkeypatch.setattr(__main__.uvicorn, "run", lambda app, **options: calls.append(options))

    __main__.cli_main(["up", *flag])

    assert calls[0]["host"] == expected
    assert capsys.readouterr() == (f"MCP URL: {origin}/mcp\n", "")
    assert os.environ.get("SWITCHBOARD_HOST") == env_host


@pytest.mark.parametrize(("env_host", "flag", "expected"), [
    (None, [], "127.0.0.1"),
    ("0.0.0.0", [], "0.0.0.0"),
    ("0.0.0.0", ["--host", "::1"], "::1"),
])
def test_serve_host_precedence(clean_env, monkeypatch, env_host, flag, expected):
    if env_host:
        monkeypatch.setenv("SWITCHBOARD_HOST", env_host)
    calls = []
    monkeypatch.setattr(__main__.uvicorn, "run", lambda app, **options: calls.append(options))

    __main__.cli_main(["serve", *flag])

    assert calls[0]["host"] == expected


def test_up_enables_owner_sign_in_with_only_the_owner_key(clean_env, tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text(f"SWITCHBOARD_KEY_OWNER={STATIC_KEY}\n")
    monkeypatch.delenv("SWITCHBOARD_KEY_OWNER", raising=False)
    monkeypatch.setenv("PORT", "18871")
    base = "http://localhost:18871"

    def serve(app, *, host, port, **options):
        assert (host, port) == ("127.0.0.1", 18871)
        with TestClient(app, base_url="http://127.0.0.1:18871") as client:
            assert client.get("/healthz").json()["status"] == "ok"
            link = payload(call(client, STATIC_KEY, "login_link"))["url"]
            assert link.startswith(f"{base}/login#t=")
            token = link.partition("#t=")[2]
            assert client.post(f"{base}/auth/link", json={"token": token}, headers={"Origin": base}).status_code == 204

    monkeypatch.setattr(__main__.uvicorn, "run", serve)
    __main__.cli_main(["up"])
    assert capsys.readouterr() == ("MCP URL: http://localhost:18871/mcp\n", "")


@pytest.mark.parametrize(("host", "bound_origin"), [
    ("127.0.0.1", "http://127.0.0.1:18871"),
    ("::1", "http://[::1]:18871"),
])
def test_up_authorize_accepts_the_printed_resource_and_bound_alias(clean_env, monkeypatch, capsys, host, bound_origin):
    monkeypatch.setenv("PORT", "18871")
    canonical = "http://localhost:18871"

    def serve(app, **options):
        printed = capsys.readouterr().out.removeprefix("MCP URL: ").strip()
        assert printed == f"{canonical}/mcp"
        with TestClient(app, base_url=canonical, follow_redirects=False) as client:
            assert client.get(f"{bound_origin}/.well-known/oauth-protected-resource").json()["resource"] == f"{bound_origin}/mcp"
            params = {"client_id": register(client)["client_id"], "redirect_uri": CLAUDE_CB, "response_type": "code",
                      "code_challenge": pkce()[1], "code_challenge_method": "S256"}
            for resource in (printed, f"{bound_origin}/mcp"):
                response = client.get("/authorize", params={**params, "resource": resource})
                assert response.status_code == 303 and urlsplit(response.headers["Location"]).path == "/login"
            invalid = client.get("/authorize", params={**params, "resource": f"{bound_origin}/other"})
            assert invalid.status_code == 302
            assert parse_qs(urlsplit(invalid.headers["Location"]).query)["error"] == ["invalid_target"]

    monkeypatch.setattr(__main__.uvicorn, "run", serve)
    __main__.cli_main(["up", "--host", host])


@pytest.mark.parametrize(("setting", "message"), [
    ("SWB_SETUP_CODE=abc", "SWB_SETUP_CODE must encode 32 random bytes"),
    ("SWITCHBOARD_PUBLIC_URL=http://swb.example.test", "public_url must be an HTTPS URL"),
])
def test_up_prints_only_the_startup_error(clean_env, tmp_path, capsys, setting, message):
    (tmp_path / ".env").write_text(f"{setting}\n")
    with pytest.raises(SystemExit) as exited:
        __main__.cli_main(["up"])
    assert exited.value.code == 2
    out, err = capsys.readouterr()
    assert not out and err.startswith(f"swb: {message}") and len(err.splitlines()) == 1


def test_wildcard_bind_requires_public_url_for_owner_link(clean_env, monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("SWITCHBOARD_KEY_OWNER", STATIC_KEY)

    def serve(app, **options):
        with TestClient(app) as client:
            assert "Set SWITCHBOARD_PUBLIC_URL" in error_text(call(client, STATIC_KEY, "login_link"))

    monkeypatch.setattr(__main__.uvicorn, "run", serve)
    __main__.cli_main(["serve"])


@pytest.mark.parametrize(("provider", "binary", "url", "flags"), [
    ("cloudflare", "cloudflared", "https://sample-name.trycloudflare.com", ("tunnel", "--no-autoupdate", "--url")),
    ("tailscale", "tailscale", "https://example-node.sample-tailnet.ts.net", ("funnel",)),
])
def test_tunnel_passes_public_url_and_stops_on_server_exit(
    clean_env, tmp_path, monkeypatch, capsys, provider, binary, url, flags,
):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("SWITCHBOARD_KEY_OWNER", "fixture-owner-key-not-a-secret")
    monkeypatch.setenv("SWITCHBOARD_PUBLIC_URL", "https://previous.example.test")
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    monkeypatch.setenv("PORT", str(port))
    origin = f"http://127.0.0.1:{port}"
    stopped = tmp_path / "stopped"
    exe = tmp_path / binary
    exe.write_text(f"""#!{sys.executable}
import os, signal, sys, time
from pathlib import Path
assert tuple(sys.argv[1:]) == {(*flags, origin)!r}
assert "SWITCHBOARD_KEY_OWNER" not in os.environ
stop_signal = signal.SIGINT if os.name == "posix" and {provider!r} == "tailscale" else signal.SIGTERM
signal.signal(stop_signal, lambda *_: (Path({str(stopped)!r}).touch(), sys.exit()))
print("ignored diagnostic", file=sys.stderr, flush=True)
print(" | {url} |" if {provider!r} == "cloudflare" else "{url}", file=sys.stderr, flush=True)
while True:
    time.sleep(1)
""")
    exe.chmod(0o755)

    (tmp_path / cli.CONFIG).parent.mkdir()
    (tmp_path / cli.CONFIG).write_text("public_url: https://base.example.test\n")
    (tmp_path / cli.CONFIG.with_name("local.yaml")).write_text("public_url: https://overlay.example.test\n")

    def serve(argv, *, listener, tunnel_url, announce, **_):
        assert argv == ["serve"]
        assert listener.getsockname() == ("127.0.0.1", port)
        assert load_config(tunnel_url=tunnel_url).public_url == url
        assert announce
        assert not stopped.exists()
        raise KeyboardInterrupt

    monkeypatch.setattr(__main__, "main", serve)
    __main__.cli_main(["up", "--tunnel", provider])

    assert stopped.exists()
    assert os.environ["SWITCHBOARD_PUBLIC_URL"] == "https://previous.example.test"
    assert capsys.readouterr() == ("", "")


def test_missing_cloudflared_prints_install_command(clean_env, tmp_path, monkeypatch, capsys, unused_tcp_port):
    monkeypatch.setenv("PORT", str(unused_tcp_port))
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(cli_up.platform, "system", lambda: "Darwin")
    with pytest.raises(SystemExit) as exc:
        cli.main(["up", "--tunnel", "cloudflare"])
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    assert "cloudflared was not found on PATH" in err and "brew install cloudflared" in err


def test_up_never_tunnels_an_existing_service(clean_env, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        bound.listen()
        port = bound.getsockname()[1]
        monkeypatch.setenv("PORT", str(port))
        with pytest.raises(SystemExit) as exc:
            cli.main(["up", "--tunnel", "cloudflare"])
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    assert f"Could not bind http://127.0.0.1:{port}." in err


def test_tunnel_uses_the_selected_ipv6_bind_host(clean_env, monkeypatch):
    try:
        with socket.socket(socket.AF_INET6) as bound:
            bound.bind(("::1", 0))
            port = bound.getsockname()[1]
    except OSError:
        pytest.skip("IPv6 loopback is unavailable")
    monkeypatch.setenv("PORT", str(port))
    tunnel = "https://sample.trycloudflare.com"

    def open_tunnel(provider, origin):
        assert provider == "cloudflare" and origin == f"http://[::1]:{port}"
        return nullcontext(tunnel)

    def serve(argv, *, listener, tunnel_url, **_):
        assert listener.family == socket.AF_INET6
        assert listener.getsockname()[:2] == ("::1", port)
        assert tunnel_url == tunnel

    monkeypatch.setattr(cli_up, "_tunnel", open_tunnel)
    monkeypatch.setattr(__main__, "main", serve)
    __main__.cli_main(["up", "--host", "::1", "--tunnel", "cloudflare"])


def test_tunnel_failure_does_not_log_diagnostics(clean_env, tmp_path, monkeypatch, capsys, unused_tcp_port):
    monkeypatch.setenv("PORT", str(unused_tcp_port))
    monkeypatch.setenv("PATH", str(tmp_path))
    exe = tmp_path / "cloudflared"
    exe.write_text("#!/bin/sh\nprintf 'fixture-diagnostic\\n' >&2\nexit 1\n")
    exe.chmod(0o755)
    with pytest.raises(SystemExit) as exc:
        cli.main(["up", "--tunnel", "cloudflare"])
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    assert "did not start a tunnel" in err and "fixture-diagnostic" not in err


@pytest.mark.parametrize("path", ["/claim/code", "/routines/claim/code", "/claims/discord/code", "/oauth?code=code"])
def test_access_log_redacts_claim_paths_and_query_strings(path):
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s", ("local", "GET", path), None)
    assert __main__._NoQuery().filter(record)
    assert "code" not in record.getMessage()
