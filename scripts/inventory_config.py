"""Read documented YAML settings and block schemas without retaining example values."""

import re
from dataclasses import fields

import yaml

from switchboard.config import Branding

SELECTOR_MAPS = {
    "agents", "aliases", "channel_projects", "channels", "dots", "inbox_keys", "key_scopes", "known",
    "personas", "projects", "resources", "routes", "routines", "sinks", "source_names", "spaces", "target_aliases",
    "workspace_projects", "workspaces",
}


def documented_example(path):
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        example = re.fullmatch(r"(\s*)# (.*)", line)
        if example:
            indent, value = example.groups()
            if not re.match(r""" *(?:[a-z0-9_-][\w./:#*-]*|".+?"|'.+?'):(?:\s|$)| *-\s""", value):
                if not (lines and lines[-1].rstrip().endswith(",") and value.startswith(" ")):
                    continue
            line = indent + value
        elif not line.strip() or line.lstrip().startswith("#"):
            continue
        if lines and re.fullmatch(r" *\w+: \{\}", lines[-1]) and line.startswith(" "):
            lines[-1] = lines[-1].removesuffix(" {}")
        lines.append(line)
    return yaml.safe_load("\n".join(lines))


def config_items(path, block_types):
    def walk(value, prefix=()):
        if isinstance(value, dict):
            block_section = len(prefix) == 2 and prefix[0] == "blocks"
            selectors = prefix and not block_section and prefix[-1] in SELECTOR_MAPS
            for name, child in value.items():
                name = "*" if selectors else str(name)
                key = (*prefix, name)
                if name != "*":
                    yield ".".join(key)
                yield from walk(child, key)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child, (*prefix, "*"))

    def schema_properties(schema, prefix):
        for name, child in schema.get("properties", {}).items():
            key = (*prefix, name)
            yield ".".join(key)
            yield from schema_properties(child, key)
        for name in ("items", "additionalProperties"):
            if isinstance(child := schema.get(name), dict):
                yield from schema_properties(child, (*prefix, "*"))

    documented = set(walk(documented_example(path)))
    documented.update(f"branding.{field.name}" for field in fields(Branding))
    for use, block in block_types.items():
        documented.update(schema_properties(block.manifest.config_schema, ("blocks", use)))
    return sorted(documented)
