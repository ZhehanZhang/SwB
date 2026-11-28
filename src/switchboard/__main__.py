from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from contextlib import ExitStack
from logging.config import dictConfig
from pathlib import Path
from socket import socket

import uvicorn
from uvicorn.main import STARTUP_FAILURE

from .config import Instance
from .entrypoint import cli_main
from .env import env_value

SERVER_COMMANDS = ("serve", "export", "log", "config", "blocks", "targets")


class _NoQuery(logging.Filter):
    """Access logs never carry OAuth query strings, claim codes or hook wake links."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            path = args[2].split("?", 1)[0]
            for prefix in ("/claim/", "/routines/claim/", "/claims/", "/hooks/muse/"):
                if path.startswith(prefix):
                    path = f"{prefix}[redacted]"
                    break
            args[2] = path
            record.args = tuple(args)
        return True


def add_server_commands(sub: argparse._SubParsersAction) -> None:
    serve = sub.add_parser("serve", help="run the SwB server (default)")
    serve.add_argument("--host", help="bind address from SWB_HOST. The default is 127.0.0.1.")
    exp = sub.add_parser("export", help="write every memory as markdown, one folder per scope")
    exp.add_argument("destination", metavar="DIR")
    log = sub.add_parser("log", help="print the recent audit log")
    log.add_argument("--limit", type=int, default=50, help="maximum rows to print (default 50)")
    cfg = sub.add_parser("config", help="config tools")
    migrate_help = "Print a version 1 config rewritten as version 2."
    migrate = cfg.add_subparsers(dest="action", required=True).add_parser("migrate", help=migrate_help, description=migrate_help)
    migrate.add_argument("file", nargs="?", help="config file from SWB_CONFIG by default")
    blk = sub.add_parser("blocks", help="the configured blocks")
    block_actions = blk.add_subparsers(dest="action", required=True)
    nested = [migrate]
    for action, description in (("list", "List configured blocks."), ("show", "Print block details."), ("check", "Check block setup.")):
        command = block_actions.add_parser(action, help=description, description=description)
        command.add_argument("block", nargs="?", help="block name")
        nested.append(command)
    targets = sub.add_parser("targets", help="every target send can reach, with slugs and levels")
    dir_help = "instance directory for config, data and .env"
    for command in (serve, exp, log, cfg, blk, targets):
        command.add_argument("--dir", type=Path, help=dir_help)
    for command in nested:
        command.add_argument("--dir", type=Path, help=dir_help, default=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swb")
    add_server_commands(parser.add_subparsers(dest="cmd"))
    return parser


def main(argv: list[str] | None = None, *, listener: socket | None = None, tunnel_url: str | None = None,
         instance: Instance | None = None, announce: bool = False,
         announce_login: bool = False,
         on_home_fallback: Callable[[Path, Path], None] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "log" and not 1 <= args.limit < 2 ** 63:
        parser.error("Set --limit between 1 and 9223372036854775807.")

    from .config import ConfigError, bind_address, config_path, local_origin, migrate_text
    from .core import Switchboard
    from .deployment import OWNER_KEY, serve_env

    serving = args.cmd in (None, "serve")
    env = dict(os.environ)
    directory = getattr(args, "dir", None)
    if directory is not None or not serving:
        from .cli import CliError, _read_env

        base = directory.resolve() if directory is not None else Path.cwd()
        instance = Instance(base, explicit=True) if directory is not None else Instance(base, bare_home=False)
        try:
            env = {**_read_env(base / ".env"), **env}
        except (CliError, OSError, UnicodeError):
            parser.exit(2, "swb: Check the instance .env file.\n")

    logging.basicConfig(level=env.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "config":
        path = config_path(args.file, env, instance=instance if args.file is None else None)
        if path is None or not path.is_file():
            parser.error("Choose an existing config file.")
        print(migrate_text(path.read_text()), end="")
        return

    port = int(env.get("PORT", "8000")) if serving else 0
    host, origin = bind_address(env, port, host=getattr(args, "host", None))
    loopback_origin = local_origin(host, origin, env) if port else None
    try:
        deployment_env = serve_env(env, instance=instance) if serving else None
    except ConfigError as error:
        parser.exit(2, f"swb: {error}\n")
    except (OSError, UnicodeError) as error:
        parser.exit(2, f"swb: Check the owner key on the volume ({type(error).__name__}).\n")
    with ExitStack() as startup:
        if announce_login:
            dictConfig(uvicorn.config.LOGGING_CONFIG)
            startup_logs = _StartupLogs()
            startup.callback(startup_logs.close)
            startup.callback(startup_logs.flush)
            startup_logs.defer(logging.getLogger())
            startup_logs.defer(logging.getLogger("uvicorn"))
            startup_logs.defer(logging.getLogger("uvicorn.access"))
        try:
            core = Switchboard(tunnel_url=tunnel_url, env=deployment_env if deployment_env is not None else env,
                               loopback_origin=loopback_origin, instance=instance)
        except ConfigError as error:
            parser.exit(2, f"swb: {error}\n")

        if serving:
            from .app import create_app

            for problem in core.health()["problems"]:
                logging.getLogger("switchboard").warning("degraded: %s", problem)
            if deployment_env is not None and core.cfg.public_url:
                logging.getLogger("switchboard").info("Sign-in page %s/login", core.cfg.public_url)
            logging.getLogger("uvicorn.access").addFilter(_NoQuery())

            def first_login() -> None:
                principal = core.auth.verify_bearer(env_value(core.cfg.env, OWNER_KEY))
                if principal is not None and principal.is_unrestricted_owner and principal.source == "owner" and core.cfg.public_url:
                    token = core.auth.sessions.mint_link(principal.client_id, principal.source)
                    core.audit.record(origin="cli", source=principal.source, client=principal.client_id, destination="core",
                                      action="login_link_minted", outcome="ok")
                    print(f"{core.cfg.public_url}/login#t={token}", flush=True)
                startup.close()

            app = create_app(core, on_started=first_login if announce_login else None)
            options = {
                "host": host,
                "port": port,
                "proxy_headers": False,
                "timeout_graceful_shutdown": 5,
            }
            if announce_login:
                options["log_config"] = None
            if on_home_fallback is not None and core.cfg.legacy_instance_data is not None:
                on_home_fallback(core.data_dir, core.cfg.legacy_instance_data)
            if announce:
                print(f"MCP URL: {core.cfg.public_url or origin}/mcp", flush=True)
            if listener is None:
                uvicorn.run(app, **options)
            else:
                server = uvicorn.Server(uvicorn.Config(app, **options))
                server.run(sockets=[listener])
                if not server.started:
                    raise SystemExit(STARTUP_FAILURE)
            return

    if args.cmd == "blocks":
        import yaml

        if args.block and args.block not in core.blocks:
            parser.exit(1, f"Configure the {args.block} block or choose a block from swb blocks list.\n")
        names = [args.block] if args.block else list(core.blocks)
        setup = core.block_setup() if args.action == "check" else {}
        for name in names:
            b = core.blocks[name]
            if args.action == "list":
                m = b.manifest
                print(f"{name:12} {m.id:10} {m.kind:6} out {m.level_out or '-':3} in {m.level_in or '-':7} {'online' if b.online else 'offline'}")
            elif args.action == "show":
                print(yaml.safe_dump({name: b.manifest.as_dict()}, sort_keys=False, allow_unicode=True), end="")
            else:
                missing = setup.get(name, {}).get("missing_env", ())
                if missing:
                    for key in missing:
                        print(f"{name} requires {key}.")
                elif step := b.config_step():
                    print(f"{name} needs setup. {step}")
                elif b.problems():
                    print(f"Check the {name} config with swb status.")
                else:
                    print(f"{name} is ready.")
        return
    if args.cmd == "targets":
        import asyncio

        for t in asyncio.run(core.targets(refresh=True)):
            print(f"{t['address']:48} {t.get('level') or '-':3} {', '.join(t['slugs'])}")
        return

    if args.cmd == "export":
        n = core.memory.export(args.destination)
        print(f"exported {n} memories to {args.destination}")
        return
    if args.cmd == "log":
        import json

        for entry in core.audit.recent(limit=args.limit):
            print(json.dumps(entry, ensure_ascii=False))
        return


class _StartupLogs(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._handlers: dict[logging.Logger, list[logging.Handler]] = {}
        self._records: list[logging.LogRecord] = []

    def defer(self, logger: logging.Logger) -> None:
        self._handlers[logger] = logger.handlers
        logger.handlers = [self]

    def emit(self, record: logging.LogRecord) -> None:
        if self._handlers:
            self._records.append(record)
        else:
            logging.getLogger(record.name).callHandlers(record)

    def flush(self) -> None:
        with self.lock:
            for logger, handlers in self._handlers.items():
                logger.handlers = handlers
            self._handlers.clear()
            for record in self._records:
                logging.getLogger(record.name).callHandlers(record)
            self._records.clear()


if __name__ == "__main__":
    cli_main()
