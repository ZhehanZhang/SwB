from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from . import client
from .caps import CAPABILITIES, registry
from .mcp_config import local_servers
from .state import CompanionError, CompanionState, base_url, device_name, state_path


def add_commands(commands: argparse._SubParsersAction) -> None:
    companion = commands.add_parser("companion", help="connect this device to SwB")
    actions = companion.add_subparsers(dest="companion_action", required=True)
    connect = actions.add_parser("connect", help="pair this device and run the companion")
    connect.add_argument("url", help="SwB server URL")
    connect.add_argument("--code", help="pairing code (entered privately by default)")
    connect.add_argument("--name", help="device name (defaults to the hostname)")
    connect.add_argument("--no-run", action="store_true", help="pair this device and exit")
    connect.add_argument("--cap", action="append", choices=CAPABILITIES, help="opt in to a capability (repeat as needed)")
    connect.add_argument("--drop-dir", type=Path, help="use this Drop folder")
    connect.add_argument("--mcp", action="append", metavar="NAME=COMMAND", help="add a local MCP server command or HTTP URL")
    connect.add_argument("--no-memory-sync", action="store_true", help="keep Claude Code auto memory on this device")
    actions.add_parser("run", help="run the paired companion")
    actions.add_parser("status", help="show the local companion status")
    actions.add_parser("disconnect", help="disconnect this device from SwB")


def run(args: argparse.Namespace, hidden_input: Callable[[str], str]) -> None:
    action = args.companion_action
    path = state_path()
    state = CompanionState.load(path)
    if state is None and action in ("status", "disconnect"):
        print("Pair this device with swb companion connect.")
        return
    if state is None and action == "run":
        raise CompanionError("Pair this device with swb companion connect.")
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    levels = [logger.level for logger in loggers]
    try:
        for logger in loggers:
            logger.setLevel(logging.CRITICAL + 1)
        if action == "connect":
            url = base_url(args.url)
            name = device_name(args.name) if args.name is not None else None
            enabled = tuple(dict.fromkeys(args.cap or ()))
            specs = tuple(args.mcp or ())
            if ("mcp" in enabled) != bool(specs):
                raise CompanionError("Use --cap mcp together with --mcp.")
            try:
                servers = local_servers(specs)
            except ValueError as error:
                raise CompanionError(str(error)) from None
            drop_dir = Path(os.path.abspath((args.drop_dir or (Path.home() / "SwB Drop")).expanduser()))
            caps, unavailable = registry(enabled, drop_dir, servers, memory_sync=not args.no_memory_sync)
            if enabled and not caps:
                raise CompanionError(next(iter(unavailable.values())))
            for reason in unavailable.values():
                print(reason, flush=True)
            if state is not None and not asyncio.run(client.clear_if_revoked(state, path)):
                raise CompanionError("Disconnect this device before pairing again.")
            code = args.code if args.code is not None else hidden_input("Pairing code: ")
            if not (code := code.strip()):
                raise CompanionError("Enter a pairing code.")
            state = asyncio.run(client.pair(url, code, caps, enabled, drop_dir, name=name))
            state = replace(state, mcp_servers=specs, memory_sync=not args.no_memory_sync)
            state.save(path)
            print("This device is paired.", flush=True)
            if args.no_run:
                return
        elif action == "disconnect":
            asyncio.run(client.disconnect(state, path))
            return
        else:
            servers = local_servers(state.mcp_servers)
            drop_dir = Path(state.drop_dir)
            caps, unavailable = registry(state.enabled_caps, drop_dir, servers, memory_sync=state.memory_sync)
            if action == "status":
                print(f"This device is paired as {state.companion_id}.")
                for cap in caps.values():
                    print(cap.title)
                for reason in unavailable.values():
                    print(reason)
                for name in servers:
                    print(name)
                return
            for reason in unavailable.values():
                print(reason, flush=True)
        asyncio.run(client.run(state, path, caps))
    except KeyboardInterrupt:
        pass
    except (OSError, UnicodeError):
        raise CompanionError("Check the companion settings and file permissions.") from None
    finally:
        for logger, level in zip(loggers, levels):
            logger.setLevel(level)
