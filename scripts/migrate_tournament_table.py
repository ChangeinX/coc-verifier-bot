#!/usr/bin/env python3
"""One-off migration tool for converting legacy tournament records to divisions.

The legacy schema stored a single configuration and bracket per guild using keys:
  - pk="GUILD#<guild_id>", sk="CONFIG"
  - pk="GUILD#<guild_id>", sk="TEAM#<user_id>"
  - pk="GUILD#<guild_id>", sk="BRACKET"

The new schema introduces a guild-level TournamentSeries plus division-scoped
configurations, registrations, and brackets. This script copies legacy items into
that layout so the bot can continue operating without manual re-entry.

Typical usage (dry-run):

    python scripts/migrate_tournament_table.py --table MyTournamentTable

Execute writes after reviewing the dry-run output:

    python scripts/migrate_tournament_table.py --table MyTournamentTable --execute

You can optionally override the division id/name used for migrated data.
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from typing import Any, Iterable

import boto3
from botocore.exceptions import ClientError

from tournament_bot.models import (
    BracketState,
    TeamRegistration,
    TournamentConfig,
    TournamentSeries,
)

log = logging.getLogger(__name__)


def load_all_items(table) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs = {"ExclusiveStartKey": last_key}
    return items


def delete_item(table, pk: str, sk: str, *, dry_run: bool) -> None:
    if dry_run:
        log.info("Would delete legacy item pk=%s sk=%s", pk, sk)
        return
    try:
        table.delete_item(Key={"pk": pk, "sk": sk})
    except ClientError as exc:  # pragma: no cover - defensive
        log.warning("Failed to delete %s/%s: %s", pk, sk, exc)


def migrate_guild(
    table,
    pk: str,
    items: dict[str, dict[str, Any]],
    *,
    division_id: str,
    division_name: str,
    dry_run: bool,
) -> None:
    guild_id = int(pk.split("#", 1)[1])
    existing_division_key = f"DIVISION#{division_id}#CONFIG"
    existing_bracket_key = f"DIVISION#{division_id}#BRACKET"

    legacy_config = items.get("CONFIG")
    if legacy_config:
        config = TournamentConfig.from_item(legacy_config)
        opens_value = str(
            legacy_config.get("registration_opens_at") or config.updated_at
        )
        closes_value = str(
            legacy_config.get("registration_closes_at") or config.updated_at
        )
        series = TournamentSeries(
            guild_id=guild_id,
            registration_opens_at=opens_value,
            registration_closes_at=closes_value,
            updated_by=config.updated_by,
            updated_at=config.updated_at,
        )

        config_new = TournamentConfig(
            guild_id=guild_id,
            division_id=division_id,
            division_name=division_name,
            team_size=config.team_size,
            allowed_town_halls=config.allowed_town_halls,
            max_teams=config.max_teams,
            updated_by=config.updated_by,
            updated_at=config.updated_at,
        )

        if existing_division_key in items:
            log.info(
                "Skipping config migration for guild %s (division %s already present)",
                guild_id,
                division_id,
            )
        else:
            if dry_run:
                log.info(
                    "Would write TournamentSeries and TournamentConfig for guild %s",
                    guild_id,
                )
            else:
                table.put_item(Item=series.to_item())
                table.put_item(Item=config_new.to_item())
            delete_item(table, pk, "CONFIG", dry_run=dry_run)
    else:
        series = None

    # Registrations
    legacy_registrations = {
        sk: item for sk, item in items.items() if sk.startswith("TEAM#")
    }
    for sk, item in legacy_registrations.items():
        registration = TeamRegistration.from_item(item)
        migrated = TeamRegistration(
            guild_id=registration.guild_id,
            division_id=division_id,
            user_id=registration.user_id,
            user_name=registration.user_name,
            players=list(registration.players),
            registered_at=registration.registered_at,
            team_name=registration.team_name,
            substitute=registration.substitute,
        )
        if dry_run:
            log.info(
                "Would migrate registration guild=%s user=%s to division %s",
                guild_id,
                registration.user_id,
                division_id,
            )
        else:
            table.put_item(Item=migrated.to_item())
        delete_item(table, pk, sk, dry_run=dry_run)

    # Bracket
    legacy_bracket = items.get("BRACKET")
    if legacy_bracket:
        if existing_bracket_key in items:
            log.info(
                "Skipping bracket migration for guild %s (division %s already present)",
                guild_id,
                division_id,
            )
        else:
            bracket = BracketState.from_item(legacy_bracket)
            migrated_bracket = BracketState(
                guild_id=bracket.guild_id,
                division_id=division_id,
                created_at=bracket.created_at,
                rounds=bracket.rounds,
            )
            if dry_run:
                log.info(
                    "Would migrate bracket for guild %s to division %s",
                    guild_id,
                    division_id,
                )
            else:
                table.put_item(Item=migrated_bracket.to_item())
            delete_item(table, pk, "BRACKET", dry_run=dry_run)

    if not legacy_config and not legacy_registrations and not legacy_bracket:
        log.debug("No legacy items found for guild %s", guild_id)


def migrate_table(
    table_name: str,
    *,
    profile: str | None,
    dry_run: bool,
    division_id: str,
    division_name: str,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    session_kwargs = {"profile_name": profile} if profile else {}
    session = boto3.Session(**session_kwargs)
    table = session.resource("dynamodb").Table(table_name)

    items = load_all_items(table)
    if not items:
        log.info("Table %s is empty; nothing to migrate", table_name)
        return

    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in items:
        grouped[item["pk"]][item["sk"]] = item

    log.info("Discovered %s guild(s) with tournament data to examine", len(grouped))

    for pk, guild_items in grouped.items():
        migrate_guild(
            table,
            pk,
            guild_items,
            division_id=division_id,
            division_name=division_name,
            dry_run=dry_run,
        )

    if dry_run:
        log.info("Dry run complete. Re-run with --execute to apply changes.")
    else:
        log.info("Migration complete. Verify results before restarting the bot.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy tournament data")
    parser.add_argument("--table", required=True, help="DynamoDB table name")
    parser.add_argument(
        "--division-id",
        default="legacy",
        help="Division id to assign to migrated data (default: legacy)",
    )
    parser.add_argument(
        "--division-name",
        default="Legacy Division",
        help="Display name for the migrated division",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional AWS profile name for boto3",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes. Without this flag the script performs a dry run.",
    )
    args = parser.parse_args()

    migrate_table(
        args.table,
        profile=args.profile,
        dry_run=not args.execute,
        division_id=args.division_id,
        division_name=args.division_name,
    )


if __name__ == "__main__":
    main()
