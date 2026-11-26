"""The agents registry: who calls in, how an OAuth client is recognised as them, and their labels.

Agents come from three places, in this order: presets (data, below), the agents a block provides in its manifest
(Capy's sign-in comes from the capy block), and the config's agents: section, which can pick presets, relabel them or
add new ones. Agents that connect with a pairing code register themselves (switchboard.connect). Nothing else in the
code names a vendor.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .blocks import AgentSpec

LOOPBACK_REDIRECTS = ("http://localhost/*", "http://127.0.0.1/*")

PRESETS: dict[str, AgentSpec] = {
    s.id: s
    for s in (
        AgentSpec("claude", "Claude", redirect_uris=("https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback"),
                  redirect_hosts=("claude.ai", "claude.com"), cimd_hosts=("claude.ai", "claude.com", "anthropic.com"), link_key="claude_project"),
        AgentSpec("chatgpt", "ChatGPT", redirect_uris=("https://chatgpt.com/connector_platform_oauth_redirect", "https://chatgpt.com/connector/oauth/*"),
                  redirect_hosts=("chatgpt.com", "openai.com"), cimd_hosts=("chatgpt.com", "openai.com"), link_key="chatgpt_project"),
        AgentSpec("grok", "Grok", redirect_uris=("https://grok.com/*", "https://x.ai/*"),
                  redirect_hosts=("grok.com", "x.ai"), cimd_hosts=("grok.com", "x.ai")),
        AgentSpec("gemini", "Gemini", redirect_uris=("https://oauth-redirect.googleusercontent.com/r/*", "https://oauth-redirect.googleusercontent.com/a/*"),
                  redirect_hosts=("oauth-redirect.googleusercontent.com",)),
        AgentSpec("claude-code", "Claude Code"),
    )
}


def host_allowed(host: str | None, hosts: Iterable[str]) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def _tuple(v: Any) -> tuple[str, ...]:
    if v is None:
        return ()
    return (str(v),) if isinstance(v, str) else tuple(str(x) for x in v)


class Agents:
    def __init__(self, specs: Iterable[AgentSpec]):
        self.specs: dict[str, AgentSpec] = {}
        for s in specs:
            self.specs[s.id] = s

    @classmethod
    def build(cls, config: Mapping[str, Any] | None, provided: Iterable[AgentSpec]) -> "Agents":
        """Presets (all of them without an agents: section, else the ones it names), block agents, then config agents."""
        specs: list[AgentSpec] = []
        provided = list(provided)
        if not config:
            specs += PRESETS.values()
            specs += provided
            return cls(specs)
        by_block = {s.id: s for s in provided}
        for aid, raw in config.items():
            aid, raw = str(aid).lower(), dict(raw or {})
            base = PRESETS.get(str(raw.get("preset") or aid)) or by_block.get(aid)
            if raw.get("from_block"):
                base = by_block.get(aid) or base
            match = dict(raw.get("match") or {})
            specs.append(AgentSpec(
                id=aid,
                label=str(raw.get("label") or (base.label if base else aid)),
                redirect_uris=_tuple(match.get("redirect_uris")) or (base.redirect_uris if base else ()),
                redirect_hosts=_tuple(match.get("redirect_hosts")) or (base.redirect_hosts if base else ()),
                cimd_hosts=_tuple(match.get("cimd_hosts")) or (base.cimd_hosts if base else ()),
                link_key=raw.get("link_key") or (base.link_key if base else None),
            ))
        specs += [s for s in provided if s.id not in {x.id for x in specs}]
        return cls(specs)

    def __contains__(self, agent: str) -> bool:
        return agent in self.specs

    def ids(self) -> list[str]:
        return list(self.specs)

    def label(self, agent: str, default: str | None = None) -> str:
        s = self.specs.get(agent)
        return s.label if s else (default if default is not None else agent)

    def match(self, redirect_host: str, cimd_host: str) -> str | None:
        for s in self.specs.values():
            if host_allowed(redirect_host, s.redirect_hosts) or host_allowed(cimd_host, s.cimd_hosts):
                return s.id
        return None

    def redirect_uris(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([u for s in self.specs.values() for u in s.redirect_uris] + list(LOOPBACK_REDIRECTS)))

    def cimd_hosts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(h for s in self.specs.values() for h in s.cimd_hosts))

    def link_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s.link_key for s in self.specs.values() if s.link_key))

    def describe(self) -> list[dict[str, Any]]:
        return [s.as_dict() for s in self.specs.values()]
