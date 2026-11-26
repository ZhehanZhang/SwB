"""Compatibility shim: moved to switchboard.blocks.capy. Removed in the next release."""

from switchboard._shim import alias

alias(__name__, "switchboard.blocks.capy", ("api", "stream", "avatars"))
