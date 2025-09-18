from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Final, Literal

import coc

log: Final = logging.getLogger("coc-gateway")

# Track re-authentication state to prevent concurrent re-auth attempts
_reauth_lock = asyncio.Lock()
_last_reauth_attempt = 0.0


FetchStatus = Literal["ok", "not_found", "access_denied", "error"]


@dataclass(slots=True)
class PlayerFetchResult:
    """Return object describing the result of a player fetch."""

    status: FetchStatus
    player: coc.Player | None = None
    exception: Exception | None = None


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
    result = await fetch_player_with_status(
        client,
        email,
        password,
        player_tag,
        max_retries=max_retries,
        reauth_cooldown=reauth_cooldown,
    )
    return result.player


async def fetch_player_with_status(
    client: coc.Client,
    email: str,
    password: str,
    player_tag: str,
    max_retries: int = 1,
    reauth_cooldown: int = 60,
) -> PlayerFetchResult:
    """Get player data and return structured status information."""
    import time

    global _last_reauth_attempt

    for attempt in range(max_retries + 1):
        try:
            player = await client.get_player(player_tag)
            return PlayerFetchResult(status="ok", player=player)
        except coc.NotFound as exc:
            log.warning("Player %s not found", player_tag)
            return PlayerFetchResult(status="not_found", exception=exc)
        except coc.HTTPException as exc:
            status_code = getattr(exc, "status", None)

            if status_code == 403:
                if attempt < max_retries:
                    log.warning(
                        "CoC API 403 error for player %s, attempting re-authentication (attempt %d/%d)",
                        player_tag,
                        attempt + 1,
                        max_retries,
                    )

                    async with _reauth_lock:
                        current_time = time.time()
                        if current_time - _last_reauth_attempt > reauth_cooldown:
                            try:
                                await client.login(email, password)
                                _last_reauth_attempt = current_time
                                log.info("CoC API re-authentication successful")
                            except coc.HTTPException as login_exc:
                                log.error(
                                    "CoC API re-authentication failed: %s", login_exc
                                )
                                return PlayerFetchResult(
                                    status="access_denied", exception=login_exc
                                )
                        else:
                            log.debug("Skipping re-authentication (too recent)")

                    continue

                log.error(
                    "CoC API 403 error after %d retries for %s: %s",
                    max_retries,
                    player_tag,
                    exc,
                )
                return PlayerFetchResult(status="access_denied", exception=exc)

            log.error("CoC API error fetching %s: %s", player_tag, exc)
            return PlayerFetchResult(status="error", exception=exc)

    # Exhausted retries without success - treat as generic error
    log.error("CoC API error fetching %s: retries exhausted", player_tag)
    return PlayerFetchResult(status="error")
