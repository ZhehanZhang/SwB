"""Live setup directions for MCP clients and enabled blocks."""

import json
from importlib.resources import files
from textwrap import indent
from typing import Any
from urllib.parse import urlsplit

import yaml
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ...auth import Principal
from ...block_env import GITHUB_SECRET, configured_env
from ...config import Config
from ...connect import public_https_prerequisite
from ...doc_links import doc_link, rewrite_doc_links
from ...modes import PROVIDERS
from ...targets import slugify
from .coding import coding_steps
from .kit import Kit

CLIENTS = ("mcp-client", "chatgpt", "claude", "grok", "muse", "coding-agent")
BLOCKS = {"claude-project": "claude-routine", "capy-captain": "capy", "slack": "slack", "github": "github",
          "grok-bot": "grok-routine", "discord": "discord", "telegram": "telegram", "dots": "dots", "webhook": "webhook"}
BLOCK_EXAMPLES = {
    "discord": {"spaces": {"home": {"guild_id": "100000000000000001", "owner_ids": ["100000000000000002"], "mode": "watch"}}},
    "telegram": {"spaces": {"home": {"chats": [-1001111111111], "owner_ids": [222222], "mode": "watch"}}},
    "slack": {"agents": {"owner": {"workspaces": {"a2a": ["bot"]}}}, "workspaces": {"a2a": {
        "team_id": "T0DEMO0001", "intake": "socket", "app_token_env": "SLACK_A2A_APP_TOKEN",
        "identities": {"bot": {"token_env": "SLACK_A2A_BOT_TOKEN", "write": "free"}},
    }}},
    "github": {"secret_env": GITHUB_SECRET, "spaces": {"example-org/app": {"mode": {"mode": "watch", "target": "agent:beta"}}}},
    "dots": {"delivery": "mailbox", "dots": {"example-dot": {"name": "Example Dot", "mailbox": "chatgpt"}}},
}
ROUTINE_ENV_ONLY = {
    "claude-routine": ("CLAUDE_ROUTINE_ID", "CLAUDE_ROUTINE_TOKEN"),
    "grok-routine": ("GROK_ROUTINE_URL", "GROK_ROUTINE_SECRET"),
}
ALIASES = {"grok-routine": "grok-bot"}
CHATGPT_PLUGINS_URL = "https://chatgpt.com/plugins"
HOSTED_CLIENT_INSTRUCTIONS = {
    "chatgpt": f"Open {CHATGPT_PLUGINS_URL} in a web browser. Select + > Add custom MCP server. "
               "Add {url} as a custom connector. Sign in with OAuth.",
    "claude": "Open Customize > Connectors > Add > Custom > Web. Add {url} as a custom connector. Sign in with OAuth.",
    "grok": "Open grok.com > Connectors > New Connector > Custom. Add {url} as a custom connector. Sign in with OAuth.",
    "muse": "In a Muse chat, ask Muse to add {url} as a custom connector with OAuth. Open the sign-in link Muse sends.",
}
GUIDE_TITLE_WORDS = {"chatgpt": "ChatGPT", "github": "GitHub", "mcp": "MCP"}


def enable_steps(cfg: Config, name: str, choose: dict[str, Any]) -> list[dict[str, str]]:
    restart = {"by": "you", "do": "Restart SwB."}
    routine_env = ROUTINE_ENV_ONLY.get(name)
    if routine_env and not cfg.blocks_from_file:
        return [{"by": "person", "do": f"Set {' and '.join(routine_env)} in .env."}, restart]

    config = {"use": name, "enabled": True, **BLOCK_EXAMPLES.get(name, {})}
    if name == "dots" and (dot := choose.get("dot")) is not None:
        if not isinstance(dot, str) or not (slug := slugify(dot)):
            raise ToolError("Choose a dot name with letters or digits.")
        config["dots"] = {slug: {"name": dot.strip(), "mailbox": "chatgpt"}}
    if cfg.blocks_from_file:
        instruction = "Merge this YAML under blocks in config/switchboard.yaml."
        snippet = indent(yaml.safe_dump({name: config}, sort_keys=False).rstrip(), "  ")
    else:
        instruction = "Add this blocks section to config/switchboard.yaml."
        snippet = yaml.safe_dump({"blocks": {**cfg.blocks, name: config}}, sort_keys=False).rstrip()
    if name == "slack":
        instruction += " Replace T0DEMO0001 with the Slack workspace ID."
    elif name == "github":
        instruction += " Replace example-org/app with the GitHub repository. Replace agent:beta with an address from targets."
    elif name == "discord":
        instruction += " Replace guild_id and owner_ids with your Discord server and user IDs."
    elif name == "telegram":
        instruction += " Replace chats and owner_ids with your Telegram group and user IDs."
    names = configured_env(name, config)
    if names:
        instruction += f" Use {', '.join(names)} in .env or get private claim links after restart."
    elif routine_env:
        instruction += (
            " Get private claim links after restart."
            f" Environment-only installs use {' and '.join(routine_env)} in .env."
        )
    return [{"by": "you", "do": instruction, "snippet": snippet}, restart]


