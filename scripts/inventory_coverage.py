"""Check that the mapped documentation mentions every inventory ID."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE_PATH = re.compile(r"""(?:https?://[\w.:-]+|(?<![\w/%~.-]))(/(?!/)[^\s<>()\[\],;!?=#|"'`]*)""")


def mentions(row, text):
    kind, name = row["id"].split(":", 1)
    if kind == "page":
        prefix = re.split(r"[{:*]", name, maxsplit=1)[0].rstrip("/") or "/"
        for match in PAGE_PATH.finditer(text):
            path = match[1].rstrip(".").rstrip("/") or "/"
            if path in (name, prefix) or (name != prefix and path.startswith(prefix.rstrip("/") + "/")):
                return True
        return False
    if kind == "config_key":
        segments = name.split(".")
        full = r"\.".join(r"[^.\s]+" if segment == "*" else re.escape(segment) for segment in segments)
        last = re.escape(segments[-1])
        return re.search(rf"(?<![\w.-])(?:{full}|{last})(?![\w-]|\.[\w*])", text) is not None
    if kind == "command" and name != "swb":
        name = f"swb {name}"
    elif kind == "mode":
        name = name.rsplit(":", 1)[-1]
    elif kind in ("block", "provider", "avatar"):
        name = row["label"]
    elif kind not in ("command", "tool"):
        return False
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None


def main(root=ROOT):
    folder = "site" if (root / "site").is_dir() else "docs"
    map_path = root / folder / "inventory-map.json"
    hold_path = root / "site/inventory-hold.json"
    held = {}
    if hold_path.exists():
        with hold_path.open(encoding="utf-8") as file:
            held = json.load(file)
        if not isinstance(held, dict) or any(
            not key or not isinstance(reason, str) or not reason.strip() for key, reason in held.items()
        ):
            print("Inventory hold must be a JSON object of inventory IDs and reasons.", file=sys.stderr)
            return 1
    has_map = map_path.exists()
    if has_map or held:
        with (root / "docs/inventory.json").open(encoding="utf-8") as file:
            items = json.load(file)["items"]
        for key in sorted(held.keys() - {row["id"] for row in items}):
            print(f"Warning: stale inventory hold for {key}: ID is absent from the inventory", file=sys.stderr)
            del held[key]
    if not has_map:
        if held:
            print(f"Missing {folder}/inventory-map.json for held IDs: {', '.join(sorted(held))}", file=sys.stderr)
            return 1
        print(f"Inventory coverage skipped. Add {folder}/inventory-map.json to enable it.")
        return 0
    with map_path.open(encoding="utf-8") as file:
        mapping = json.load(file)
    if not isinstance(mapping, dict):
        print("Inventory map must be a JSON object of IDs and repository-relative page paths.", file=sys.stderr)
        return 1

    failed = False
    pages = {}
    resolved_root = root.resolve()
    for row in items:
        key = row["id"]
        path = mapping.get(key)
        if key not in mapping:
            print(f"Missing inventory mapping for {key}", file=sys.stderr)
        elif isinstance(path, str) and path and not Path(path).is_absolute():
            page = (root / path).resolve()
            if not page.is_relative_to(resolved_root) or not page.is_file():
                print(f"Missing documentation page for {key}: {path}", file=sys.stderr)
            elif key in held:
                print(f"Held inventory item {key}: {path} ({held[key]})")
                continue
            else:
                if page not in pages:
                    try:
                        pages[page] = page.read_text(encoding="utf-8")
                    except (OSError, UnicodeError):
                        pages[page] = None
                content = pages[page]
                if content is None:
                    print(f"Unreadable documentation page for {key}: {path}", file=sys.stderr)
                elif mentions(row, content):
                    continue
                else:
                    print(f"Documentation page does not mention {key}: {path}", file=sys.stderr)
        else:
            print(f"Invalid documentation path for {key}", file=sys.stderr)
        failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
