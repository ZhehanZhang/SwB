import json
import logging
import os
import stat
import warnings
from contextlib import asynccontextmanager
from importlib.metadata import version
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs

import httpx2
import pytest
import yaml
from mcp.types import CallToolResult, TextContent

from conftest import OWNER, PUBLIC, STATIC_KEY, base_config, call, payload
from switchboard import __main__, cli


def test_log_prints_one_json_object_per_line(make, tmp_path, monkeypatch, capsys):
    core, _ = make()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SWITCHBOARD_DATA_DIR", str(core.data_dir))
    monkeypatch.delenv("SWITCHBOARD_CONFIG", raising=False)
    for title in ("First", "Second café\nStill one line"):
        core.audit.record(origin="mcp", source="owner", client="key:owner", destination="core",
                          action="read", outcome="ok", title=title)

    __main__.cli_main(["log"])

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert [json.loads(line) for line in lines] == core.audit.recent()


def test_cli_version_matches_installed_distribution(capsys):
    with pytest.raises(SystemExit) as exited:
        __main__.cli_main(["--version"])
    assert exited.value.code == 0
    assert capsys.readouterr().out == f"swb {version('swb-one')}\n"


@pytest.fixture(autouse=True)
def clear_owner_key(monkeypatch):
    monkeypatch.delenv(cli.OWNER_KEY, raising=False)


def test_init_writes_minimal_config_and_private_owner_key(tmp_path, capsys):
    instance = tmp_path / "my instance"
    cli.main(["init", "--dir", str(instance), "--public-url", "https://switchboard.example.test/"])
    assert yaml.safe_load((instance / cli.CONFIG).read_text()) == {
        "version": 2, "blocks": {"mcp": {"use": "mcp"}}, "public_url": "https://switchboard.example.test",
    }
    env = instance / ".env"
    name, key = env.read_text().strip().split("=", 1)
    assert name == cli.OWNER_KEY
    assert len(bytes.fromhex(key)) == 32
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    out, err = capsys.readouterr()
    assert key not in out + err
    assert f"Owner key saved in {env}." in out and f"swb up --dir '{instance}'" in out


@pytest.mark.parametrize("existing", [cli.CONFIG, ".env"])
def test_init_does_not_overwrite_or_create_other_file_when_one_exists(tmp_path, capsys, existing):
    path = tmp_path / existing
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("existing")
    with pytest.raises(SystemExit) as exc:
        cli.main(["init", "--dir", str(tmp_path)])
    assert exc.value.code == 1
    assert path.read_text() == "existing"
    other = cli.CONFIG if existing == ".env" else ".env"
    assert not (tmp_path / other).exists()
    assert "--force" in capsys.readouterr().err


def test_init_force_rotates_key_without_following_a_symlink(tmp_path, capsys):
    cli.main(["init", "--dir", str(tmp_path)])
    env = tmp_path / ".env"
    original = env.read_text()
    target = tmp_path / "keep"
    env.rename(target)
    env.symlink_to(target)
    cli.main(["init", "--dir", str(tmp_path), "--force"])
    assert not env.is_symlink()
    assert env.read_text() != original
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert target.read_text() == original


def test_init_invalid_public_url_leaves_no_files(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["init", "--dir", str(tmp_path), "--public-url", "http://example.test"])
    assert not list(tmp_path.iterdir())
    assert "--public-url" in capsys.readouterr().err


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_init_accepts_http_loopback_public_urls(tmp_path, host):
    origin = f"http://{host}:8870"
    cli.main(["init", "--dir", str(tmp_path), "--public-url", origin])
    assert yaml.safe_load((tmp_path / cli.CONFIG).read_text())["public_url"] == origin