def register_guides(mcp: MCPServer, cfg: Config) -> dict[str, dict[str, str]]:
    def register_guide(name: str, uri: str, title: str, text: str) -> None:
        @mcp.prompt(name=name, title=title)
        @mcp.resource(uri, name=name, title=title, mime_type="text/markdown")
        def read_guide() -> str:
            return rewrite_doc_links(text, cfg.public_url)

    guides = {}
    for path in sorted(files("switchboard.prompts.setup").iterdir(), key=lambda path: path.name):
        if not path.is_file() or not path.name.endswith(".md"):
            continue
        kind = path.name.removesuffix(".md")
        name, uri = f"setup-{kind}", f"swb://setup/{kind}"
        label = " ".join(GUIDE_TITLE_WORDS.get(word, word.capitalize()) for word in kind.split("-"))
        register_guide(name, uri, f"Set up {label}", path.read_bytes().decode("utf-8"))
        guides[kind] = {"prompt": name, "resource": uri}
    return guides


def hosted_client_instruction(kind: str, connector: str | None) -> str:
    return HOSTED_CLIENT_INSTRUCTIONS[kind].format(url=connector or "<public_url>/mcp")


def client_steps(kit: Kit, kind: str, principal: Principal, project: str | None, choose: dict[str, Any]) -> dict[str, Any]:
    core = kit.core
    base = core.base_url
    resource = core.cfg.resource
    oauth = bool(resource and core.cfg.owner_sign_in)
    hosted = kind in HOSTED_CLIENT_INSTRUCTIONS
    if hosted and (not oauth or not resource or urlsplit(resource).scheme != "https"):
        return {"done": [], "next": [{"by": "person", "do": public_https_prerequisite(core.cfg.owner_sign_in)}]}
    url = resource if hosted else f"{base}/mcp"
    done = [f"MCP URL: {url}.", f"OAuth sign-in: {'available' if oauth else 'unavailable'}."]
    if hosted:
        signed_in = principal.kind == "oauth" and principal.source == kind
        if not signed_in and principal.is_unrestricted_owner:
            signed_in = core.store.one(
                "SELECT 1 FROM oauth_tokens WHERE source = ? AND resource IN (SELECT value FROM json_each(?)) AND expires_at > ? AND revoked_at IS NULL LIMIT 1",
                (kind, json.dumps(core.cfg.resources), core.clock()),
            ) is not None
        if signed_in:
            hook = [{"by": "you", "do": "Set up the Muse hook to wake Muse when SwB messages arrive.",
                     "call": {"tool": "muse_hook_setup", "arguments": {}}, "optional": True}] if kind == "muse" else []
            return {"done": [*done, "The client is signed in."], "next": hook}
        return {"done": done, "next": [{"by": "person", "do": hosted_client_instruction(kind, resource)}]}
    if not principal.is_unrestricted_owner:
        return {"done": done, "next": [{"by": "person", "do": "Ask the owner to mint a pairing code from their own sign-in."}]}
    picked = choose.get("project")
    if picked is not None and not isinstance(picked, str):
        raise ToolError("choose.project must be a project slug")
    if project and picked and core.project(project).slug != core.project(picked).slug:
        raise ToolError("project and choose.project must name the same Switchboard project")
    project = project or picked
    agent = choose.get("agent")
    if agent is None:
        args = {"what": kind, "choose": {"agent": "<agent name>"}}
        if project:
            args["project"] = project
        return {"done": done, "next": [{"by": "you", "do": "Choose the other client's agent name and call connect again.",
                                        "call": {"tool": "connect", "arguments": args}}]}
    if not isinstance(agent, str):
        raise ToolError("choose.agent must be an agent name")
    if project:
        project = core.project(project).slug
    if any(key["agent"] == agent.strip().lower() and key["project"] == project for key in core.connections.list()):
        return {"done": [*done, "The agent already has a pairing key."], "next": []}
    pairing = kit.mint_pairing(principal, agent, project, None)
    return {"done": done, "next": [{"by": "person",
            "do": f'POST {base}/connect with {{"code":"{pairing["code"]}"}} to get your bearer key, then send Authorization: Bearer <key> to {url}.'}]}


