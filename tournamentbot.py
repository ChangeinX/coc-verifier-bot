#!/usr/bin/env python3
"""Discord bot managing Clash of Clans tournament registrations."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Final

import boto3
import coc
import discord
from discord import app_commands
from discord.abc import Messageable
from discord.app_commands import errors as app_errors

from tournament_bot import (
    BracketState,
    InvalidTownHallError,
    InvalidValueError,
    PlayerEntry,
    TeamRegistration,
    TournamentConfig,
    TournamentStorage,
    parse_player_tags,
    parse_registration_datetime,
    parse_town_hall_levels,
    utc_now_iso,
    validate_max_teams,
    validate_registration_window,
    validate_team_size,
)
from tournament_bot.bracket import (
    create_bracket_state,
    render_bracket,
    set_match_winner,
    simulate_tournament,
)
from tournament_bot.simulator import build_seeded_registrations
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

coc_client: coc.Client | None = None


def isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def ensure_coc_client() -> coc.Client:
    global coc_client
    if coc_client is None:
        if not COC_EMAIL or not COC_PASSWORD:
            raise RuntimeError(
                "COC_EMAIL and COC_PASSWORD are required for seeded simulations"
            )
        coc_client = coc.Client()
        await coc_client.login(COC_EMAIL, COC_PASSWORD)
    return coc_client


async def build_seeded_registrations_for_guild(guild_id: int) -> list[TeamRegistration]:
    """Fetch live player data and build seeded tournament registrations."""
    if not COC_EMAIL or not COC_PASSWORD:
        raise RuntimeError(
            "COC_EMAIL and COC_PASSWORD must be configured for simulations"
        )
    client = await ensure_coc_client()
    return await build_seeded_registrations(
        client,
        COC_EMAIL,
        COC_PASSWORD,
        guild_id,
    )


def format_display(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%b %d, %Y %H:%M UTC")


def format_config_message(
    config: TournamentConfig,
    *,
    opens_at: datetime | None = None,
    closes_at: datetime | None = None,
) -> str:
    if opens_at is None or closes_at is None:
        try:
            opens_at, closes_at = config.registration_window()
        except (ValueError, AttributeError):
            opens_at = closes_at = None

    allowed = ", ".join(str(level) for level in config.allowed_town_halls)
    window = (
        f"{format_display(opens_at)} — {format_display(closes_at)}"
        if opens_at and closes_at
        else "Not configured"
    )
    return (
        "Tournament configuration updated.\n"
        f"- Registration: {window}\n"
        f"- Team size: {config.team_size}\n"
        f"- Allowed Town Halls: {allowed}\n"
        f"- Maximum teams: {config.max_teams}"
    )


def build_setup_embed(
    config: TournamentConfig,
    *,
    opens_at: datetime,
    closes_at: datetime,
    requested_by: discord.abc.User,
) -> discord.Embed:
    embed = discord.Embed(
        title="Clash Time!",
        description="Registration window is locked. Rally your squad!",
        color=discord.Color.orange(),
        timestamp=closes_at,
    )
    embed.add_field(
        name="Registration Window",
        value=f"{format_display(opens_at)} — {format_display(closes_at)}",
        inline=False,
    )
    embed.add_field(
        name="Team Size",
        value=f"{config.team_size} players",
        inline=True,
    )
    embed.add_field(
        name="Allowed Town Halls",
        value=", ".join(str(level) for level in config.allowed_town_halls),
        inline=True,
    )
    embed.add_field(
        name="Max Teams",
        value=str(config.max_teams),
        inline=True,
    )
    embed.set_footer(text=f"Configured by {requested_by}")
    return embed


def format_lineup_table(players: list[PlayerEntry]) -> str:
    headers = ("Player", "TH", "Player Tag", "Clan")
    rows: list[tuple[str, str, str, str]] = []
    for player in players:
        clan_parts: list[str] = []
        if player.clan_name:
            clan_parts.append(player.clan_name)
        if player.clan_tag:
            clan_parts.append(player.clan_tag)
        clan_value = " ".join(clan_parts) if clan_parts else "-"
        rows.append(
            (
                player.name,
                f"TH{player.town_hall}",
                player.tag,
                clan_value,
            )
        )

    if not rows:
        return "No players registered"

    widths = [len(header) for header in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))

    lines = [" ".join(headers[idx].ljust(widths[idx]) for idx in range(len(headers)))]
    lines.append(" ".join("-" * widths[idx] for idx in range(len(headers))))
    for row in rows:
        lines.append(
            " ".join(row[idx].ljust(widths[idx]) for idx in range(len(headers)))
        )
    return "\n".join(lines)


def build_registration_embed(
    registration: TeamRegistration,
    *,
    config: TournamentConfig,
    closes_at: datetime,
    is_update: bool,
) -> discord.Embed:
    embed = discord.Embed(
        title="Team registration updated!" if is_update else "Team registered!",
        description=f"Captain: {registration.user_name}",
        color=discord.Color.green(),
        timestamp=datetime.now(UTC),
    )
    players_table = format_lineup_table(registration.players)
    embed.add_field(
        name="Lineup",
        value=f"```\n{players_table}\n```",
        inline=False,
    )
    embed.set_footer(text=f"Teams lock {format_display(closes_at)}")
    embed.add_field(
        name="Team Size",
        value=f"{len(registration.players)}/{config.team_size}",
        inline=True,
    )
    embed.add_field(
        name="Town Halls",
        value=", ".join(
            sorted({f"TH{player.town_hall}" for player in registration.players})
        )
        or "-",
        inline=True,
    )
    return embed


def bracket_summary(bracket: BracketState) -> str:
    if not bracket.rounds:
        return "No rounds configured"
    first_round = bracket.rounds[0]
    team_ids = {
        slot.team_id
        for match in first_round.matches
        for slot in (match.competitor_one, match.competitor_two)
        if slot.team_id is not None
    }
    round_names = ", ".join(round_.name for round_ in bracket.rounds)
    return f"Teams: {len(team_ids)} | Rounds: {round_names}"


def bracket_champion_name(bracket: BracketState) -> str | None:
    if not bracket.rounds:
        return None
    final_round = bracket.rounds[-1]
    if not final_round.matches:
        return None
    winner_slot = final_round.matches[-1].winner_slot()
    if winner_slot and winner_slot.team_id is not None:
        return winner_slot.display()
    return None


def build_bracket_embed(
    bracket: BracketState,
    *,
    title: str,
    requested_by: discord.abc.User | None,
    summary_note: str | None = None,
) -> discord.Embed:
    graph = render_bracket(bracket)
    description = f"```\n{graph}\n```" if graph else "Bracket is empty"
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple(),
        timestamp=datetime.now(UTC),
    )
    summary = bracket_summary(bracket)
    if summary:
        embed.add_field(name="Summary", value=summary, inline=False)
    if summary_note:
        embed.add_field(name="Note", value=summary_note, inline=False)
    if requested_by is not None:
        embed.set_footer(text=f"Updated by {requested_by}")
    return embed


async def fetch_players(tags: list[str]) -> list[PlayerEntry]:
    async def fetch(tag: str) -> PlayerEntry:
        if coc_client is None:
            raise RuntimeError("Clash of Clans client is not initialized")
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
        clan = getattr(player, "clan", None)
        clan_name = getattr(clan, "name", None) if clan else None
        clan_tag = getattr(clan, "tag", None) if clan else None
        return PlayerEntry(
            name=player.name,
            tag=tag,
            town_hall=int(town_hall),
            clan_name=clan_name,
            clan_tag=clan_tag,
        )

    player_entries = await asyncio.gather(*[fetch(tag) for tag in tags])
    return list(player_entries)


def ensure_guild(interaction: discord.Interaction) -> discord.Guild:
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("This command can only be used in a server")
    return guild


async def send_ephemeral(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# ---------- Slash Commands ----------
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    team_size="Number of players per team (increments of 5)",
    allowed_town_halls="Comma or space separated Town Hall levels (e.g. 16 17)",
    max_teams="Maximum teams allowed (increments of 2)",
    registration_opens="When registration opens (UTC, e.g. 2024-05-01T18:00)",
    registration_closes="When registration closes (UTC, e.g. 2024-05-10T22:00)",
)
@tree.command(name="setup", description="Configure tournament registration rules")
async def setup_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    team_size: int,
    allowed_town_halls: str,
    max_teams: int,
    registration_opens: str,
    registration_closes: str,
) -> None:
    try:
        guild = ensure_guild(interaction)
        team_size_validated = validate_team_size(team_size)
        allowed_levels = parse_town_hall_levels(allowed_town_halls)
        max_teams_validated = validate_max_teams(max_teams)
        opens_at_input = parse_registration_datetime(registration_opens)
        closes_at_input = parse_registration_datetime(registration_closes)
        opens_at, closes_at = validate_registration_window(
            opens_at_input, closes_at_input
        )
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

    if closes_at <= datetime.now(UTC):
        await interaction.response.send_message(
            "Registration end must be in the future.",
            ephemeral=True,
        )
        return

    config = TournamentConfig(
        guild_id=guild.id,
        team_size=team_size_validated,
        allowed_town_halls=allowed_levels,
        max_teams=max_teams_validated,
        registration_opens_at=isoformat_utc(opens_at),
        registration_closes_at=isoformat_utc(closes_at),
        updated_by=interaction.user.id,
        updated_at=utc_now_iso(),
    )
    storage.save_config(config)

    ack_message = format_config_message(config, opens_at=opens_at, closes_at=closes_at)
    await interaction.response.send_message(ack_message, ephemeral=True)

    channel = interaction.channel
    if isinstance(channel, Messageable):
        embed = build_setup_embed(
            config,
            opens_at=opens_at,
            closes_at=closes_at,
            requested_by=interaction.user,
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:  # pragma: no cover - network failure
            log.warning("Failed to send setup announcement: %s", exc)
    else:
        log.debug("Skipping channel announcement; channel not messageable")


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
        opens_at, closes_at = config.registration_window()
    except ValueError:
        log.error("Configuration for guild %s is missing registration window", guild.id)
        await interaction.response.send_message(
            "Tournament registration window is missing. Please ping an admin to re-run /setup.",
            ephemeral=True,
        )
        return

    now = datetime.now(UTC)
    if now < opens_at:
        await interaction.response.send_message(
            f"Registration hasn't opened yet. Come back {format_display(opens_at)}.",
            ephemeral=True,
        )
        return
    if now >= closes_at:
        await interaction.response.send_message(
            f"Registration closed {format_display(closes_at)}. Teams are locked—please contact an admin for help.",
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
            f"{player.name} (TH{player.town_hall})" for player in disallowed
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

    embed = build_registration_embed(
        registration,
        config=config,
        closes_at=closes_at,
        is_update=existing_registration is not None,
    )
    try:
        await interaction.followup.send(embed=embed)
    except discord.HTTPException as exc:  # pragma: no cover - network failure
        log.warning("Failed to send registration embed: %s", exc)
        await interaction.followup.send(
            "Team registered, but I couldn't post the announcement.",
            ephemeral=True,
        )


@app_commands.default_permissions(administrator=True)
@tree.command(name="create-bracket", description="Seed registered teams into a bracket")
async def create_bracket_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    storage_available = True
    try:
        storage.ensure_table()
    except RuntimeError as exc:
        storage_available = False
        log.info(
            "Tournament storage unavailable; falling back to seeded simulation: %s", exc
        )

    registrations = storage.list_registrations(guild.id) if storage_available else []
    if len(registrations) < 2:
        await send_ephemeral(
            interaction,
            "At least two registered teams are required to create a bracket.",
        )
        return

    existing = storage.get_bracket(guild.id)
    bracket = create_bracket_state(guild.id, registrations)
    storage.save_bracket(bracket)

    bye_count = sum(
        1
        for match in bracket.rounds[0].matches
        for slot in (match.competitor_one, match.competitor_two)
        if slot.team_id is None and slot.team_label == "BYE"
    )
    note_parts = [f"Teams seeded from {len(registrations)} registration(s)"]
    if bye_count:
        note_parts.append(f"Auto-advances applied for {bye_count} bye(s)")
    if existing is not None:
        note_parts.append("Replaced previous bracket state")
    note = " | ".join(note_parts)

    await send_ephemeral(
        interaction,
        "Bracket created successfully."
        + (" Replaced previous bracket." if existing is not None else ""),
    )

    channel = interaction.channel
    if isinstance(channel, Messageable):
        embed = build_bracket_embed(
            bracket,
            title="Tournament Bracket Created",
            requested_by=interaction.user,
            summary_note=note,
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:  # pragma: no cover - network failure
            log.warning("Failed to send bracket announcement: %s", exc)
    else:
        log.debug("Skipping bracket announcement; channel not messageable")


@create_bracket_command.error
async def create_bracket_error_handler(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_errors.MissingPermissions):
        await send_ephemeral(
            interaction,
            "You need administrator permissions to run this command.",
        )
        return
    log.exception("Unhandled create-bracket error: %s", error)
    await send_ephemeral(
        interaction,
        "An unexpected error occurred while creating the bracket.",
    )


@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    match_id="Match identifier (e.g. R1M1)",
    winner_slot="Winning slot: 1 for the first team, 2 for the second team",
)
@tree.command(
    name="select-round-winner",
    description="Record the winner for a bracket match",
)
async def select_round_winner_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    match_id: str,
    winner_slot: app_commands.Range[int, 1, 2],
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    try:
        storage.ensure_table()
    except RuntimeError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    bracket = storage.get_bracket(guild.id)
    if bracket is None:
        await send_ephemeral(
            interaction,
            "No bracket found. Run /create-bracket before selecting winners.",
        )
        return

    match_identifier = match_id.strip().upper()
    match_obj = bracket.find_match(match_identifier)
    if match_obj is None:
        await send_ephemeral(
            interaction,
            f"Match {match_identifier} was not found. Please double-check the ID.",
        )
        return

    previous_winner = match_obj.winner_index
    try:
        set_match_winner(bracket, match_identifier, winner_slot)
    except ValueError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    storage.save_bracket(bracket)
    winner_slot_obj = match_obj.winner_slot()
    if previous_winner == match_obj.winner_index and winner_slot_obj is not None:
        ack_message = (
            f"Winner for {match_identifier} remains {winner_slot_obj.display()}."
        )
    elif winner_slot_obj is not None:
        ack_message = f"Recorded {winner_slot_obj.display()} as the winner for {match_identifier}."
    else:
        ack_message = f"Recorded winner for {match_identifier}."

    champion = bracket_champion_name(bracket)
    if champion:
        ack_message += f" Current champion: {champion}."

    await send_ephemeral(interaction, ack_message)

    channel = interaction.channel
    if isinstance(channel, Messageable):
        embed = build_bracket_embed(
            bracket,
            title="Bracket Update",
            requested_by=interaction.user,
            summary_note=f"Winner recorded for {match_identifier}",
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:  # pragma: no cover - network failure
            log.warning("Failed to post bracket update: %s", exc)
    else:
        log.debug("Skipping bracket update; channel not messageable")


@select_round_winner_command.error
async def select_round_winner_error_handler(  # pragma: no cover - Discord wiring
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_errors.MissingPermissions):
        await send_ephemeral(
            interaction,
            "You need administrator permissions to run this command.",
        )
        return
    log.exception("Unhandled select-round-winner error: %s", error)
    await send_ephemeral(
        interaction,
        "An unexpected error occurred while recording the winner.",
    )


@app_commands.default_permissions(administrator=True)
@tree.command(name="simulate-tourney", description="Simulate the full tournament flow")
async def simulate_tourney_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    storage_available = True
    try:
        storage.ensure_table()
    except RuntimeError as exc:
        storage_available = False
        log.info(
            "Tournament storage unavailable; falling back to seeded simulation: %s",
            exc,
        )

    registrations = storage.list_registrations(guild.id) if storage_available else []
    use_seeded_registrations = False

    if len(registrations) < 2:
        try:
            registrations = await build_seeded_registrations_for_guild(guild.id)
            use_seeded_registrations = True
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Failed to build seeded registrations: %s", exc)
            await send_ephemeral(
                interaction,
                "Unable to load seeded tournament data for the simulation.",
            )
            return

    if len(registrations) < 2:
        await send_ephemeral(
            interaction,
            "At least two registered teams are required to run the simulation.",
        )
        return

    previous = (
        storage.get_bracket(guild.id)
        if storage_available and not use_seeded_registrations
        else None
    )
    bracket = create_bracket_state(guild.id, registrations)
    if storage_available and not use_seeded_registrations:
        storage.save_bracket(bracket)
    final_state, snapshots = simulate_tournament(bracket)
    if storage_available and not use_seeded_registrations:
        storage.save_bracket(final_state)

    channel = interaction.channel
    messages_posted = 0
    if isinstance(channel, Messageable):
        for idx, (label, snapshot) in enumerate(snapshots, start=1):
            embed = build_bracket_embed(
                snapshot,
                title=f"Simulation – {label}",
                requested_by=interaction.user,
                summary_note=f"Snapshot {idx} of {len(snapshots)}",
            )
            try:
                await channel.send(embed=embed)
                messages_posted += 1
            except discord.HTTPException as exc:  # pragma: no cover - network failure
                log.warning("Failed to send simulation snapshot: %s", exc)
                break
    else:
        log.debug("Skipping simulation announcements; channel not messageable")

    champion = bracket_champion_name(final_state)
    ack_message_parts = [
        "Simulation complete.",
        f"Posted {messages_posted} snapshot(s).",
    ]
    if not storage_available:
        ack_message_parts.append("Storage disabled; results not persisted.")
    if use_seeded_registrations:
        ack_message_parts.append("Used seeded roster for simulation.")
    if previous is not None:
        ack_message_parts.append("Previous bracket state was replaced.")
    if champion:
        ack_message_parts.append(f"Champion: {champion}.")
    await send_ephemeral(interaction, " ".join(ack_message_parts))


@simulate_tourney_command.error
async def simulate_tourney_error_handler(  # pragma: no cover - Discord wiring
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_errors.MissingPermissions):
        await send_ephemeral(
            interaction,
            "You need administrator permissions to run this command.",
        )
        return
    log.exception("Unhandled simulate-tourney error: %s", error)
    await send_ephemeral(
        interaction,
        "An unexpected error occurred while simulating the tournament.",
    )


# ---------- Lifecycle ----------
@bot.event
async def on_ready() -> None:  # pragma: no cover - Discord lifecycle hook
    await tree.sync()
    log.info("Signing in to CoC API for tournament bot...")
    global coc_client
    if coc_client is None:
        coc_client = coc.Client()
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
        if coc_client is not None:
            await coc_client.close()


if __name__ == "__main__":
    asyncio.run(main())
