import os
from pathlib import Path
from unittest.mock import Mock

import httpx2
import pytest

from switchboard.cli import main
from switchboard.companion.caps.mcp import _LoopbackTransport
from switchboard.companion.mcp_config import local_servers
from switchboard.companion.state import CompanionState


def test_stdio_uses_shlex_and_keeps_operands_literal():
    [server] = local_servers(("test='/path with spaces/server' 'two words' 'literal; $HOME' '|'",)).values()
    assert server.kind == "stdio"
    assert server.argv == ("/path with spaces/server", "two words", "literal; $HOME", "|")
    assert "literal" not in repr(server)


@pytest.mark.parametrize("target", ("http://127.0.0.1:1234/mcp", "http://[::1]:1234/mcp", "http://localhost:1234/mcp?route=a"))
def test_only_literal_loopback_http_hosts_are_accepted(target):
    [server] = local_servers((f"test={target}",)).values()
    assert server.kind == "http" and server.url == target
    assert target not in repr(server)


@pytest.mark.parametrize("specs", [
    ("test=",), ("test",), ("=cmd",), ("test='",), ("test=''",), ("test=bad\x00arg",), ("test=cmd\nsecond",),
    ("test=one", "test=two"), ("test=http://example.test/mcp",), ("test=http://localhost.example/mcp",),
    ("test=http://127.0.0.2:1234/mcp",), ("test=http://127.1:1234/mcp",), ("test=http://[::ffff:127.0.0.1]/mcp",),
    ("test=http://127.0.0.1@outside.example/mcp",), ("test=http://someone@127.0.0.1/mcp",), ("test=http://localhost:0/mcp",),
    ("test=ftp://localhost/mcp",), ("test=http://localhost/mcp#fragment",), ("test=http://localhost\\@outside.example/mcp",),
])
def test_invalid_mcp_configuration_reports_only_a_fixed_error(specs):
    with pytest.raises(ValueError, match=r"^Use --mcp NAME=COMMAND or NAME=URL\. HTTP servers must use loopback addresses\.$"):
        local_servers(specs)


def test_opt_in_validation_runs_before_hidden_pairing_prompt(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SWB_COMPANION_HOME", str(tmp_path))
    hidden = Mock(side_effect=AssertionError("pairing prompt reached"))
    monkeypatch.setattr("switchboard.cli._hidden_input", hidden)
    for extra in (["--cap", "mcp"], ["--mcp", "demo=test"]):
        with pytest.raises(SystemExit) as exc:
            main(["companion", "connect", "http://127.0.0.1:8000", *extra])
        assert exc.value.code == 1
    with pytest.raises(SystemExit) as exc:
        main(["companion", "connect", "http://127.0.0.1:8000", "--cap", "mcp", "--mcp", "demo=http://outside.example/mcp"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "HTTP servers must use loopback addresses." in err and "outside.example" not in err
    hidden.assert_not_called()


def test_state_round_trip_and_status_print_only_mcp_server_names(monkeypatch, tmp_path, capsys):
    home = tmp_path / "state"
    monkeypatch.setenv("SWB_COMPANION_HOME", str(home))
    path = home / "companion.json"
    private_argument = "fixture-private-command-argument"
    private_path = "fixture-private-url-path"
    specs = (f"device-script=python -m example {private_argument}", f"local-http=http://localhost:90/{private_path}")
    state = CompanionState("http://127.0.0.1:8000", "cmp_" + "1" * 24, "swbd_" + "A" * 43, ("mcp",), str(Path.home()), specs)
    state.save(path)
    assert CompanionState.load(path) == state
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert home.stat().st_mode & 0o777 == 0o700
    main(["companion", "status"])
    captured = capsys.readouterr()
    assert {"device-script", "local-http"}.issubset(captured.out.splitlines())
    assert not any(v in repr(state) + captured.out + captured.err for v in (private_argument, private_path, state.device_key))


async def test_loopback_transport_pins_localhost_and_refuses_other_origins(monkeypatch):
    requests = []

    async def request(self, outgoing):
        requests.append((outgoing.url.host, outgoing.headers["host"]))
        if outgoing.url.host == "127.0.0.1":
            raise httpx2.ConnectError("local service not listening on IPv4")
        return httpx2.Response(200)

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", request)
    async with _LoopbackTransport(httpx2.URL("http://localhost:1234/mcp"), Mock()) as transport:
        request = httpx2.Request("POST", "http://localhost:1234/mcp")
        assert (await transport.handle_async_request(request)).status_code == 200
        assert request.url.host == "localhost"
        with pytest.raises(httpx2.ConnectError, match="Local MCP request failed."):
            await transport.handle_async_request(httpx2.Request("POST", "http://localhost:1235/mcp"))
        with pytest.raises(httpx2.ConnectError, match="Local MCP request failed."):
            await transport.handle_async_request(httpx2.Request("POST", "http://outside.example/mcp"))
    assert requests == [("127.0.0.1", "localhost:1234"), ("::1", "localhost:1234")]
