#!/usr/bin/env python3
"""Reset downstream bracket progress for in-flight tournaments.

This utility clears recorded winners for rounds beyond the opening round so that
admins can recover from the pre-window enforcement bug. By default it performs
no writes (dry-run). Pass ``--execute`` once you are satisfied with the planned
changes.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:  # pragma: no cover - simple environment setup
    sys.path.insert(0, str(ROOT_DIR))

from tournament_bot.models import BracketState

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ResetSummary:
    guild_id: int
    division_id: str
    match_id: str
    round_index: int
    winner_slot: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table",
        help="DynamoDB table name that stores tournament data",
    )
    parser.add_argument(
        "--guild",
        type=int,
        action="append",
        dest="guild_ids",
        help="Guild id to process (repeat for multiple). Defaults to tfvars values",
    )
    parser.add_argument(
        "--division",
        action="append",
        dest="divisions",
        help="Optional division id filter (repeatable)",
    )
    parser.add_argument(
        "--profile",
        help="Optional AWS profile to use",
    )
    parser.add_argument(
        "--region",
        help="AWS region (defaults to boto3's resolution order)",
    )
    parser.add_argument(
        "--input-file",
        help="Process bracket items from a JSON file instead of DynamoDB",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply fixes instead of printing the planned changes",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    return args


def load_tfvars_defaults() -> dict[str, Any]:
    """Return defaults gathered from infra/*.tfvars."""

    def _clean_value(raw: str) -> str:
        value = raw.strip().rstrip(",")
        if value.startswith("[") and value.endswith("]"):
            return value
        if value.startswith('"') and value.endswith('"'):
            return value[1:-1]
        if value.lower() == "null":
            return ""
        return value

    def _parse_file(path: pathlib.Path) -> dict[str, str]:
        results: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#", 1)[0].strip()
            if not stripped or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = _clean_value(value)
            if value.startswith("[") and value.endswith("]"):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:  # pragma: no cover - defensive
                    continue
                if parsed:
                    results[key] = parsed[0]
                continue
            results[key] = value
        return results

    defaults: dict[str, Any] = {
        "region": None,
        "table": None,
        "guild_ids": [],
    }
    pattern = ROOT_DIR.glob("infra/*.tfvars")
    for tfvars in sorted(pattern):
        data = _parse_file(tfvars)
        if not defaults["region"] and data.get("aws_region"):
            defaults["region"] = data["aws_region"]
        if not defaults["table"] and data.get("tournament_table_name"):
            defaults["table"] = data["tournament_table_name"]
        guild_value = data.get("tournament_guild_id")
        if guild_value:
            try:
                guild_id = int(re.sub(r"[^0-9]", "", guild_value))
            except ValueError:  # pragma: no cover - defensive
                continue
            if guild_id and guild_id not in defaults["guild_ids"]:
                defaults["guild_ids"].append(guild_id)
    return defaults


def should_include_division(division_id: str, selected: Sequence[str] | None) -> bool:
    if not selected:
        return True
    division_normalized = division_id.lower()
    return any(entry.lower() == division_normalized for entry in selected)


def iter_offline_brackets(path: str) -> Iterable[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):  # pragma: no cover - defensive
        raise ValueError("Input file must contain a JSON list of DynamoDB items")
    for item in data:
        if not isinstance(item, dict):  # pragma: no cover - defensive
            raise ValueError("Each entry in the input file must be a JSON object")
        yield item


def iter_dynamo_brackets(
    table,
    guild_id: int,
    divisions: Sequence[str] | None,
) -> Iterable[dict[str, Any]]:
    pk_value = f"GUILD#{guild_id}"
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("pk").eq(pk_value)
        & Key("sk").begins_with("DIVISION#"),
    }
    while True:
        response = table.query(**kwargs)
        items = response.get("Items", [])
        for item in items:
            sk_value = str(item.get("sk", ""))
            if not sk_value.endswith("#BRACKET"):
                continue
            division_id = str(
                item.get("division_id") or sk_value.split("#")[1]
                if "#" in sk_value
                else "default"
            )
            if should_include_division(division_id, divisions):
                yield item
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key


def reset_bracket(bracket: BracketState) -> list[ResetSummary]:
    summaries: list[ResetSummary] = []
    for round_index, round_obj in enumerate(bracket.rounds):
        if round_index == 0:
            continue
        for match in round_obj.matches:
            if match.winner_index is None:
                continue
            summaries.append(
                ResetSummary(
                    guild_id=bracket.guild_id,
                    division_id=bracket.division_id,
                    match_id=match.match_id,
                    round_index=round_index,
                    winner_slot=match.winner_index + 1,
                )
            )
            match.winner_index = None
    return summaries


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO), format="%(message)s"
    )


def main() -> None:
    args = parse_args()
    configure_logging(args.log_level)
    dry_run = not args.execute
    divisions = args.divisions or None

    defaults = load_tfvars_defaults()
    table_name = args.table or defaults.get("table")
    guild_ids = args.guild_ids or defaults.get("guild_ids") or []
    region = args.region or defaults.get("region")

    if args.input_file is None and not table_name:
        raise SystemExit("No DynamoDB table specified and none found in infra/*.tfvars")
    if args.input_file is None and not guild_ids:
        raise SystemExit("No guild ids provided and none found in infra/*.tfvars")

    overall_resets = 0

    if args.input_file:
        for item in iter_offline_brackets(args.input_file):
            bracket = BracketState.from_item(item)
            summaries = reset_bracket(bracket)
            if not summaries:
                log.info(
                    "No downstream winners to reset for guild %s division %s",
                    bracket.guild_id,
                    bracket.division_id,
                )
                continue
            overall_resets += len(summaries)
            for summary in summaries:
                log.info(
                    "Would clear winner slot %s for %s (round %s)",
                    summary.winner_slot,
                    summary.match_id,
                    summary.round_index + 1,
                )
            log.info(
                "Would update bracket for guild %s division %s",
                bracket.guild_id,
                bracket.division_id,
            )
        log.info("Dry-run complete. Total matches needing reset: %s", overall_resets)
        return

    session_kwargs: dict[str, Any] = {}
    if args.profile:
        session_kwargs["profile_name"] = args.profile
    if region:
        session_kwargs["region_name"] = region
    session = boto3.Session(**session_kwargs)
    table = session.resource("dynamodb").Table(table_name)

    try:
        for guild_id in guild_ids:
            for item in iter_dynamo_brackets(table, guild_id, divisions):
                bracket = BracketState.from_item(item)
                summaries = reset_bracket(bracket)
                if not summaries:
                    log.info(
                        "Guild %s division %s already clean",
                        bracket.guild_id,
                        bracket.division_id,
                    )
                    continue
                overall_resets += len(summaries)
                for summary in summaries:
                    log.info(
                        "%s: cleared winner slot %s for %s (round %s)",
                        "Would" if dry_run else "Cleared",
                        summary.winner_slot,
                        summary.match_id,
                        summary.round_index + 1,
                    )
                if dry_run:
                    log.info(
                        "Would update bracket for guild %s division %s",
                        bracket.guild_id,
                        bracket.division_id,
                    )
                else:
                    table.put_item(Item=bracket.to_item())
                    log.info(
                        "Updated bracket for guild %s division %s",
                        bracket.guild_id,
                        bracket.division_id,
                    )
    except (ClientError, BotoCoreError) as exc:  # pragma: no cover - network failure
        log.error("AWS request failed: %s", exc)
        raise SystemExit(2) from exc

    log.info(
        "%s complete. Total matches reset: %s",
        "Dry-run" if dry_run else "Execution",
        overall_resets,
    )


if __name__ == "__main__":
    main()
