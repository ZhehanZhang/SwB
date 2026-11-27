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

OWNER_KEY = "SWITCHBOARD_KEY_OWNER"
CONFIG = Path("config/switchboard.yaml")
LOOPBACK = ("localhost", "127.0.0.1", "::1")


class CliError(ValueError):
    pass


def _url(value: str, *, public: bool = False) -> str:
    message = ("--public-url must be an HTTPS base URL or use http://localhost"
               if public else "--url must be HTTPS or use a loopback address for HTTP")
    try:
        url = urlsplit(value)
        port = url.port
        host = url.hostname
    except ValueError:
        raise CliError(message) from None
    if not host or port == 0 or url.username is not None or re.search(r"[\s?#]", value):
        raise CliError(message)
    local = ("localhost",) if public else LOOPBACK
    if url.scheme != "https" and not (url.scheme == "http" and host in local):
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
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _write_file(config_path, yaml.safe_dump(config, sort_keys=False), force=force, mode=0o644)
    _write_file(env_path, f"{OWNER_KEY}={secrets.token_hex(32)}\n", force=force, mode=0o600)
    start = "swb up" if base == Path.cwd() else f"swb up --dir {shlex.quote(str(base))}"
    print(f"Config: {config_path}\nOwner key saved in {env_path}.\nNext: {start}")


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


def _up(base: Path, env: dict[str, str]) -> None:
    from .__main__ import main as server

    for name, value in env.items():
        os.environ.setdefault(name, value)
    os.environ["SWITCHBOARD_CONFIG"] = str(base / CONFIG)
    os.environ["SWITCHBOARD_DATA"] = str(base / "data")
    server(["serve"])


def _choices(groups: list[list[str]]) -> dict[str, str | bool]:
    choices: dict[str, str | bool] = {}
    for group in groups:
        for pair in group:
            key, separator, value = pair.partition("=")
            if not key or not separator or any(char.isspace() for char in key):
                raise CliError("--choose expects key=value pairs.")
            if key in choices:
                raise CliError("--choose cannot repeat a key.")
            if key in ("create_new", "primary"):
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
        invalid = CliError("Provide a Switchboard claim link or code.")
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
        with httpx2.Client(headers={"Authorization": f"Bearer {key}"}, timeout=30,
                           follow_redirects=False, trust_env=False) as client:
            response = client.post(url, data={"token": token})
    except (httpx2.HTTPError, ValueError):
        raise CliError("Couldn't submit the claim. Check that Switchboard is running and verify --url.") from None
    if not response.is_success:
        raise CliError(f"Switchboard did not save the token (HTTP {response.status_code}). Check the claim link and owner key.")
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
        raise CliError("Couldn't reach or authenticate to Switchboard. Check that it's running and verify --url and the owner key.") from None
    if result.is_error:
        raise CliError(f"Switchboard refused the {tool} tool call.")
    payload = result.structured_content
    if payload is None and len(result.content) == 1 and result.content[0].type == "text":
        try:
            payload = json.loads(result.content[0].text)
        except ValueError:
            pass
    if not isinstance(payload, dict):
        raise CliError("Switchboard returned an unexpected MCP result.")
    return payload


def _display(tool: str, payload: dict[str, Any]) -> str:
    if tool == "switchboard_status":
        steps = payload.get("next_steps") or []
        return "\n".join([f"Status: {payload.get('status', 'unknown')}", "Next steps:",
                          *([f"  - {step}" for step in steps] or ["  None."])])
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="swb")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a minimal config and a local owner key")
    init.add_argument("--public-url", help="optional public base URL")
    init.add_argument("--force", action="store_true", help="replace both config and owner key")
    up = commands.add_parser("up", help="run the Switchboard server")
    connect = commands.add_parser("connect", help="get or advance connection steps")
    connect.add_argument("what", help="connection kind, for example mcp-client")
    connect.add_argument("--choose", action="append", nargs="+", default=[], metavar="KEY=VALUE")
    status = commands.add_parser("status", help="show server status and next steps")
    claim = commands.add_parser("claim", help="save a token using a one-time claim link")
    claim.add_argument("link", metavar="LINK-OR-CODE", help="claim link, or the code for a generic token claim")
    muse = commands.add_parser("muse-bridge", help="pair and run SwB as a Muse gadget on Linux")
    muse.add_argument("--url", required=True, help="SwB base URL or MCP endpoint")
    muse.add_argument("--setup", action="store_true", help="enter the bridge token and pair in the Muse app")
    muse.add_argument("--pair-again", action="store_true", help="replace this gadget's Meta pairing (requires --setup)")
    muse.add_argument("--state-dir", type=Path, default=Path.home() / ".config" / "swb" / "muse")
    for command in (init, up, connect, status, claim):
        command.add_argument("--dir", type=Path, default=Path("."), help="instance directory (default: current directory)")
    for command in (connect, status, claim):
        command.add_argument("--url", help="server base URL or MCP endpoint (default: http://127.0.0.1:8000/mcp)")
    args = parser.parse_args(argv)
    try:
        if args.command == "muse-bridge":
            from .muse_gadget import run_gadget

            url = _url(args.url).removesuffix("/mcp")
            run_gadget(url, args.state_dir, setup=args.setup, pair_again=args.pair_again)
            return
        base = args.dir.resolve()
        if args.command == "init":
            _init(base, args.public_url, args.force)
            return
        env = _read_env(base / ".env")
        if args.command == "up":
            _up(base, env)
            return
        arguments = {}
        if args.command == "connect":
            arguments["what"] = args.what
            if choose := _choices(args.choose):
                arguments["choose"] = choose
        key = os.environ.get(OWNER_KEY, env.get(OWNER_KEY))
        if not key:
            raise CliError(f"{OWNER_KEY} is missing. Run swb init or use --dir to locate your instance.")
        port = os.environ.get("PORT", env.get("PORT", "8000"))
        url = _url(args.url or f"http://127.0.0.1:{port}/mcp")
        request_log = logging.getLogger("httpx2" if args.command == "claim" else "mcp")
        level = request_log.level
        try:
            request_log.setLevel(logging.CRITICAL + 1)
            if args.command == "claim":
                claim_url, routine = _claim_url(url, args.link)
                _claim(claim_url, key, routine=routine)
                return
            tool = "connect" if args.command == "connect" else "switchboard_status"
            payload = asyncio.run(_call(url, key, tool, arguments))
        finally:
            request_log.setLevel(level)
        print(_display(tool, payload).replace(key, "[redacted]"))
    except CliError as error:
        parser.exit(1, f"swb: {error}\n")
    except (OSError, UnicodeError) as error:
        parser.exit(1, f"swb: couldn't read or write the instance files ({type(error).__name__}).\n")