def test_init_points_to_login_link(tmp_path, capsys):
    cli.main(["init", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert f"swb login-link --dir {tmp_path}" in out
    assert cli._read_env(tmp_path / ".env")[cli.OWNER_KEY] not in out


def test_login_link_calls_owner_tool(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(cli.OWNER_KEY, raising=False)
    cli.main(["init", "--dir", str(tmp_path)])
    capsys.readouterr()
    link = "https://switchboard.example.test/login#t=fixture"

    async def call(url, key, tool, args):
        assert cli._read_env(tmp_path / ".env")[cli.OWNER_KEY] == key
        assert (url, tool, args) == ("https://switchboard.example.test/mcp", "login_link", {})
        return {"url": link, "expires_in": 300}

    monkeypatch.setattr(cli, "_call", call)
    cli.main(["login-link", "--dir", str(tmp_path), "--url", "https://switchboard.example.test"])
    assert capsys.readouterr().out == link + "\n"


def test_up_uses_instance_paths_and_loads_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(f"# comment\n{cli.OWNER_KEY}='owner-test-key'\nPORT=4567 # local port\n")
    calls = []
    monkeypatch.setattr(__main__, "main", lambda argv, *, data_dir, **_: calls.append(
        (argv, *(os.environ[k] for k in (cli.OWNER_KEY, "PORT", "SWITCHBOARD_CONFIG")), data_dir)))
    with patch.dict(os.environ, {"SWITCHBOARD_CONFIG": "/path/to/old-config", "PORT": "9876",
                                 "HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home")}, clear=True):
        cli.main(["up", "--dir", str(tmp_path)])
    assert calls == [(["serve"], "owner-test-key", "9876", str(tmp_path / cli.CONFIG), tmp_path / "data")]


def test_connect_passes_choices_and_renders_mcp_actions(tmp_path, monkeypatch, capsys):
    key = "fixture-owner-key-not-a-secret"
    (tmp_path / ".env").write_text(f"{cli.OWNER_KEY}={key}\n")
    call = AsyncMock(return_value={
        "what": "mcp-client", "done": ["The MCP server is running."], "next": [
            {"by": "you", "do": "Choose another client.", "call": {"tool": "connect", "arguments": {"what": "mcp-client"}}},
            {"by": "person", "do": "Open the link.", "link": "https://switchboard.example.test/claim/example",
             "snippet": "[mcp_servers.switchboard]\nurl = 'https://switchboard.example.test/mcp'"},
        ],
    })
    monkeypatch.setattr(cli, "_call", call)
    cli.main(["connect", "mcp-client", "--choose", "agent=client", "sample=a=b", "--choose", "project=demo",
              "create_new=true", "primary=false",
              "--dir", str(tmp_path), "--url", "https://switchboard.example.test"])
    call.assert_awaited_once_with("https://switchboard.example.test/mcp", key, "connect",
                                  {"what": "mcp-client", "choose": {"agent": "client", "sample": "a=b", "project": "demo",
                                                                   "create_new": True, "primary": False}})
    out, err = capsys.readouterr()
    assert key not in out + err
    for text in ("Done:", "The MCP server is running.", "Next steps:", "you: Choose another client.",
                 'Call: {"tool": "connect"', "person: Open the link.", "https://switchboard.example.test/claim/example",
                 "    [mcp_servers.switchboard]\n    url = 'https://switchboard.example.test/mcp'"):
        assert text in out


@pytest.mark.parametrize(("kind", "create_token"), [
    ("telegram", "Create a bot in @BotFather with /newbot."),
    ("discord", "Under Bot, reset or generate the bot token."),
])
def test_connect_bot_cli_prints_one_token_entry_step(make, tmp_path, monkeypatch, capsys, kind, create_token):
    _, client = make(config=base_config(version=2, blocks={"mcp": {}, kind: {}}))
    (tmp_path / ".env").write_text(f"{cli.OWNER_KEY}={STATIC_KEY}\n")
    result = payload(call(client, STATIC_KEY, "connect", {"what": kind}))
    monkeypatch.setattr(cli, "_call", AsyncMock(return_value=result))
    cli.main(["connect", kind, "--dir", str(tmp_path), "--url", PUBLIC])
    out = capsys.readouterr().out
    assert create_token in out and out.lower().count("paste the token") == 1
    [step] = result["next"]
    assert step["link"] in out
    assert client.get(step["link"], headers=OWNER).status_code == 200


@pytest.mark.parametrize("bad", [["missing-equals"], ["=value"], ["name=a", "name=b"], ["primary=wrong"]])
def test_connect_rejects_invalid_choices_before_mcp(tmp_path, monkeypatch, capsys, bad):
    call = AsyncMock()
    monkeypatch.setattr(cli, "_call", call)
    with pytest.raises(SystemExit):
        cli.main(["connect", "mcp-client", "--dir", str(tmp_path), "--choose", *bad])
    call.assert_not_awaited()
    assert "--choose" in capsys.readouterr().err


def test_status_lists_next_steps_and_redacts_owner_key(tmp_path, monkeypatch, capsys):
    key = "fixture-owner-key-not-a-secret"
    (tmp_path / ".env").write_text(f"{cli.OWNER_KEY}={key}\nPORT=1234\n")
    for name in ("PORT", "SWITCHBOARD_HOST", "SWITCHBOARD_PORT"):
        monkeypatch.delenv(name, raising=False)
    call = AsyncMock(return_value={"status": "degraded", "next_steps": ["Configure OAuth.", f"Echoed {key}"]})
    monkeypatch.setattr(cli, "_call", call)
    cli.main(["status", "--dir", str(tmp_path)])
    call.assert_awaited_once_with("http://localhost:1234/mcp", key, "switchboard_status", {})
    out, err = capsys.readouterr()
    assert key not in out + err
    assert out == "Status: degraded\nNext steps:\n  - Configure OAuth.\n  - Echoed [redacted]\n"


@pytest.mark.parametrize(("kind", "value", "parsed"), [("muse", "true", True), ("grok", "url", "url")])
def test_connect_passes_block_specific_replacement(tmp_path, monkeypatch, kind, value, parsed):
    key = "fixture-owner-key-not-a-secret"
    monkeypatch.setenv(cli.OWNER_KEY, key)
    call = AsyncMock(return_value={"what": kind, "next": []})
    monkeypatch.setattr(cli, "_call", call)
    cli.main(["connect", kind, "--choose", f"replace={value}", "--dir", str(tmp_path), "--url", "https://swb.example"])
    call.assert_awaited_once_with("https://swb.example/mcp", key, "connect", {"what": kind, "choose": {"replace": parsed}})


def test_remote_http_is_rejected_without_sending_owner_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.OWNER_KEY, "fixture-owner-key-not-a-secret")
    call = AsyncMock()
    monkeypatch.setattr(cli, "_call", call)
    with pytest.raises(SystemExit):
        cli.main(["status", "--dir", str(tmp_path), "--url", "http://switchboard.example.test/mcp"])
    call.assert_not_awaited()
    assert "--url must be HTTPS" in capsys.readouterr().err


@pytest.mark.parametrize(("bind_host", "target"), [
    ("0.0.0.0", "localhost"),
    ("::", "localhost"),
    ("::1", "[::1]"),
    ("127.0.0.1", "127.0.0.1"),
    ("192.168.1.20", "localhost"),
    ("localhost", "localhost"),
    (None, "localhost"),
])
@pytest.mark.parametrize("published_port", [None, "5678"])
def test_client_defaults_to_a_loopback_target(tmp_path, monkeypatch, bind_host, target, published_port):
    key = "fixture-owner-key-not-a-secret"
    values = {cli.OWNER_KEY: key, "PORT": "1234", "SWITCHBOARD_HOST": bind_host, "SWITCHBOARD_PORT": published_port}
    (tmp_path / ".env").write_text("".join(f"{name}={value}\n" for name, value in values.items() if value is not None))
    for name in values:
        monkeypatch.delenv(name, raising=False)
    origin = f"http://{target}:{published_port or '1234'}"
    call = AsyncMock(return_value={"url": "http://localhost:1234/login#t=fixture"})
    claim = Mock()
    monkeypatch.setattr(cli, "_call", call)
    monkeypatch.setattr(cli, "_claim", claim)

    cli.main(["login-link", "--dir", str(tmp_path)])
    cli.main(["claim", "fixture-code", "--dir", str(tmp_path)])

    call.assert_awaited_once_with(f"{origin}/mcp", key, "login_link", {})
    claim.assert_called_once_with(f"{origin}/claim/fixture-code", key, routine=False)


@pytest.mark.parametrize(("dotenv_port", "env_port", "expected_port"), [
    ("SWITCHBOARD_PORT=5678", ("SWITCHBOARD_PORT", "4321"), "4321"),
    ("SWITCHBOARD_PORT=5678", ("PORT", "4321"), "5678"),
    ("", ("PORT", "4321"), "4321"),
])
def test_client_merges_process_environment_and_instance_settings(tmp_path, monkeypatch, dotenv_port, env_port, expected_port):
    key = "fixture-owner-key-not-a-secret"
    (tmp_path / ".env").write_text(f"{cli.OWNER_KEY}={key}\nSWITCHBOARD_HOST=127.0.0.1\nPORT=1234\n{dotenv_port}\n")
    for name in (cli.OWNER_KEY, "PORT", "SWITCHBOARD_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SWITCHBOARD_HOST", "::1")
    monkeypatch.setenv(*env_port)
    call = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(cli, "_call", call)

    cli.main(["status", "--dir", str(tmp_path)])

    call.assert_awaited_once_with(f"http://[::1]:{expected_port}/mcp", key, "switchboard_status", {})


def test_explicit_url_overrides_both_local_port_settings_and_host(tmp_path, monkeypatch):
    key = "fixture-owner-key-not-a-secret"
    (tmp_path / ".env").write_text(f"{cli.OWNER_KEY}={key}\nSWITCHBOARD_HOST=::1\nSWITCHBOARD_PORT=5678\nPORT=1234\n")
    monkeypatch.delenv(cli.OWNER_KEY, raising=False)
    call = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(cli, "_call", call)

    cli.main(["status", "--dir", str(tmp_path), "--url", "https://swb.example"])

    call.assert_awaited_once_with("https://swb.example/mcp", key, "switchboard_status", {})


def test_status_uses_swb_branding_if_connection_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.OWNER_KEY, "fixture-owner-key-not-a-secret")
    monkeypatch.setattr(cli, "streamable_http_client", Mock(side_effect=OSError))
    with pytest.raises(SystemExit) as exited:
        cli.main(["status", "--dir", str(tmp_path)])
    assert exited.value.code == 1
    assert capsys.readouterr() == ("", "swb: Check that SwB is running. Verify --url and the owner key.\n")


