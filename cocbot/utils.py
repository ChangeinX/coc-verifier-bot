import logging
import re
from typing import Optional

import coc
import discord

from .clients import bot, coc_client
from .config import ADMIN_LOG_CHANNEL_ID, CLAN_TAG

log = logging.getLogger("coc-gateway")


def normalize_town_hall(value: str) -> str | None:
    """Return a normalized 'Town Hall <level>' string or None."""
    match = re.search(r"(\d+)", value)
    if not match:
        return None
    level = int(match.group(1))
    return f"Town Hall {level}"


async def resolve_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    if not ADMIN_LOG_CHANNEL_ID:
        return None
    channel = guild.get_channel(ADMIN_LOG_CHANNEL_ID) or bot.get_channel(ADMIN_LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(ADMIN_LOG_CHANNEL_ID)
        except discord.HTTPException:
            log.warning("Cannot fetch channel %s – invalid ID or not accessible", ADMIN_LOG_CHANNEL_ID)
            return None
    if isinstance(channel, discord.TextChannel):
        return channel
    log.warning("Channel ID %s is not a text channel", ADMIN_LOG_CHANNEL_ID)
    return None


async def get_player(player_tag: str) -> coc.Player | None:
    try:
        player = await coc_client.get_player(player_tag)
    except coc.NotFound:
        log.warning("Player %s not found", player_tag)
        return None
    except coc.HTTPException as exc:
        log.error("CoC API error: %s", exc)
        return None
    return player


async def is_member_of_clan(player_tag: str) -> bool:
    player = await get_player(player_tag)
    if not player or not player.clan:
        return False
    return player.clan.tag.upper() == CLAN_TAG.upper()
