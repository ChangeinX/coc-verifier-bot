import logging

import discord
from discord.ext import tasks

from .clients import bot, table
from .config import CLAN_TAG
from .utils import get_player

log = logging.getLogger("coc-gateway")


@tasks.loop(minutes=5)
async def membership_check() -> None:
    log.info("Membership check started...")
    if table is None or not bot.guilds:
        return
    guild = bot.guilds[0]
    try:
        data = table.scan()
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to scan table: %s", exc)
        return

    for item in data.get("Items", []):
        discord_id = int(item["discord_id"])
        member = guild.get_member(discord_id)
        if member is None:
            continue
        player = await get_player(item["player_tag"])
        if player is None:
            log.warning("Skipping member %s due to failed player lookup", member)
            continue
        if not player.clan or player.clan.tag.upper() != CLAN_TAG.upper():
            try:
                await member.kick(reason="Left clan")
            except discord.Forbidden:
                log.warning("Forbidden kicking %s", member)
            except discord.HTTPException as exc:
                log.exception("Failed to kick %s: %s", member, exc)
            try:
                table.delete_item(Key={"discord_id": item["discord_id"]})
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Failed to delete record for %s: %s", member, exc)
