"""Check the documentation map covers every inventory ID."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(root=ROOT):
    folder = "site" if (root / "site").is_dir() else "docs"
    map_path = root / folder / "inventory-map.json"
    if not map_path.exists():
        print(f"Inventory coverage skipped. Add {folder}/inventory-map.json to enable it.")
        return 0
    with map_path.open(encoding="utf-8") as file:
        mapping = json.load(file)
    with (root / "docs/inventory.json").open(encoding="utf-8") as file:
        items = json.load(file)["items"]
    if not isinstance(mapping, dict):
        print("Inventory map must be a JSON object of IDs and repository-relative page paths.", file=sys.stderr)
        return 1

    failed = False
    for row in items:
        key = row["id"]
        path = mapping.get(key)
        if key not in mapping:
            print(f"Missing inventory mapping for {key}", file=sys.stderr)
        elif isinstance(path, str) and path and not Path(path).is_absolute():
            page = (root / path).resolve()
            if page.is_relative_to(root.resolve()) and page.is_file():
                continue
            print(f"Missing documentation page for {key}: {path}", file=sys.stderr)
        else:
            print(f"Invalid documentation path for {key}", file=sys.stderr)
        failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