def register(mcp: MCPServer, kit: Kit) -> None:
    core = kit.core
    owners = {kind: block for block in core.blocks.values() for kind in block.connect_kinds}
    kinds = sorted(set(CLIENTS) | BLOCKS.keys() | owners.keys() | {"cloudflare"})
    guides = register_guides(mcp, core.cfg)
    guide_names = sorted(guide["prompt"] for guide in guides.values())

    @mcp.tool(
        title="Connect to SwB",
        description=('When someone asks how to connect, set up or link something to SwB, call connect first. '
                     f'Supported kinds include {", ".join(kinds)}. Do steps marked "you" in order. '
                     'Share "person" steps and links with the owner. Follow required steps in next until complete is true. '
                     'The done field lists progress. '
                     'Use choose for details such as a Captain, project, agent name or dot name. '
                     'For Muse device pairing pass choose.gadget=true. Use private links for credentials. '
                     'Read the prompt or resource in guide for detailed setup instructions.'),
        annotations=kit.SEND,
    )
    async def connect(ctx: Context, what: str, project: str | None = None, choose: dict[str, Any] | None = None) -> dict[str, Any]:
        p = kit.who(ctx)
        what = ALIASES.get(what := what.strip().lower(), what)
        result: dict[str, Any] = {"what": what, "complete": False}
        if what in guides:
            result["guide"] = guides[what]
        if what == "cloudflare":
            return {**result, "done": [], "next": [
                {"by": "person", "do": "Follow the Cloudflare setup guide.",
                 "link": doc_link("setup/cloudflare", core.cfg.public_url)}]}
        if what not in kinds:
            if what in guides:
                return {**result, "done": [], "next": []}
            return {**result, "what": "unknown", "done": [], "next": [], "kinds": kinds, "guides": guide_names}
        block = owners.get(what)
        if what not in CLIENTS and not p.is_unrestricted_owner:
            modes = ({"modes": core.modes.summary(block.name, p, slugs=await core.modes.status_slugs(p))}
                     if block is not None and block.manifest.id in PROVIDERS else {})
            return {**result, "done": [], "next": [
                {"by": "person", "do": f"Run connect for {what} from the owner's sign-in."}], **modes}
        choose = (choose or {}) if p.is_unrestricted_owner or what == "coding-agent" else {}
        muse_gadget = what == "muse" and p.is_unrestricted_owner and choose.get("connector") is not True and (
            choose.get("gadget") is True or "companion" in choose or (block is not None and (p.source, p.kind) != ("muse", "oauth")))
        if muse_gadget:
            result.pop("guide", None)
        if muse_gadget and block is None:
            return {**result, "done": [], "next": enable_steps(core.cfg, "muse", choose)}
        if what in BLOCKS and block is None:
            return {**result, "done": [], "next": enable_steps(core.cfg, BLOCKS[what], choose)}
        outcome = "ok"
        try:
            if what == "coding-agent":
                out = coding_steps(kit, p, project, choose)
            elif what in CLIENTS and not muse_gadget:
                out = client_steps(kit, what, p, project, choose)
            else:
                out = await kit.run(block.connect_steps(what, p, project, choose))
                if out is None:
                    step = block.next_step()
                    out = {"done": [], "next": [{"by": "person", "do": step}] if step else []}
        except Exception:
            outcome = "failed"
            raise
        finally:
            if p.is_unrestricted_owner:
                core.audit.record(origin=p.origin, source=p.source, client=p.client_id, destination=block.name if block else "core",
                                  action=f"connect_{what}", outcome=outcome)
        if block is not None and block.manifest.id in PROVIDERS:
            out.setdefault("modes", core.modes.summary(block.name, p))
        complete = all(step.get("optional") for step in out["next"])
        return {**result, **out, "complete": complete, **({"state": "connected"} if complete else {})}