@pytest.mark.parametrize(("claim", "endpoint", "expected", "success"), [
    ("fixture-code", None, "http://localhost:8870/claim/fixture-code", "Token saved."),
    ("https://other.example.test/claim/fixture-code", "https://switchboard.example.test",
     "https://switchboard.example.test/claim/fixture-code", "Token saved."),
    ("https://other.example.test/prefix/routines/claim/fixture-code", "https://switchboard.example.test/prefix/mcp",
     "https://switchboard.example.test/prefix/routines/claim/fixture-code", "Routine saved."),
])
def test_claim_prompts_and_posts_to_configured_origin(tmp_path, monkeypatch, capsys, claim, endpoint, expected, success):
    key, token = "fixture-owner-key-not-a-secret", "fixture-token-not-a-secret"
    (tmp_path / ".env").write_text(f"{cli.OWNER_KEY}={key}\n")
    for name in ("PORT", "SWITCHBOARD_HOST", "SWITCHBOARD_PORT"):
        monkeypatch.delenv(name, raising=False)
    prompt = Mock(return_value=token)
    monkeypatch.setattr(cli.getpass, "getpass", prompt)

    def post(request):
        assert request.method == "POST" and str(request.url) == expected
        assert request.headers["Authorization"] == f"Bearer {key}"
        assert parse_qs(request.content.decode()) == {"token": [token]}
        return httpx2.Response(200, text=token)

    client = httpx2.Client
    monkeypatch.setattr(cli.httpx2, "Client", lambda **kw: client(transport=httpx2.MockTransport(post), **kw))
    args = ["claim", claim, "--dir", str(tmp_path)]
    if endpoint:
        args += ["--url", endpoint]
    cli.main(args)
    prompt.assert_called_once_with("Token: ")
    out, err = capsys.readouterr()
    assert success in out
    assert all(value not in out + err for value in (key, token, "fixture-code"))


