"""Tournament simulation helpers for seeded Clash of Clans brackets."""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

import coc

from tournament_bot import PlayerEntry, TeamRegistration
from tournament_bot.models import ISO_FORMAT
from verifier_bot import coc_api

DEFAULT_BASE_REGISTRATION = datetime(2025, 1, 1, tzinfo=UTC)
DEFAULT_DATA_PACKAGE = "tournament_bot.data"
DEFAULT_SEED_FILENAME = "tourney_seed_tags.txt"


@dataclass(slots=True)
class SeededPlayer:
    """Minimal player data used for seeding the tournament."""

    name: str
    tag: str
    town_hall: int
    trophies: int
    exp_level: int
    clan_name: str | None
    clan_tag: str | None

    def team_label(self) -> str:
        clan_component = f" ({self.clan_name})" if self.clan_name else ""
        return f"{self.name}{clan_component}".strip()


def load_seed_tags(seed_file: Path | None = None) -> list[str]:
    """Load player tags used to seed the simulation bracket."""
    if seed_file is None:
        content = (
            resources.files(DEFAULT_DATA_PACKAGE) / DEFAULT_SEED_FILENAME
        ).read_text(encoding="utf-8")
    else:
        path = Path(seed_file)
        if not path.exists():
            raise FileNotFoundError(f"Seed file not found: {seed_file}")
        content = path.read_text(encoding="utf-8")

    tags: list[str] = []
    for line in content.splitlines():
        value = line.strip().upper()
        if not value:
            continue
        tags.append(value)
    if not tags:
        raise ValueError("Seed file did not contain any player tags")
    return tags


async def fetch_seeded_players(
    client: coc.Client,
    email: str,
    password: str,
    tags: Sequence[str],
    *,
    max_retries: int = 2,
    reauth_cooldown: int = 90,
) -> list[SeededPlayer]:
    """Fetch live player data for the provided tags."""
    seeded: list[SeededPlayer] = []
    for tag in tags:
        result = await coc_api.fetch_player_with_status(
            client,
            email,
            password,
            tag,
            max_retries=max_retries,
            reauth_cooldown=reauth_cooldown,
        )
        if result.status != "ok" or result.player is None:
            raise RuntimeError(
                f"Failed to load player data for {tag}: status={result.status}"
            )
        player = result.player
        clan = player.clan
        seeded.append(
            SeededPlayer(
                name=player.name,
                tag=player.tag,
                town_hall=getattr(player, "town_hall", 0),
                trophies=getattr(player, "trophies", 0),
                exp_level=getattr(player, "exp_level", 0),
                clan_name=clan.name if clan else None,
                clan_tag=clan.tag if clan else None,
            )
        )
    return seeded


def ensure_town_hall_range(
    players: Iterable[SeededPlayer], *, minimum: int, maximum: int
) -> None:
    """Raise if any seeded players fall outside the allowed Town Hall range."""
    outside = [
        player for player in players if not (minimum <= player.town_hall <= maximum)
    ]
    if outside:
        details = ", ".join(f"{player.tag}(TH{player.town_hall})" for player in outside)
        raise ValueError(
            f"Players outside allowed Town Hall range {minimum}-{maximum}: {details}"
        )


def sorted_for_seeding(players: Sequence[SeededPlayer]) -> list[SeededPlayer]:
    """Return players ordered by seed priority."""
    return sorted(
        players,
        key=lambda player: (
            -player.town_hall,
            -player.trophies,
            -player.exp_level,
            player.name.lower(),
            player.tag,
        ),
    )


def build_registrations(
    players: Sequence[SeededPlayer],
    guild_id: int,
    *,
    base_time: datetime | None = None,
    shuffle: bool = False,
    rng: random.Random | None = None,
) -> list[TeamRegistration]:
    """Convert seeded players into tournament registrations."""
    registrations: list[TeamRegistration] = []
    base = base_time or datetime.now(UTC)
    ordering = list(players)
    if shuffle:
        randomizer = rng or random.Random()
        randomizer.shuffle(ordering)

    for index, player in enumerate(ordering):
        registered_at = (base + timedelta(seconds=index)).strftime(ISO_FORMAT)
        entry = PlayerEntry(
            name=player.name,
            tag=player.tag,
            town_hall=player.town_hall,
            clan_name=player.clan_name,
            clan_tag=player.clan_tag,
        )
        registration = TeamRegistration(
            guild_id=guild_id,
            user_id=index + 1,
            user_name=player.team_label(),
            players=[entry],
            registered_at=registered_at,
        )
        registrations.append(registration)
    return registrations


async def build_seeded_registrations(
    client: coc.Client,
    email: str,
    password: str,
    guild_id: int,
    *,
    seed_file: Path | None = None,
    base_time: datetime | None = None,
    shuffle: bool = False,
    rng: random.Random | None = None,
) -> list[TeamRegistration]:
    """Load seeded registrations using live player data."""
    tags = load_seed_tags(seed_file)
    players = await fetch_seeded_players(client, email, password, tags)
    ensure_town_hall_range(players, minimum=15, maximum=17)
    seeded_players = sorted_for_seeding(players)
    return build_registrations(
        seeded_players,
        guild_id,
        base_time=base_time or DEFAULT_BASE_REGISTRATION,
        shuffle=shuffle,
        rng=rng,
    )


__all__ = [
    "SeededPlayer",
    "DEFAULT_BASE_REGISTRATION",
    "build_registrations",
    "build_seeded_registrations",
    "ensure_town_hall_range",
    "fetch_seeded_players",
    "load_seed_tags",
    "sorted_for_seeding",
]
