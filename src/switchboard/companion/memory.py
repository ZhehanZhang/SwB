from __future__ import annotations

import re
from typing import Any

MEMORY_SYNC_EVENT = "memory.sync"
MEMORY_SYNC_SOURCE = "claude-code-auto-memory"
MEMORY_CONTENT_LIMIT = 20_000
MEMORY_FRAME_LIMIT = 128 * 1024
_SKIPPED_FILES = {"memory.md", "claude.md", "claude.local.md", "agents.md"}


def memory_filename(name: str) -> bool:
    return (name.isprintable() and not name.startswith(".") and len(name) > 3
            and name.endswith(".md") and name.lower() not in _SKIPPED_FILES)


def memory_source_key(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 512 or "\\" in value or not value.isprintable():
        return False
    project, sep, name = value.partition("/")
    return bool(sep and project and project not in (".", "..") and "/" not in name and memory_filename(name))


def sync_item(path: str, content: str) -> dict[str, Any]:
    body = content
    fields: dict[str, str] = {}
    lines = content.splitlines(keepends=True)
    if lines and lines[0].rstrip("\r\n") == "---":
        for end, line in enumerate(lines[1:], 1):
            if line.rstrip("\r\n") == "---":
                for field in lines[1:end]:
                    key, _, value = field.partition(":")
                    if key in {"name", "description", "type"}:
                        value = value.strip()
                        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                            value = value[1:-1].strip()
                        if value and value.isprintable():
                            fields[key] = value
                body = "".join(lines[end + 1:])
                break
    heading = re.search(r"^#{1,6}[ \t]+([^\r\n]+)", body, re.MULTILINE)
    title = fields.get("name") or fields.get("description") or (heading[1].strip() if heading else "")
    return {"source_key": path, "title": (title or path.rsplit("/", 1)[-1][:-3])[:200],
            "body": body, "tags": [fields["type"]] if "type" in fields else []}
