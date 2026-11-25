"""Compatibility shim: origins are blocks now (switchboard.blocks). Removed in the next release."""

from ..blocks import Block, load

Origin = Block
ORIGINS = {"mcp": "switchboard.blocks.mcp:MCPBlock", "slack": "switchboard.blocks.slack:SlackBlock"}

__all__ = ["Origin", "ORIGINS", "load"]
