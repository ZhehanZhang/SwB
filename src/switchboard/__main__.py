from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from socket import socket

import uvicorn
from uvicorn.main import STARTUP_FAILURE

SERVER_COMMANDS = ("serve", "export", "log", "config", "blocks", "targets")


def cli_main(argv: list[str] | None = None) -> None:
    from .cli import main as client

    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in SERVER_COMMANDS:
        main(args)
    elif not args and Path(sys.argv[0]).stem in ("switchboard", "__main__"):
        main(["serve"])
    else:
        client(args or ["--help"])


class _NoQuery(logging.Filter):
    """Access logs never carry OAuth query strings or claim codes."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            path = args[2].split("?", 1)[0]
            for prefix in ("/claim/", "/routines/claim/", "/claims/"):
                if path.startswith(prefix):
                    path = f"{prefix}[redacted]"
                    break
            args[2] = path
            record.args = tuple(args)
        return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swb")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="run the server (default)")
    serve.add_argument("--host", help="bind address from SWITCHBOARD_HOST or 127.0.0.1 by default")
    exp = sub.add_parser("export", help="write every memory as markdown, one folder per scope")
    exp.add_argument("dir")
    sub.add_parser("log", help="print the recent audit log")
    cfg = sub.add_parser("config", help="config tools")
    cfg.add_argument("action", choices=["migrate"], help="migrate: print a version 1 config rewritten as version 2")
    cfg.add_argument("file", nargs="?", help="config file (default: SWITCHBOARD_CONFIG)")
    blk = sub.add_parser("blocks", help="the configured blocks")
    blk.add_argument("action", choices=["list", "show", "check"])
    blk.add_argument("block", nargs="?")
    sub.add_parser("targets", help="every target send can reach, with slugs and levels")
    return parser


def main(argv: list[str] | None = None, *, listener: socket | None = None, tunnel_url: str | None = None,
         data_dir: Path | None = None, announce: bool = False, on_start: Callable[[], None] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    from .core import Switchboard

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "config":
        from .config import config_path, migrate_text

        path = config_path(args.file, os.environ)
        if path is None:
            parser.error("no config file found")
        print(migrate_text(path.read_text()), end="")
        return
    from .config import ConfigError, LOOPBACK_HOSTS, bind_address, instance_paths
    from .deployment import serve_env

    serving = args.cmd in (None, "serve")
    port = int(os.environ.get("PORT", "8000")) if serving else 0
    host, origin = bind_address(os.environ, port, host=getattr(args, "host", None))
    loopback_origin = origin if host in LOOPBACK_HOSTS and port else None
    try:
        env = serve_env(os.environ) if serving else None
    except ConfigError as error:
        parser.exit(2, f"swb: {error}\n")
    except (OSError, UnicodeError) as error:
        parser.exit(2, f"swb: Check the owner key on the volume ({type(error).__name__}).\n")
    if not serving:
        from .cli import CliError, _read_env

        base = Path.cwd()
        try:
            env = {**_read_env(base / ".env"), **os.environ}
        except (CliError, OSError, UnicodeError):
            parser.exit(2, "swb: Check the instance .env file.\n")
        config, instance_data, _ = instance_paths(base, env, explicit=False)
        if config is not None:
            env["SWITCHBOARD_CONFIG"] = str(config)
            data_dir = instance_data
    try:
        core = Switchboard(data_dir=data_dir, tunnel_url=tunnel_url, env=env, loopback_origin=loopback_origin)
    except ConfigError as error:
        parser.exit(2, f"swb: {error}\n")
    if args.cmd == "blocks":
        import yaml

        names = [args.block] if args.block else list(core.blocks)
        for name in names:
            b = core.blocks[name]
            if args.action == "list":
                m = b.manifest
                print(f"{name:12} {m.id:10} {m.kind:6} out {m.level_out or '-':3} in {m.level_in or '-':7} {'online' if b.online else 'offline'}")
            elif args.action == "show":
                print(yaml.safe_dump({name: b.manifest.as_dict()}, sort_keys=False, allow_unicode=True), end="")
            else:
                print(f"{name}: " + ("; ".join(b.problems()) or "ok"))
        return
    if args.cmd == "targets":
        import asyncio

        for t in asyncio.run(core.targets(refresh=True)):
            print(f"{t['address']:48} {t.get('level') or '-':3} {', '.join(t['slugs'])}")
        return

    if args.cmd == "export":
        n = core.memory.export(args.dir)
        print(f"exported {n} memories to {args.dir}")
        return
    if args.cmd == "log":
        import json

        for entry in core.audit.recent(limit=50):
            print(json.dumps(entry, ensure_ascii=False))
        return

    from .app import create_app

    for problem in core.health()["problems"]:
        logging.getLogger("switchboard").warning("degraded: %s", problem)
    if env is not None and core.cfg.public_url:
        logging.getLogger("switchboard").info("Sign-in page %s/login", core.cfg.public_url)
    logging.getLogger("uvicorn.access").addFilter(_NoQuery())
    app = create_app(core)
    options = {
        "host": host,
        "port": port,
        "proxy_headers": True,
        "forwarded_allow_ips": os.environ.get("FORWARDED_ALLOW_IPS", "*"),
        "timeout_graceful_shutdown": 5,
    }
    if on_start is not None:
        on_start()
    if announce:
        print(f"MCP URL: {core.cfg.public_url or origin}/mcp", flush=True)
    if listener is None:
        uvicorn.run(app, **options)
    else:
        server = uvicorn.Server(uvicorn.Config(app, **options))
        server.run(sockets=[listener])
        if not server.started:
            raise SystemExit(STARTUP_FAILURE)


if __name__ == "__main__":
    cli_main()
