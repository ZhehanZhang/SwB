"""Live setup directions for MCP clients and enabled blocks."""

from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ...auth import Principal
from .coding import coding_steps
from .kit import Kit

CLIENTS = ("mcp-client", "chatgpt", "claude", "coding-agent")
BLOCKS = {"claude-project": "claude-routine", "capy-captain": "capy", "slack": "slack", "github": "github", "grok": "grok-routine", "discord": "discord", "telegram": "telegram"}
CHATGPT_PLUGINS_URL = "https://chatgpt.com/plugins"
MENUS = {"chatgpt": f"{CHATGPT_PLUGINS_URL} in a web browser (the desktop apps and the plugin editor can't add one), then + > Add custom MCP server,", "claude": "Customize > Connectors > Add > Custom > Web"}


def hosted_client_instruction(kind: str, connector: str | None) -> str:
    return f"Open {MENUS[kind]} to add {connector or '<public_url>/mcp'} as a custom connector and sign in with OAuth."


def client_steps(kit: Kit, kind: str, principal: Principal, project: str | None, choose: dict[str, Any]) -> dict[str, Any]:
    core = kit.core
    base = core.base_url
    url = f"{base}/mcp"
    oauth = bool(core.cfg.issuer and core.cfg.owner_sign_in)
    done = [f"MCP URL: {url}.", f"OAuth sign-in: {'available' if oauth else 'unavailable'}."]
    if kind in MENUS:
        signed_in = principal.kind == "oauth" and principal.source == kind
        if not signed_in and principal.is_unrestricted_owner and oauth:
            signed_in = core.store.one(
                "SELECT 1 FROM oauth_tokens WHERE source = ? AND resource = ? AND expires_at > ? AND revoked_at IS NULL LIMIT 1",
                (kind, core.cfg.resource, core.clock()),
            ) is not None
        if signed_in:
            return {"done": [*done, "The client is signed in."], "next": []}
        next_steps = [] if oauth else [{"by": "person", "do": "Configure a public URL and owner sign-in before connecting a hosted client."}]
        next_steps.append({"by": "person", "do": hosted_client_instruction(kind, core.cfg.resource)})
        return {"done": done, "next": next_steps}
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
    kinds = sorted(set(CLIENTS) | BLOCKS.keys() | owners.keys())

    @mcp.tool(
        title="Connect to Switchboard",
        description=('When someone asks "how do I connect/set up/link X to switchboard?", call connect first. '
                     f'Kinds: {", ".join(kinds)}. Follow each next step in order: run exact calls marked "you", '
                     'give "person" steps and links to the owner, and call connect again to check progress. '
                     'Pass choices such as a Captain, project or agent name in choose, never credentials; use the link for token entry.'),
        annotations=kit.SEND,
    )
    async def connect(ctx: Context, what: str, project: str | None = None, choose: dict[str, Any] | None = None) -> dict[str, Any]:
        p = kit.who(ctx)
        what = what.strip().lower()
        if what not in kinds:
            return {"what": "unknown", "done": [], "next": [], "kinds": kinds}
        block = owners.get(what)
        if what in BLOCKS and block is None:
            return {"what": what, "done": [], "next": [
                {"by": "person", "do": f"Enable the {BLOCKS[what]} block in config/switchboard.yaml and restart Switchboard."}]}
        if what not in CLIENTS and not p.is_unrestricted_owner:
            return {"what": what, "done": [], "next": [
                {"by": "person", "do": f"Run connect for {what} from the owner's sign-in."}]}
        choose = (choose or {}) if p.is_unrestricted_owner or what == "coding-agent" else {}
        outcome = "ok"
        try:
            if what == "coding-agent":
                out = coding_steps(kit, p, project, choose)
            elif what in CLIENTS:
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
        return {"what": what, **out, **({"state": "connected"} if not out["next"] else {})}
