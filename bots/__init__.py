"""Unified bot feature implementations.

This package mirrors the legacy entry points (`bot.py`, `giveawaybot.py`,
`tournamentbot.py`) so tests can continue to import those modules while the
unified runtime can coordinate their behavior.
"""

__all__ = ["verification", "giveaway", "tournament"]
