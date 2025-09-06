from __future__ import annotations

import logging
from typing import Final

import discord

log: Final = logging.getLogger("coc-gateway")


async def resolve_log_channel(
    bot: discord.Client,
    admin_log_channel_id: int,
    guild: discord.Guild,
) -> discord.TextChannel | None:
    """Return a TextChannel object or None if unavailable.

    Looks in guild cache, then bot cache, then falls back to REST fetch.
    """
    if not admin_log_channel_id:
        return None

    channel = guild.get_channel(admin_log_channel_id) or bot.get_channel(
        admin_log_channel_id
    )
    if channel is None:
        try:
            channel = await bot.fetch_channel(admin_log_channel_id)
        except discord.HTTPException:
            log.warning(
                "Cannot fetch channel %s – invalid ID or not accessible",
                admin_log_channel_id,
            )
            return None

    if isinstance(channel, discord.TextChannel):
        return channel
    log.warning("Channel ID %s is not a text channel", admin_log_channel_id)
    return None
