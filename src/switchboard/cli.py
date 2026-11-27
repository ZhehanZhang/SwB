from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import re
import secrets
import shlex
import tempfile
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from . import __version__
from .__main__ import SERVER_COMMANDS, add_server_commands
from .config import INSTANCE_CONFIG, ConfigError, Instance, LOOPBACK_HOSTS, client_endpoint
from .deployment import OWNER_KEY, volume_owner_key
from .store import ensure_private_data_dir

CONFIG = INSTANCE_CONFIG
CLAIM_ERRORS = {400: "claim_input", 403: "claim_owner_key", 410: "claim_unavailable"}


class CliError(ValueError):
    pass


def _url(value: str, *, public: bool = False) -> str:
    message = ("--public-url must be an HTTPS base URL or use a loopback address for HTTP"
               if public else "--url must be HTTPS or use a loopback address for HTTP")
    try:
        url = urlsplit(value)
        port = url.port
        host = url.hostname
    except ValueError:
        raise CliError(message) from None
    if not host or port == 0 or url.username is not None or re.search(r"[\s?#]", value):
        raise CliError(message)
    if url.scheme != "https" and not (url.scheme == "http" and host in LOOPBACK_HOSTS):
        raise CliError(message)
    path = url.path.rstrip("/")
    if not public and not path:
        path = "/mcp"
    return url._replace(path=path).geturl()


def _write_file(path: Path, text: str, *, force: bool, mode: int) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(text)
            f.close()
            temporary.chmod(mode)
            if force:
                temporary.replace(path)
            else:
                path.hardlink_to(temporary)
        finally:
            temporary.unlink(missing_ok=True)


def _init(base: Path, public_url: str | None, force: bool) -> None:
    config = {"version": 2, "blocks": {"mcp": {"use": "mcp"}}}
    if public_url is not None:
        config["public_url"] = _url(public_url, public=True)
    config_path, env_path = base / CONFIG, base / ".env"
    for path in (config_path, env_path):
        if not force and os.path.lexists(path):
            raise CliError(f"{path} already exists; use --force to replace it.")
    if not os.path.lexists(config_path):
        ensure_private_data_dir(base / "data")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _write_file(config_path, yaml.safe_dump(config, sort_keys=False), force=force, mode=0o644)
    _write_file(env_path, f"{OWNER_KEY}={secrets.token_hex(32)}\n", force=force, mode=0o600)
    start = "swb up" if base == Path.cwd() else f"swb up --dir {shlex.quote(str(base))}"
    print(f"Config: {config_path}\nOwner key saved in {env_path}.\nNext: {start}, then {start.replace('swb up', 'swb login-link', 1)} to sign in.")


def _read_env(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    values = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        error = CliError(f"Invalid .env assignment on line {number}.")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key):
            raise error
        if value.startswith(("'", '"')):
            try:
                quoted = shlex.split(value, comments=True)
            except ValueError:
                raise error from None
            if len(quoted) != 1:
                raise error
            value = quoted[0]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0]
        values[key] = value
    return values


def _choices(groups: list[list[str]], *, kind: str = "") -> dict[str, str | bool]:
    choices: dict[str, str | bool] = {}
    for group in groups:
        for pair in group:
            key, separator, value = pair.partition("=")
            if not key or not separator or any(char.isspace() for char in key):
                raise CliError("--choose expects key=value pairs.")
            if key in choices:
                raise CliError("--choose cannot repeat a key.")
            if key in ("create_new", "primary") or (key == "replace" and kind.strip().lower() == "muse"):
                if value.lower() not in ("true", "false"):
                    raise CliError(f"--choose {key} expects true or false.")
                choices[key] = value.lower() == "true"
            else:
                choices[key] = value
    return choices


