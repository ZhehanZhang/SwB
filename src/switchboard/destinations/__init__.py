"""Compatibility shim: destinations are blocks now (switchboard.blocks). Removed in the next release."""

from ..blocks import Action, Block, BlockError, Call, load

Destination = Block
DestinationError = BlockError
DESTINATIONS = {"capy": "switchboard.blocks.capy:CapyBlock", "slack": "switchboard.blocks.slack:SlackBlock"}

__all__ = ["Action", "Call", "Destination", "DestinationError", "DESTINATIONS", "load"]
