from __future__ import annotations

import asyncio
import logging
from typing import Final

import coc

log: Final = logging.getLogger("coc-gateway")

# Track re-authentication state to prevent concurrent re-auth attempts
_reauth_lock = asyncio.Lock()
_last_reauth_attempt = 0.0


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


async def get_player_with_retry(
    client: coc.Client,
    email: str,
    password: str,
    player_tag: str,
    max_retries: int = 1,
    reauth_cooldown: int = 60,
) -> coc.Player | None:
    """Get player with automatic re-authentication on 403 errors."""
    import time

    global _last_reauth_attempt

    for attempt in range(max_retries + 1):
        try:
            player = await client.get_player(player_tag)
            return player
        except coc.NotFound:
            log.warning("Player %s not found", player_tag)
            return None
        except coc.HTTPException as exc:
            # Check if this is a 403 authentication error
            if hasattr(exc, "status") and exc.status == 403:
                if attempt < max_retries:
                    log.warning(
                        "CoC API 403 error for player %s, attempting re-authentication (attempt %d/%d)",
                        player_tag,
                        attempt + 1,
                        max_retries,
                    )

                    # Use lock to prevent concurrent re-authentication attempts
                    async with _reauth_lock:
                        current_time = time.time()
                        # Only re-authenticate if we haven't tried recently (within cooldown period)
                        if current_time - _last_reauth_attempt > reauth_cooldown:
                            try:
                                await client.login(email, password)
                                _last_reauth_attempt = current_time
                                log.info("CoC API re-authentication successful")
                            except coc.HTTPException as login_exc:
                                log.error(
                                    "CoC API re-authentication failed: %s", login_exc
                                )
                                return None
                        else:
                            log.debug("Skipping re-authentication (too recent)")

                    # Continue to next retry attempt
                    continue
                else:
                    log.error(
                        "CoC API 403 error after %d retries: %s", max_retries, exc
                    )
                    return None
            else:
                log.error("CoC API error: %s", exc)
                return None

    return None