def _claim_url(endpoint: str, link: str) -> tuple[str, bool]:
    target = urlsplit(endpoint)
    prefix = target.path.removesuffix("/mcp") if target.path.endswith("/mcp") else ""
    if re.fullmatch(r"[A-Za-z0-9_-]+", link):
        path = f"/claim/{link}"
    else:
        invalid = CliError("Provide a SwB claim link or code.")
        try:
            parsed = urlsplit(link)
        except ValueError:
            raise invalid from None
        if re.search(r"[\s?#]", link) or (parsed.scheme and (parsed.scheme not in ("https", "http") or not parsed.hostname)):
            raise invalid
        path = parsed.path
        if prefix and path.startswith(prefix + "/"):
            path = path[len(prefix):]
        if not re.fullmatch(r"(?:/routines)?/claim/[A-Za-z0-9_-]+", path):
            raise invalid
    return target._replace(path=prefix + path).geturl(), path.startswith("/routines/")


def _claim(url: str, key: str, *, routine: bool) -> None:
    token = _hidden_input("Token: ")
    if not token.strip():
        raise CliError("No token entered.")
    try:
        with httpx2.Client(headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=30,
                           follow_redirects=False, trust_env=False) as client:
            response = client.post(url, data={"token": token})
    except (httpx2.HTTPError, ValueError):
        raise CliError("Check that SwB is running and verify --url.") from None
    if not response.is_success:
        status = response.status_code
        if status in CLAIM_ERRORS and response.headers.get("content-type", "").partition(";")[0] == "application/json":
            try:
                error = response.json() if len(response.content) <= 4096 else None
            except ValueError:
                error = None
            if isinstance(error, dict) and error.get("error") == CLAIM_ERRORS[status]:
                message = error.get("message")
                if (isinstance(message, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .()'/+-]{0,159}", message)
                        and not any(secret in message for secret in (token.strip(), key, url.rsplit("/", 1)[-1]))):
                    raise CliError(message)
        raise CliError(f"Check the claim link and owner key (HTTP {status}).")
    print("Routine saved." if routine else "Token saved. Run swb connect again to check the next step.")


def _hidden_input(prompt: str) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise CliError("Use a terminal with hidden input to enter the token.") from None


async def _call(url: str, key: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30, trust_env=False) as client,
            streamable_http_client(url, http_client=client) as streams,
            ClientSession(*streams, read_timeout_seconds=30) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool, arguments)
    except Exception:
        raise CliError("Check that SwB is running. Verify --url and the owner key.") from None
    if result.is_error:
        reason = "\n".join(item.text for item in result.content if item.type == "text" and item.text.strip())
        raise CliError(reason.replace(key, "[redacted]") or f"SwB refused the {tool} tool call.")
    payload = result.structured_content
    if payload is None and len(result.content) == 1 and result.content[0].type == "text":
        try:
            payload = json.loads(result.content[0].text)
        except ValueError:
            pass
    if not isinstance(payload, dict):
        raise CliError("SwB returned an unexpected MCP result.")
    return payload


