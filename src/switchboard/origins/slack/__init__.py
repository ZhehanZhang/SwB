"""Compatibility shim: moved to switchboard.blocks.slack. Removed in the next release."""

from switchboard._shim import alias

alias(__name__, "switchboard.blocks.slack", ("intake",))
