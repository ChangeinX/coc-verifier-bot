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

    Looks in guild cache first, then tries REST fetch as fallback.
    """
    if not admin_log_channel_id:
        return None

    # First try guild cache (most reliable for guild-specific channels)
    channel = guild.get_channel(admin_log_channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel

    # If not in guild cache, try REST fetch
    try:
        channel = await bot.fetch_channel(admin_log_channel_id)
        if isinstance(channel, discord.TextChannel):
            # Verify the channel is accessible from this guild
            if channel.guild.id == guild.id:
                return channel
            else:
                log.warning(
                    "Channel %s belongs to different guild (%s) than expected (%s)",
                    admin_log_channel_id,
                    channel.guild.id,
                    guild.id,
                )
                return None
        else:
            log.warning("Channel ID %s is not a text channel", admin_log_channel_id)
            return None
    except discord.NotFound:
        log.warning("Channel %s not found", admin_log_channel_id)
        return None
    except discord.Forbidden:
        log.warning(
            "No access to channel %s – check bot permissions",
            admin_log_channel_id,
        )
        return None
    except discord.HTTPException as exc:
        log.warning(
            "Cannot fetch channel %s – HTTP error: %s",
            admin_log_channel_id,
            exc,
        )
        return None
