"""Utility script to run a full tournament simulation without user prompts.

This script loads seed player tags from a text file, fetches live data from the
Clash of Clans API, builds tournament registrations, and runs the bracket
simulation. Output is printed to stdout so the workflow is fully non-interactive.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import coc

from tournament_bot import PlayerEntry, TeamRegistration
from tournament_bot.bracket import (
    create_bracket_state,
    render_bracket,
    simulate_tournament,
)
from tournament_bot.models import ISO_FORMAT
from verifier_bot import coc_api

DEFAULT_SEED_FILE = Path(__file__).with_name("data").joinpath("tourney_seed_tags.txt")
DEFAULT_BASE_REGISTRATION = datetime(2025, 1, 1, tzinfo=UTC)


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate a tournament bracket using live player data"
    )
    parser.add_argument(
        "--seed-file",
        type=Path,
        default=DEFAULT_SEED_FILE,
        help="Path to text file containing one player tag per line",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=1,
        help="Guild ID to embed in generated registrations",
    )
    parser.add_argument(
        "--base-time",
        type=str,
        default=DEFAULT_BASE_REGISTRATION.strftime(ISO_FORMAT),
        help="Registration timestamp seed (ISO-8601, defaults to 2025-01-01T00:00:00.000Z)",
    )
    parser.add_argument(
        "--no-bracket",
        action="store_true",
        help="Skip printing the rendered bracket (snapshots are still noted)",
    )
    return parser.parse_args()


def load_tags(seed_file: Path) -> list[str]:
    if not seed_file.exists():
        raise FileNotFoundError(f"Seed file not found: {seed_file}")
    tags: list[str] = []
    for line in seed_file.read_text(encoding="utf-8").splitlines():
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
) -> list[SeededPlayer]:
    seeded: list[SeededPlayer] = []
    for tag in tags:
        result = await coc_api.fetch_player_with_status(
            client,
            email,
            password,
            tag,
            max_retries=2,
            reauth_cooldown=90,
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
    players: Iterable[SeededPlayer], minimum: int, maximum: int
) -> None:
    outside: list[SeededPlayer] = [
        player for player in players if not (minimum <= player.town_hall <= maximum)
    ]
    if outside:
        details = ", ".join(f"{player.tag}(TH{player.town_hall})" for player in outside)
        raise ValueError(
            f"Players outside allowed Town Hall range {minimum}-{maximum}: {details}"
        )


def sorted_for_seeding(players: Sequence[SeededPlayer]) -> list[SeededPlayer]:
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
    base_time: datetime,
) -> list[TeamRegistration]:
    registrations: list[TeamRegistration] = []
    for index, player in enumerate(players):
        registered_at = (base_time + timedelta(seconds=index)).strftime(ISO_FORMAT)
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


def print_snapshots(snapshots: Sequence[tuple[str, object]]) -> None:
    for label, _ in snapshots:
        print(f"Snapshot recorded: {label}")


def render_and_print_final(bracket_state) -> None:
    rendered = render_bracket(bracket_state)
    print("\n=== Final Bracket ===")
    print(rendered)
    print("====================\n")


async def main_async() -> None:
    args = parse_args()
    email = os.getenv("COC_EMAIL")
    password = os.getenv("COC_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "COC_EMAIL and COC_PASSWORD environment variables are required"
        )

    base_time = datetime.fromisoformat(
        args.base_time.replace("Z", "+00:00")
    ).astimezone(UTC)
    tags = load_tags(args.seed_file)

    client = coc.Client()
    try:
        await client.login(email, password)
        players = await fetch_seeded_players(client, email, password, tags)
    finally:
        await client.close()

    ensure_town_hall_range(players, minimum=15, maximum=17)
    seeded_players = sorted_for_seeding(players)
    registrations = build_registrations(seeded_players, args.guild_id, base_time)

    bracket = create_bracket_state(args.guild_id, registrations)
    final_state, snapshots = simulate_tournament(bracket)

    print_snapshots(snapshots)
    if not args.no_bracket:
        render_and_print_final(final_state)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
