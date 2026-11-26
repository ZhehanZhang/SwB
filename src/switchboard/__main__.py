from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn


class _NoQuery(logging.Filter):
    """Access logs never carry OAuth query strings or claim codes."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            path = args[2].split("?", 1)[0]
            for prefix in ("/claim/", "/routines/claim/"):
                if path.startswith(prefix):
                    path = f"{prefix}[redacted]"
                    break
            args[2] = path
            record.args = tuple(args)
        return True


def main(argv: list[str] | None = None) -> None:
    from .core import Switchboard

    parser = argparse.ArgumentParser(prog="switchboard")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the server (default)")
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
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = os.environ.get("SWITCHBOARD_CONFIG", "/config/switchboard.yaml")
    data = os.environ.get("SWITCHBOARD_DATA", "/data")
    if args.cmd == "config":
        from pathlib import Path

        from .config import migrate_text

        print(migrate_text(Path(args.file or config).read_text()), end="")
        return
    core = Switchboard(config, data)
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
        for r in core.audit.recent(limit=50):
            print(r)
        return

    from .app import create_app

    for problem in core.health()["problems"]:
        logging.getLogger("switchboard").warning("degraded: %s", problem)
    logging.getLogger("uvicorn.access").addFilter(_NoQuery())
    uvicorn.run(create_app(core), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=True, forwarded_allow_ips="*", timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main(sys.argv[1:])
