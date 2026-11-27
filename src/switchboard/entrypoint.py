from __future__ import annotations

import sys
from pathlib import Path


def cli_main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "hook":
        from .companion.hook import run

        run(args[1:])
        return

    from .__main__ import SERVER_COMMANDS, main

    if args and args[0] in SERVER_COMMANDS:
        main(args)
    elif not args and Path(sys.argv[0]).stem in ("switchboard", "__main__"):
        main(["serve"])
    else:
        from .cli import main as client

        client(args or ["--help"])