@pytest.mark.parametrize("link", [
    "https://other.example.test/connect/fixture-code",
    "https://other.example.test/claim/fixture-code?redirect=other",
    "https://other.example.test/claim/../fixture-code",
    "https://other.example.test//claim/fixture-code",
])
def test_claim_rejects_unexpected_paths_before_prompting(tmp_path, monkeypatch, capsys, link):
    monkeypatch.setenv(cli.OWNER_KEY, "fixture-owner-key-not-a-secret")
    prompt = Mock()
    monkeypatch.setattr(cli.getpass, "getpass", prompt)
    with pytest.raises(SystemExit):
        cli.main(["claim", link, "--dir", str(tmp_path)])
    prompt.assert_not_called()
    err = capsys.readouterr().err
    assert "claim link or code" in err and "fixture-code" not in err


def test_claim_does_not_follow_redirects_or_print_response(tmp_path, monkeypatch, capsys):
    key, token, code = "fixture-owner-key-not-a-secret", "fixture-token-not-a-secret", "fixture-code"
    monkeypatch.setenv(cli.OWNER_KEY, key)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: token)
    calls = []

    def post(request):
        calls.append(request.url.host)
        return httpx2.Response(307, headers={"Location": "https://other.example.test/"}, text=f"{key} {token} {code}")

    client = httpx2.Client
    monkeypatch.setattr(cli.httpx2, "Client", lambda **kw: client(transport=httpx2.MockTransport(post), **kw))
    with pytest.raises(SystemExit):
        cli.main(["claim", code, "--dir", str(tmp_path), "--url", "https://switchboard.example.test"])
    assert calls == ["switchboard.example.test"]
    out, err = capsys.readouterr()
    assert "HTTP 307" in err
    assert all(value not in out + err for value in (key, token, code))


