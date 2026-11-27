import logging
import os
import socket
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import STATIC_KEY, call, error_text, payload
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
    monkeypatch.setattr(__main__, "main", lambda argv, *, announce_url, **_: calls.append(
        (argv, load_config().data_dir, os.environ["SWITCHBOARD_HOST"], os.environ["PORT"], announce_url)))

    __main__.cli_main(["up"])

    assert calls == [(["serve"], data, "127.0.0.1", "8870", "http://127.0.0.1:8870")]
    assert capsys.readouterr() == ("", "")
    assert "SWITCHBOARD_HOST" not in os.environ


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
    assert capsys.readouterr() == ("MCP URL: http://127.0.0.1:18871/mcp\n", "")


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

    def serve(argv, *, listener, tunnel_url, announce_url, **_):
        assert argv == ["serve"]
        assert listener.getsockname() == ("127.0.0.1", port)
        assert load_config(tunnel_url=tunnel_url).public_url == url
        assert announce_url == url
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
