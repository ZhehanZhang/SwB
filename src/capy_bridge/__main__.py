from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn


class _NoQuery(logging.Filter):
    """Access logs never carry query strings (OAuth codes and state live there)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            args[2] = args[2].split("?", 1)[0]
            record.args = tuple(args)
        return True


def main(argv: list[str] | None = None) -> None:
    from .core import Bridge

    parser = argparse.ArgumentParser(prog="capy-bridge")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the server (default)")
    exp = sub.add_parser("export", help="write every memory as markdown into a directory")
    exp.add_argument("dir")
    sub.add_parser("log", help="print recent delegations")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = os.environ.get("CAPY_BRIDGE_CONFIG", "/config/capy-bridge.yaml")
    data = os.environ.get("CAPY_BRIDGE_DATA", "/data")
    bridge = Bridge(config, data)

    if args.cmd == "export":
        n = bridge.memory.export(args.dir)
        print(f"exported {n} memories to {args.dir}")
        return
    if args.cmd == "log":
        for r in bridge.recent_delegations(50):
            print(r)
        return

    from .app import create_app

    for problem in bridge.cfg.problems():
        logging.getLogger("capy_bridge").warning("degraded: %s", problem)
    logging.getLogger("uvicorn.access").addFilter(_NoQuery())
    uvicorn.run(create_app(bridge), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main(sys.argv[1:])