def test_claim_requires_hidden_input(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.OWNER_KEY, "fixture-owner-key-not-a-secret")

    def prompt(_):
        warnings.warn("Cannot turn echo off.", cli.getpass.GetPassWarning)
        pytest.fail("Claim fell back to visible input")

    monkeypatch.setattr(cli.getpass, "getpass", prompt)
    with pytest.raises(SystemExit):
        cli.main(["claim", "fixture-code", "--dir", str(tmp_path)])
    assert "hidden input" in capsys.readouterr().err


@pytest.mark.parametrize("result_kind", ["structured", "text", "error", "empty error"])
async def test_mcp_transport_initializes_and_sends_bearer(monkeypatch, result_kind):
    calls = []
    url, key = "https://switchboard.example.test/mcp", "fixture-owner-key-not-a-secret"

    @asynccontextmanager
    async def transport(url, *, http_client):
        assert isinstance(http_client, httpx2.AsyncClient)
        calls.append((url, http_client.headers["Authorization"]))
        yield "read", "write"

    class Session:
        def __init__(self, read, write, **kwargs):
            assert (read, write) == ("read", "write")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def initialize(self):
            calls.append("initialize")

        async def call_tool(self, tool, arguments):
            calls.append((tool, arguments))
            if result_kind in ("error", "empty error"):
                content = [TextContent(type="text", text=f"Set SWITCHBOARD_PUBLIC_URL. Key {key}.")]
                return CallToolResult(content=content if result_kind == "error" else [], is_error=True)
            return CallToolResult(content=[TextContent(type="text", text='{"what":"mcp-client"}')],
                                  structured_content={"what": "mcp-client"} if result_kind == "structured" else None)

    monkeypatch.setattr(cli, "streamable_http_client", transport)
    monkeypatch.setattr(cli, "ClientSession", Session)
    if result_kind == "error":
        with pytest.raises(cli.CliError) as refused:
            await cli._call(url, key, "connect", {"what": "mcp-client"})
        assert str(refused.value) == "Set SWITCHBOARD_PUBLIC_URL. Key [redacted]."
    elif result_kind == "empty error":
        with pytest.raises(cli.CliError, match="SwB refused the connect tool call"):
            await cli._call(url, key, "connect", {"what": "mcp-client"})
    else:
        assert await cli._call(url, key, "connect", {"what": "mcp-client"}) == {"what": "mcp-client"}
    assert calls == [(url, f"Bearer {key}"), "initialize", ("connect", {"what": "mcp-client"})]


@pytest.mark.parametrize(("path", "expected"), [
    ("/claim/fixture-code?extra=private", "/claim/[redacted]"),
    ("/routines/claim/fixture-code/token", "/routines/claim/[redacted]"),
    ("/authorize?code=fixture-code", "/authorize"),
    ("/healthz", "/healthz"),
])
def test_uvicorn_access_filter_redacts_only_sensitive_path_parts(path, expected):
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1", "GET", path, "1.1", 200), None)
    assert __main__._NoQuery().filter(record)
    assert record.getMessage() == f'127.0.0.1 - "GET {expected} HTTP/1.1" 200'
