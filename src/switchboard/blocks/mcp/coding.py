"""Remote MCP setup snippets for coding agents."""

import json
import shlex
from typing import Any, NamedTuple

from mcp.server.mcpserver.exceptions import ToolError

from ...auth import Principal
from .kit import Kit


class Guide(NamedTuple):
    where: str
    snippet: str
    oauth: str | None
    bearer: str
    command: str = ""


JSON_BEARER = ', "headers": {"Authorization": "Bearer <key>"}'
MCP_JSON = '{"mcpServers": {"switchboard": {%s@auth}}}'
GUIDES = {
    "claude-code": Guide("~/.claude.json", MCP_JSON % '"type": "http", "url": @url',
                         "Sign in to SwB from /mcp in Claude Code.", JSON_BEARER,
                         "claude mcp add --transport http --scope user switchboard @url"),
    "codex": Guide("~/.codex/config.toml", "[mcp_servers.switchboard]\nurl = @url@auth",
                   "Run codex mcp login switchboard to sign in.", '\nhttp_headers = { Authorization = "Bearer <key>" }'),
    "gemini-cli": Guide("~/.gemini/settings.json", MCP_JSON % '"httpUrl": @url',
                        "Sign in when Gemini CLI prompts, or run /mcp auth switchboard.", JSON_BEARER),
    "goose": Guide("~/.config/goose/config.yaml", 'extensions:\n  switchboard:\n    type: streamable_http\n'
                   '    name: switchboard\n    enabled: true\n    uri: @url\n    headers: {@auth}\n'
                   '    env_keys: []\n    envs: {}\n    timeout: 300',
                   "Sign in when Goose opens the browser on first connect.", '"Authorization": "Bearer <key>"'),
    "opencode": Guide("opencode.json (OpenCode V2)", '{"mcp": {"servers": {"switchboard": {"type": "remote", "url": @url, "enabled": true@auth}}}}',
                      "Run opencode mcp auth switchboard to sign in.", ', "oauth": false' + JSON_BEARER),
    "cline": Guide("~/.cline/data/settings/cline_mcp_settings.json", MCP_JSON % '"type": "streamableHttp", "url": @url',
                   "Open cline mcp and select Authorize OAuth for the switchboard entry.", JSON_BEARER),
    "kiro": Guide("~/.kiro/settings/mcp.json", MCP_JSON % '"url": @url',
                  "Sign in when Kiro opens the browser on first connect.", JSON_BEARER),
    "qwen-code": Guide("~/.qwen/settings.json", MCP_JSON % '"httpUrl": @url',
                       "Sign in when Qwen Code prompts, or use /mcp to authenticate.", JSON_BEARER),
    "other": Guide("", "Transport: Streamable HTTP\nURL: @url@auth", None, "\nAuthorization: Bearer <key>"),
}


def coding_snippet(agent: str, url: str, oauth: bool) -> dict[str, str]:
    guide = GUIDES[agent]
    use_oauth = oauth and guide.oauth is not None
    if use_oauth and guide.command:
        instruction = "Add SwB with this command."
        snippet = guide.command.replace("@url", shlex.quote(url))
    else:
        instruction = f"Merge this entry into {guide.where}." if guide.where else "Enter these settings in your remote MCP client (client-specific syntax unverified)."
        if not use_oauth:
            instruction = instruction.rstrip(".") + ", replacing <key> locally with the pairing key and keeping it out of shared files."
        auth = "" if use_oauth else guide.bearer
        snippet_url = json.dumps(url) if guide.where else url
        snippet = guide.snippet.replace("@auth", auth).replace("@url", snippet_url)
        if snippet.startswith("{"):
            snippet = json.dumps(json.loads(snippet), indent=2)
    return {"id": agent, "where": guide.where, "snippet": snippet, "instruction": instruction, "auth": "oauth" if use_oauth else "bearer"}


def coding_steps(kit: Kit, principal: Principal, project: str | None, choose: dict[str, Any]) -> dict[str, Any]:
    core = kit.core
    url = f"{core.base_url}/mcp"
    oauth = bool(core.cfg.issuer and core.cfg.owner_sign_in)
    done = [f"MCP URL: {url}.", f"OAuth sign-in: {'available' if oauth else 'unavailable'}."]
    names = ", ".join(GUIDES)
    agent = choose.get("agent")
    if agent is None:
        step: dict[str, Any] = {"by": "person", "do": f"Choose a coding agent: {names}."}
        if principal.is_unrestricted_owner:
            args = {"what": "coding-agent", "choose": {"agent": "<coding agent>"}}
            if project:
                args["project"] = project
            step.update(by="you", call={"tool": "connect", "arguments": args})
        return {"done": done, "next": [step]}
    agent = agent.strip().lower() if isinstance(agent, str) else None
    if agent not in GUIDES:
        raise ToolError(f"Choose one of these coding agents: {names}.")
    guide = GUIDES[agent]
    config = coding_snippet(agent, url, oauth)
    use_oauth = config["auth"] == "oauth"
    steps: list[dict[str, str]] = []
    if not use_oauth:
        if not principal.is_unrestricted_owner:
            steps.append({"by": "person", "do": "Ask the owner to run connect from their own sign-in to mint a pairing code."})
        else:
            if project:
                project = core.project(project).slug
            if key := next((k for k in core.connections.list() if k["agent"] == agent and k["project"] == project), None):
                return {"done": [*done, "This agent already has a pairing key"], "next": [{"by": "person",
                        "do": f'To set up another machine or replace it, revoke it with connect_revoke(key_id="{key["id"]}"), then call connect again.'}]}
            code = kit.mint_pairing(principal, agent, project, None)["code"]
            steps.append({"by": "person", "do": f'POST {core.base_url}/connect with {{"code":"{code}"}} from the client to obtain its bearer key.'})
    steps.append({"by": "person", "do": config["instruction"], "snippet": config["snippet"]})
    if use_oauth:
        steps.append({"by": "person", "do": guide.oauth})
    wake = "Use the SwB wait or inbox tool to pick up messages."
    if agent == "claude-code":
        wake += " The Claude Code channel can also deliver messages to an open session."
    steps.append({"by": "person", "do": wake})
    return {"done": done, "next": steps}
