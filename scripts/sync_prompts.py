"""Sync the bundled setup prompts from docs/prompts."""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed prompt copies")
    args = parser.parse_args()

    source = ROOT / "docs/prompts"
    target = ROOT / "src/switchboard/prompts/setup"
    prompts = {path.name: path.read_bytes() for path in source.glob("*.md")}
    copies = {path.name: path.read_bytes() for path in target.glob("*.md")}
    if args.check:
        if prompts != copies:
            parser.exit(1, "Bundled setup prompts are stale. Run python scripts/sync_prompts.py.\n")
        return

    target.mkdir(parents=True, exist_ok=True)
    for name in copies.keys() - prompts.keys():
        (target / name).unlink()
    for name, content in prompts.items():
        if copies.get(name) != content:
            (target / name).write_bytes(content)


if __name__ == "__main__":
    main()
