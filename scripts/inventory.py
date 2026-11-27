"""Generate the release inventory from the bundled SwB product."""

import argparse
import asyncio
import hashlib
import html
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from switchboard import pages
from switchboard.__main__ import build_parser as server_parser
from switchboard.agents import Agents
from switchboard.app import create_app
from switchboard.blocks import BLOCKS, load
from switchboard.blocks.github.api import INSTALLATION_ID_ENV, github_app
from switchboard.blocks.mcp.setup import CLIENTS
from switchboard.blocks.slack.credentials import slack_workspace
from switchboard.cli import build_parser as client_parser
from switchboard.core import Switchboard
from switchboard.credentials import MARKS
from switchboard.modes import ACTIONS, MODES
from switchboard.slack.avatar_files import BUNDLED_AVATARS, agent_avatar, find_avatar

from inventory_config import config_items

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/inventory.json"
EXAMPLE = ROOT / "config/switchboard.example.yaml"
WEB = ROOT / "web/src"


def asset(*paths):
    return next((path.relative_to(ROOT).as_posix() for path in paths if path is not None and path.is_file()), None)


def avatar_path(name):
    return find_avatar(agent_avatar(name) or f"{name}.png")


def item(kind, slug, label, icon=None, **fields):
    return {"id": f"{kind}:{slug}", "kind": kind, "label": label, "icon": icon, **fields}


def block_items(core):
    for use, block in core.blocks.items():
        manifest = block.manifest
        mark = use.split("-", 1)[0]
        labels = {}
        if use == "github":
            labels = {field.name: field.label for field in github_app(block.secret_env)[2]}
        elif use == "slack":
            names = {env.split("_")[-2].lower(): env for env in manifest.secrets}
            labels = {field.name: field.label for field in slack_workspace("<workspace>", names)[2]}
        for env in manifest.secrets:
            if env not in labels:
                if env == INSTALLATION_ID_ENV or env.endswith("*"):
                    labels[env] = None
                    continue
                response = pages.token_claim_page(env, "", require_https=env.endswith(".url_env>"))
                match = re.search(r'<label for="token">([^<]+)</label>', response.body.decode())
                labels[env] = html.unescape(match[1])
        yield item(
            "block", use, manifest.title,
            asset(MARKS / f"{use}.svg", WEB / "assets" / f"{use}.svg", MARKS / f"{mark}.svg", avatar_path(use)),
            use=use,
            block_kind=manifest.kind,
            connect_target=next(iter(block.connect_kinds), None) or next((client for client in CLIENTS if client.startswith(f"{use}-")), None),
            credentials=[{"env": env, "claim_label": label} for env, label in sorted(labels.items())],
        )


def product_name():
    return re.search(r'PRODUCT_NAME\s*=\s*"([^"]+)"', (WEB / "app/brand.ts").read_text(encoding="utf-8"))[1]


