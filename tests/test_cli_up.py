import os
import socket
import stat
import sys
from pathlib import Path

import pytest

from switchboard import __main__, cli, cli_up


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    for name in ("PORT", "SWITCHBOARD_HOST", "SWITCHBOARD_CONFIG", "SWITCHBOARD_DATA", "SWITCHBOARD_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("override", [False, True])
def test_up_defaults_to_loopback_and_uses_env_data_override(clean_env, tmp_path, monkeypatch, capsys, override):
    data = str(tmp_path / "custom-data") if override else str(Path.home() / ".local/share/switchboard")
    if override:
        monkeypatch.setenv("SWITCHBOARD_DATA", data)
    calls = []
    monkeypatch.setattr(__main__, "main", lambda argv: calls.append(
        (argv, *(os.environ[k] for k in ("SWITCHBOARD_CONFIG", "SWITCHBOARD_DATA", "SWITCHBOARD_HOST", "PORT")))))

    __main__.cli_main(["up"])

    assert calls == [(["serve"], str(tmp_path / cli.CONFIG), data, "127.0.0.1", "8870")]
    assert capsys.readouterr().out == "MCP URL: http://127.0.0.1:8870/mcp\n"
    assert "SWITCHBOARD_HOST" not in os.environ
    assert stat.S_IMODE(Path(data).stat().st_mode) == 0o700


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
signal.signal(signal.SIGTERM, lambda *_: (Path({str(stopped)!r}).touch(), sys.exit()))
print("ignored diagnostic", file=sys.stderr, flush=True)
print(" | {url} |" if {provider!r} == "cloudflare" else "{url}", file=sys.stderr, flush=True)
while True:
    time.sleep(1)
""")
    exe.chmod(0o755)

    def serve(argv, *, listener):
        assert argv == ["serve"]
        assert listener.getsockname() == ("127.0.0.1", port)
        assert os.environ["SWITCHBOARD_PUBLIC_URL"] == url
        assert not stopped.exists()
        raise KeyboardInterrupt

    monkeypatch.setattr(__main__, "main", serve)
    __main__.cli_main(["up", "--tunnel", provider])

    assert stopped.exists()
    assert os.environ["SWITCHBOARD_PUBLIC_URL"] == "https://previous.example.test"
    assert capsys.readouterr() == (f"MCP URL: {url}/mcp\n", "")


def test_missing_cloudflared_prints_install_command(clean_env, tmp_path, monkeypatch, capsys):
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


def test_tunnel_failure_does_not_log_diagnostics(clean_env, tmp_path, monkeypatch, capsys):
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