def _display(tool: str, payload: dict[str, Any]) -> str:
    if tool == "login_link":
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            raise CliError("Ask SwB for a new sign-in link.")
        return url
    if tool == "switchboard_status":
        steps = payload.get("next_steps") or []
        lines = [f"Status: {payload.get('status', 'unknown')}"]
        if blocks := payload.get("block_setup"):
            lines.append("Blocks:")
            for name, block in blocks.items():
                state = "on" if block["enabled"] else "off"
                missing = ", ".join(block["missing_env"])
                lines.append(f"  - {name}: {state}" + (f" (missing env: {missing})" if missing else ""))
        return "\n".join([*lines, "Next steps:", *([f"  - {step}" for step in steps] or ["  None."])])
    lines = [str(payload.get("what", "Connect"))]
    if payload.get("state"):
        lines[0] += f": {payload['state']}"
    if payload.get("error"):
        lines.append(str(payload["error"]))
    if payload.get("kinds"):
        lines.append("Supported kinds: " + ", ".join(payload["kinds"]))
    if payload.get("done"):
        lines.extend(["Done:", *(f"  - {line}" for line in payload["done"])])
    if payload.get("next"):
        lines.append("Next steps:")
        for step in payload["next"]:
            lines.append(f"  - {step['by']}: {step['do']}")
            if step.get("call"):
                lines.append(f"    Call: {json.dumps(step['call'], ensure_ascii=False)}")
            if step.get("link"):
                lines.append(f"    {step['link']}")
            if step.get("snippet"):
                lines.extend(f"    {line}" for line in step["snippet"].splitlines())
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swb")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a minimal config and a local owner key")
    init.add_argument("--public-url", help="optional public base URL")
    init.add_argument("--force", action="store_true", help="replace both config and owner key")
    up = commands.add_parser("up", help="run the SwB server")
    up.add_argument("--host", help="bind address from SWITCHBOARD_HOST or 127.0.0.1 by default")
    up.add_argument("--tunnel", choices=("cloudflare", "tailscale"), help="publish the local server over HTTPS")
    connect = commands.add_parser("connect", help="get or advance connection steps")
    connect.add_argument("what", help="connection kind, for example mcp-client")
    connect.add_argument("--choose", action="append", nargs="+", default=[], metavar="KEY=VALUE")
    status = commands.add_parser("status", help="show server status and next steps")
    login_link = commands.add_parser("login-link", help="mint a one-time owner sign-in link")
    claim = commands.add_parser("claim", help="save a token using a one-time claim link")
    claim.add_argument("link", metavar="LINK-OR-CODE", help="claim link, or the code for a generic token claim")
    muse = commands.add_parser("muse-bridge", help="pair and run SwB as a Muse gadget on Linux")
    muse.add_argument("--url", required=True, help="SwB base URL or MCP endpoint")
    muse.add_argument("--setup", action="store_true", help="enter the bridge token and pair in the Muse app")
    muse.add_argument("--pair-again", action="store_true", help="replace this gadget's Meta pairing (requires --setup)")
    muse.add_argument("--state-dir", type=Path, default=Path.home() / ".config" / "swb" / "muse")
    up.add_argument("--dir", type=Path, help="use this instance directory for config, data and .env")
    for command in (init, connect, status, claim, login_link):
        command.add_argument("--dir", type=Path, help="instance directory (default: current directory)")
    for command in (connect, status, claim, login_link):
        command.add_argument("--url", help="server base URL or MCP endpoint (defaults to loopback and SWITCHBOARD_PORT, PORT, or 8870)")
    add_server_commands(commands)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in SERVER_COMMANDS:
        from .__main__ import main as server

        server(argv)
        return
    try:
        if args.command == "muse-bridge":
            from .muse_gadget import run_gadget

            url = _url(args.url).removesuffix("/mcp")
            run_gadget(url, args.state_dir, setup=args.setup, pair_again=args.pair_again)
            return
        base = (args.dir or Path.cwd()).resolve()
        if args.command == "init":
            _init(base, args.public_url, args.force)
            return
        env = _read_env(base / ".env")
        if args.command == "up":
            from .cli_up import run

            run(base, env, args.tunnel, host=args.host, instance=args.dir is not None)
            return
        arguments = {}
        if args.command == "connect":
            arguments["what"] = args.what
            if choose := _choices(args.choose, kind=args.what):
                arguments["choose"] = choose
        env = {**env, **os.environ}
        local_url = client_endpoint(env)
        url = _url(args.url or local_url)
        key = env.get(OWNER_KEY)
        if not key and url == local_url:
            key = volume_owner_key(env, instance=Instance(base, explicit=args.dir is not None))
        if not key:
            raise CliError(f"{OWNER_KEY} is missing. Run swb init or use --dir to locate your instance.")
        request_log = logging.getLogger("httpx2" if args.command == "claim" else "mcp")
        level = request_log.level
        try:
            request_log.setLevel(logging.CRITICAL + 1)
            if args.command == "claim":
                claim_url, routine = _claim_url(url, args.link)
                _claim(claim_url, key, routine=routine)
                return
            tool = {"connect": "connect", "login-link": "login_link", "status": "switchboard_status"}[args.command]
            payload = asyncio.run(_call(url, key, tool, arguments))
        finally:
            request_log.setLevel(level)
        print(_display(tool, payload).replace(key, "[redacted]"))
    except (CliError, ConfigError) as error:
        parser.exit(1, f"swb: {error}\n")
    except (OSError, UnicodeError) as error:
        parser.exit(1, f"swb: couldn't read or write the instance files ({type(error).__name__}).\n")