def provider_and_avatar_items(block_types):
    providers = Agents.build(None, (spec for block in block_types.values() for spec in block.manifest.agents)).specs
    labels = {
        path.name: block.manifest.title for use, block in block_types.items() if (path := avatar_path(use))
    }
    brand = product_name()
    labels.update({"swb.png": brand, "switchboard.png": brand})
    for provider in providers.values():
        path = avatar_path(provider.id)
        if path is not None:
            labels[path.name] = provider.label
        yield item("provider", provider.id, provider.label, asset(path))
    for path in BUNDLED_AVATARS.glob("*.png"):
        if path.is_file():
            yield item("avatar", path.name, labels.get(path.name, path.stem), asset(path),
                       sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def page_items(routes):
    inventory = {
        route.path: item("page", route.path, route.title, path=route.path, surfaces=["server"])
        for route in routes if isinstance(route, pages.PageRoute)
    }
    navigation = (WEB / "app/navigation.ts").read_text(encoding="utf-8")
    names = {path: name for name, path in re.findall(r'name:\s*"([^"]+)",\s*to:\s*"([^"]+)"', navigation)}
    platforms = dict(re.findall(r'(\w+):\s*"([^"]+)"', re.search(r"connectionNames\s*=\s*\{([^}]+)\}", navigation)[1]))
    names["/"] = product_name()

    route_source = (WEB / "app/app.tsx").read_text(encoding="utf-8")
    console_routes = re.findall(
        r'<Route\s+(?:path="([^"]+)"|index)\s+element=\{<(\w+)([^>]*?)\s*/>\s*\}', route_source,
    )
    if len(console_routes) != len(re.findall(r"<Route\s+(?:path\b|index\b)", route_source)):
        raise ValueError("could not read every console page from web/src/app/app.tsx")
    for path, component, args in console_routes:
        if path == "*":
            continue
        path = path or "/"
        section = "/" + path.split("/")[1]
        label = names.get(section, component.removesuffix("Page"))
        extra = {}
        if component == "Navigate":
            target = re.search(r'\bto="([^"]+)"', args)[1]
            label = names[target]
            extra["redirect_to"] = target
        elif ":platform" in path:
            extra["platform_titles"] = platforms
        elif component == "LoginPage":
            login_source = (WEB / "pages/login.tsx").read_text(encoding="utf-8")
            label = re.search(r"document\.title\s*=\s*`([^|]+?)\s*\|", login_source)[1]
        server = inventory.get(path)
        if server is not None and server["label"] != label:
            extra["server_title"] = server["label"]
        inventory[path] = item(
            "page", path, label, path=path, surfaces=["console", "server"] if server else ["console"], **extra,
        )
    yield from inventory.values()


def command_items():
    client, server = client_parser(), server_parser()
    flags = sorted(flag for action in client._actions for flag in action.option_strings)
    yield item("command", client.prog, client.prog, command=client.prog, flags=flags, arguments=[])
    for parser in (client, server):
        subcommands = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
        for name, command in subcommands.choices.items():
            flags = sorted(flag for action in command._actions for flag in action.option_strings)
            arguments = [
                {"name": action.dest, "choices": list(action.choices or ())}
                for action in command._actions if not action.option_strings
            ]
            yield item("command", name, command.prog, command=command.prog, flags=flags, arguments=arguments)


async def generate():
    block_types = {use: load(use) for use in BLOCKS}
    with TemporaryDirectory() as directory:
        work = Path(directory)
        config = work / "switchboard.yaml"
        config.write_text(yaml.safe_dump({"version": 2, "blocks": {use: {"use": use} for use in block_types}}), encoding="utf-8")
        core = Switchboard(config, data_dir=work, env={})
        try:
            app = create_app(core)
            mcp = core.blocks["mcp"].mcp
            tools = await mcp.list_tools()
            prompts = await mcp.list_prompts()
            rows = [
                *block_items(core),
                *provider_and_avatar_items(block_types),
                *page_items(app.starlette.routes),
                *command_items(),
                *(item("mode", mode, mode, category="channel_mode") for mode in MODES),
                *(item("mode", f"triage:{action}", action, category="triage_action") for action in ACTIONS),
                *(item("tool", tool.name, tool.title or tool.name, name=tool.name,
                       description=tool.description, input_properties=sorted(tool.input_schema.get("properties", {})))
                  for tool in tools),
                *(item("prompt", prompt.name, prompt.title or prompt.name, name=prompt.name) for prompt in prompts),
                *(item("config_key", key, key, key=key) for key in config_items(EXAMPLE, block_types)),
            ]
        finally:
            await core.aclose()
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("inventory contains duplicate IDs")
    return (json.dumps({"schema_version": 1, "items": sorted(rows, key=lambda row: row["id"])},
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed inventory")
    args = parser.parse_args()
    content = asyncio.run(generate())
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != content:
            parser.exit(1, "docs/inventory.json is stale. Run python scripts/inventory.py.\n")
    else:
        OUTPUT.write_bytes(content)


if __name__ == "__main__":
    main()
