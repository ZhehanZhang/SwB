"""Compatibility shim: moved to switchboard.blocks.mcp. Removed in the next release."""

from switchboard._shim import alias

alias(__name__, "switchboard.blocks.mcp", ("tasks",))
