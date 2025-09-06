from __future__ import annotations

import logging
from typing import Final

import coc

log: Final = logging.getLogger("coc-gateway")


async def get_player(client: coc.Client, player_tag: str) -> coc.Player | None:
    """Return player object or None on API error."""
    try:
        player = await client.get_player(player_tag)
    except coc.NotFound:
        log.warning("Player %s not found", player_tag)
        return None
    except coc.HTTPException as exc:
        log.error("CoC API error: %s", exc)
        return None
    return player


async def is_member_of_clan(
    client: coc.Client,
    main_clan_tag: str | None,
    feeder_clan_tag: str | None,
    player_tag: str,
) -> bool:
    player = await get_player(client, player_tag)
    if not player or not player.clan:
        return False
    player_clan_tag = player.clan.tag.upper()
    if main_clan_tag and player_clan_tag == main_clan_tag.upper():
        return True
    if feeder_clan_tag and player_clan_tag == feeder_clan_tag.upper():
        return True
    return False


async def get_player_clan_tag(
    client: coc.Client,
    main_clan_tag: str | None,
    feeder_clan_tag: str | None,
    player_tag: str,
) -> str | None:
    """Return the clan tag the player belongs to (main or feeder), or None if not a member."""
    player = await get_player(client, player_tag)
    if not player or not player.clan:
        return None
    player_clan_tag = player.clan.tag.upper()
    if main_clan_tag and player_clan_tag == main_clan_tag.upper():
        return main_clan_tag.upper()
    if feeder_clan_tag and player_clan_tag == feeder_clan_tag.upper():
        return feeder_clan_tag.upper()
    return None
