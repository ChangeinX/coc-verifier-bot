#!/usr/bin/env python3
"""Discord bot managing Clash of Clans tournament registrations."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Final

import boto3
import coc
import discord
from discord import app_commands
from discord.app_commands import errors as app_errors

from tournament_bot import (
    InvalidTownHallError,
    InvalidValueError,
    PlayerEntry,
    TeamRegistration,
    TournamentConfig,
    TournamentStorage,
    parse_player_tags,
    parse_town_hall_levels,
    utc_now_iso,
    validate_max_teams,
    validate_team_size,
)
from verifier_bot import coc_api

# ---------- Environment ----------
DISCORD_TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
COC_EMAIL: Final[str | None] = os.getenv("COC_EMAIL")
COC_PASSWORD: Final[str | None] = os.getenv("COC_PASSWORD")
TOURNAMENT_TABLE_NAME: Final[str | None] = os.getenv("TOURNAMENT_TABLE_NAME")
AWS_REGION: Final[str] = os.getenv("AWS_REGION", "us-east-1")

REQUIRED_VARS = (
    "DISCORD_TOKEN",
    "COC_EMAIL",
    "COC_PASSWORD",
    "TOURNAMENT_TABLE_NAME",
)

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("tournament-bot")

# ---------- Discord Setup ----------
intents = discord.Intents.default()
intents.guilds = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------- AWS / CoC Clients ----------
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(TOURNAMENT_TABLE_NAME) if TOURNAMENT_TABLE_NAME else None
storage = TournamentStorage(table)

coc_client = coc.Client()


def format_config_message(config: TournamentConfig) -> str:
    allowed = ", ".join(str(level) for level in config.allowed_town_halls)
    return (
        "Tournament configuration updated:\n"
        f"- Team size: {config.team_size}\n"
        f"- Allowed Town Halls: {allowed}\n"
        f"- Maximum teams: {config.max_teams}"
    )


async def fetch_players(tags: list[str]) -> list[PlayerEntry]:
    async def fetch(tag: str) -> PlayerEntry:
        player = await coc_api.get_player_with_retry(
            coc_client, COC_EMAIL, COC_PASSWORD, tag
        )
        if player is None:
            raise InvalidValueError(f"Player {tag} not found or API unavailable")
        town_hall = getattr(player, "town_hall_level", None) or getattr(
            player, "town_hall", None
        )
        if town_hall is None:
            raise InvalidValueError(
                f"Unable to determine town hall for {player.name} ({tag})"
            )
        return PlayerEntry(name=player.name, tag=tag, town_hall=int(town_hall))

    player_entries = await asyncio.gather(*[fetch(tag) for tag in tags])
    return list(player_entries)


def ensure_guild(interaction: discord.Interaction) -> discord.Guild:
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("This command can only be used in a server")
    return guild


# ---------- Slash Commands ----------
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    team_size="Number of players per team (increments of 5)",
    allowed_town_halls="Comma or space separated Town Hall levels (e.g. 16 17)",
    max_teams="Maximum teams allowed (increments of 2)",
)
@tree.command(name="setup", description="Configure tournament registration rules")
async def setup_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    team_size: int,
    allowed_town_halls: str,
    max_teams: int,
) -> None:
    try:
        guild = ensure_guild(interaction)
        team_size_validated = validate_team_size(team_size)
        allowed_levels = parse_town_hall_levels(allowed_town_halls)
        max_teams_validated = validate_max_teams(max_teams)
    except (InvalidValueError, InvalidTownHallError) as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    except RuntimeError as exc:  # pragma: no cover - safety check
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        storage.ensure_table()
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    config = TournamentConfig(
        guild_id=guild.id,
        team_size=team_size_validated,
        allowed_town_halls=allowed_levels,
        max_teams=max_teams_validated,
        updated_by=interaction.user.id,
        updated_at=utc_now_iso(),
    )
    storage.save_config(config)

    await interaction.response.send_message(
        format_config_message(config), ephemeral=True
    )


@setup_command.error
async def setup_error_handler(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_errors.MissingPermissions):
        await interaction.response.send_message(
            "You need administrator permissions to run this command.",
            ephemeral=True,
        )
        return
    if isinstance(error, app_errors.MissingAnyRole):  # pragma: no cover
        await interaction.response.send_message(
            "You lack the required role to configure the tournament.",
            ephemeral=True,
        )
        return
    log.exception("Unhandled setup command error: %s", error)
    try:
        await interaction.response.send_message(
            "An unexpected error occurred while running /setup.", ephemeral=True
        )
    except discord.InteractionResponded:
        await interaction.followup.send(
            "An unexpected error occurred while running /setup.", ephemeral=True
        )


@app_commands.describe(
    player_tags="Provide player tags separated by spaces or commas (e.g. #ABCD123 #EFGH456)",
)
@tree.command(name="registerteam", description="Register a team for the tournament")
async def register_team_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction, player_tags: str
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:  # pragma: no cover - slash commands only guilds
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        storage.ensure_table()
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    config = storage.get_config(guild.id)
    if config is None:
        await interaction.response.send_message(
            "Tournament has not been configured yet. Please ask an admin to run /setup.",
            ephemeral=True,
        )
        return

    try:
        tags = parse_player_tags(player_tags)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    if len(tags) != config.team_size:
        await interaction.response.send_message(
            f"Exactly {config.team_size} player tags are required for registration.",
            ephemeral=True,
        )
        return

    existing_registration = storage.get_registration(guild.id, interaction.user.id)
    current_count = storage.registration_count(guild.id)
    if existing_registration is None and current_count >= config.max_teams:
        await interaction.response.send_message(
            "The registration limit has been reached. Please contact an admin.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        players = await fetch_players(tags)
    except InvalidValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Unexpected error fetching player data: %s", exc)
        await interaction.followup.send(
            "Failed to validate player tags against the Clash of Clans API.",
            ephemeral=True,
        )
        return

    disallowed = [
        player
        for player in players
        if player.town_hall not in config.allowed_town_halls
    ]
    if disallowed:
        lines = ", ".join(
            f"{player.name} ({player.tag}) TH{player.town_hall}"
            for player in disallowed
        )
        await interaction.followup.send(
            f"These players have unsupported Town Hall levels: {lines}",
            ephemeral=True,
        )
        return

    registration = TeamRegistration(
        guild_id=guild.id,
        user_id=interaction.user.id,
        user_name=str(interaction.user),
        players=players,
        registered_at=utc_now_iso(),
    )
    storage.save_registration(registration)

    prefix = (
        "Team registration updated!"
        if existing_registration
        else "Team registered successfully!"
    )
    message = "\n".join([prefix, *registration.lines_for_channel])
    await interaction.followup.send(message)


# ---------- Lifecycle ----------
@bot.event
async def on_ready() -> None:  # pragma: no cover - Discord lifecycle hook
    await tree.sync()
    log.info("Signing in to CoC API for tournament bot...")
    await coc_client.login(COC_EMAIL, COC_PASSWORD)
    log.info("Tournament bot ready as %s (%s)", bot.user, bot.user.id)


async def main() -> None:  # pragma: no cover - CLI entry point
    missing = [var for var in REQUIRED_VARS if not os.getenv(var)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)  # type: ignore[arg-type]
    finally:
        await coc_client.close()


if __name__ == "__main__":
    asyncio.run(main())
