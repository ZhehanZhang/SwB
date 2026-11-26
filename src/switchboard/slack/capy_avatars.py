"""Compatibility shim: moved to switchboard.blocks.capy.avatars. Removed in the next release."""

from switchboard._shim import alias

alias(__name__, "switchboard.blocks.capy.avatars", ())
