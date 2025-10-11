"""Utility script to run a seeded tournament simulation from the CLI."""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import coc

from tournament_bot.bracket import (
    create_bracket_state,
    render_bracket,
    simulate_tournament,
)
from tournament_bot.simulator import build_seeded_registrations

DEFAULT_BASE_TIME = datetime(2025, 1, 1, tzinfo=UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate a tournament bracket using live player data"
    )
    parser.add_argument(
        "--seed-file",
        type=Path,
        default=None,
        help="Optional path to a text file containing one player tag per line",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=1,
        help="Guild ID to embed in generated registrations",
    )
    parser.add_argument(
        "--division",
        type=str,
        default="th12",
        help="Division ID to assign to generated registrations",
    )
    parser.add_argument(
        "--no-bracket",
        action="store_true",
        help="Skip printing the rendered bracket (snapshots are still noted)",
    )
    parser.add_argument(
        "--base-time",
        type=str,
        default=DEFAULT_BASE_TIME.isoformat().replace("+00:00", "Z"),
        help="Registration timestamp seed (ISO-8601, defaults to 2025-01-01T00:00:00.000Z)",
    )
    return parser.parse_args()


def print_snapshots(snapshots):
    for idx, (label, _) in enumerate(snapshots, start=1):
        print(f"Snapshot {idx}: {label}")


def render_final_bracket(bracket_state):
    print("\n=== Final Bracket ===")
    print(render_bracket(bracket_state))
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

    client = coc.Client()
    try:
        await client.login(email, password)
        registrations = await build_seeded_registrations(
            client,
            email,
            password,
            args.guild_id,
            args.division,
            seed_file=args.seed_file,
            base_time=base_time,
        )
    finally:
        await client.close()

    bracket = create_bracket_state(args.guild_id, args.division, registrations)
    final_state, snapshots = simulate_tournament(bracket)

    print_snapshots(snapshots)
    if not args.no_bracket:
        render_final_bracket(final_state)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
