#!/usr/bin/env python3
"""Discord bot managing Clash of Clans tournament registrations."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime
from typing import Final

import boto3
import coc
import discord
from discord import app_commands
from discord.abc import Messageable
from discord.app_commands import errors as app_errors

from tournament_bot import (
    BracketMatch,
    BracketSlot,
    BracketState,
    InvalidTownHallError,
    InvalidValueError,
    PlayerEntry,
    TeamRegistration,
    TournamentConfig,
    TournamentSeries,
    TournamentStorage,
    parse_player_tags,
    parse_registration_datetime,
    parse_town_hall_levels,
    utc_now_iso,
    validate_max_teams,
    validate_registration_window,
    validate_team_name,
    validate_team_size,
)
from tournament_bot.bracket import (
    apply_team_names,
    create_bracket_state,
    render_bracket,
    set_match_winner,
    simulate_tournament,
    team_captain_lines,
)
from tournament_bot.simulator import build_seeded_registrations
from verifier_bot import coc_api

# ---------- Environment ----------
DISCORD_TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
COC_EMAIL: Final[str | None] = os.getenv("COC_EMAIL")
COC_PASSWORD: Final[str | None] = os.getenv("COC_PASSWORD")
TOURNAMENT_TABLE_NAME: Final[str | None] = os.getenv("TOURNAMENT_TABLE_NAME")
AWS_REGION: Final[str] = os.getenv("AWS_REGION", "us-east-1")
REGISTRATION_CHANNEL_ID_RAW: Final[str | None] = os.getenv(
    "TOURNAMENT_REGISTRATION_CHANNEL_ID"
)
TOURNAMENT_ADMIN_ROLE_ID: Final[int] = 1_400_887_994_445_205_707
GUILD_ID_RAW: Final[str | None] = os.getenv("TOURNAMENT_GUILD_ID")

REQUIRED_VARS = (
    "DISCORD_TOKEN",
    "COC_EMAIL",
    "COC_PASSWORD",
    "TOURNAMENT_TABLE_NAME",
    "TOURNAMENT_REGISTRATION_CHANNEL_ID",
)

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("tournament-bot")

if REGISTRATION_CHANNEL_ID_RAW:
    try:
        _registration_channel_id = int(REGISTRATION_CHANNEL_ID_RAW)
    except ValueError:
        log.warning(
            "Invalid TOURNAMENT_REGISTRATION_CHANNEL_ID=%s; expected an integer",
            REGISTRATION_CHANNEL_ID_RAW,
        )
        _registration_channel_id = None
else:
    _registration_channel_id = None

TOURNAMENT_REGISTRATION_CHANNEL_ID: Final[int | None] = _registration_channel_id

if GUILD_ID_RAW:
    try:
        _guild_id = int(GUILD_ID_RAW)
    except ValueError:
        log.warning(
            "Invalid TOURNAMENT_GUILD_ID=%s; expected an integer",
            GUILD_ID_RAW,
        )
        _guild_id = None
else:
    _guild_id = None

TOURNAMENT_GUILD_ID: Final[int | None] = _guild_id

# ---------- Discord Setup ----------
intents = discord.Intents.default()
intents.guilds = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

GUILD_OBJECT = (
    discord.Object(id=TOURNAMENT_GUILD_ID) if TOURNAMENT_GUILD_ID is not None else None
)


def tournament_command(*args, **kwargs):
    """Register a slash command scoped to the configured tournament guild."""

    def decorator(func):
        command_kwargs = dict(kwargs)
        if (
            GUILD_OBJECT is not None
            and "guild" not in command_kwargs
            and "guilds" not in command_kwargs
        ):
            command_kwargs["guild"] = GUILD_OBJECT
        return tree.command(*args, **command_kwargs)(func)

    return decorator


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


async def build_seeded_registrations_for_guild(
    guild_id: int, division_id: str
) -> list[TeamRegistration]:
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
        division_id=division_id,
        shuffle=True,
    )


def format_display(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%b %d, %Y %H:%M UTC")


def build_setup_overview_embed(
    series: TournamentSeries | None, divisions: list[TournamentConfig]
) -> discord.Embed:
    embed = discord.Embed(
        title="Tournament Setup",
        description="Configure registration windows and divisions for this guild.",
        color=discord.Color.teal(),
        timestamp=datetime.now(UTC),
    )

    if series is not None:
        opens_at, closes_at = series.registration_window()
        embed.add_field(
            name="Registration Window",
            value=f"{format_display(opens_at)} — {format_display(closes_at)}",
            inline=False,
        )
    else:
        embed.add_field(
            name="Registration Window",
            value="Not configured",
            inline=False,
        )

    if divisions:
        for config in divisions:
            allowed = ", ".join(str(level) for level in config.allowed_town_halls)
            details = (
                f"Team size: {config.team_size}\n"
                f"Max teams: {config.max_teams}\n"
                f"Allowed TH: {allowed or 'None'}"
            )
            embed.add_field(
                name=f"{config.division_name} ({config.division_id})",
                value=details,
                inline=False,
            )
    else:
        embed.add_field(
            name="Divisions",
            value="No divisions configured yet.",
            inline=False,
        )

    embed.set_footer(
        text="Use the buttons below to update the registration window and divisions."
    )
    return embed


class SetupView(discord.ui.View):
    def __init__(self, guild_id: int, requester_id: int) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.message: discord.Message | None = None
        self.preset_select = DivisionPresetSelect(self)
        self.existing_select: ExistingDivisionSelect | None = None
        self.add_item(self.preset_select)
        self._ensure_existing_select()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id or is_tournament_admin(
            interaction.user
        ):
            return True
        await interaction.response.send_message(
            "Only tournament admins may use this setup session.",
            ephemeral=True,
        )
        return False

    async def refresh(self) -> None:
        self._ensure_existing_select()
        if self.message is None:
            return
        series = storage.get_series(self.guild_id)
        divisions = storage.list_division_configs(self.guild_id)
        embed = build_setup_overview_embed(series, divisions)
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException as exc:  # pragma: no cover - network failure
            log.warning("Failed to refresh setup view: %s", exc)

    async def on_timeout(self) -> None:  # pragma: no cover - UI timeout
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def _ensure_existing_select(self) -> None:
        divisions = storage.list_division_configs(self.guild_id)
        if divisions:
            options = [
                discord.SelectOption(
                    label=cfg.division_name,
                    value=cfg.division_id,
                    description=cfg.division_id,
                )
                for cfg in divisions[:25]
            ]
            if self.existing_select is None:
                self.existing_select = ExistingDivisionSelect(self, options)
                self.add_item(self.existing_select)
            else:
                self.existing_select.refresh_options(options)
        elif self.existing_select is not None:
            self.remove_item(self.existing_select)
            self.existing_select = None

    async def open_division_modal(
        self, interaction: discord.Interaction, division_id: str | None
    ) -> None:
        existing = (
            storage.get_config(self.guild_id, division_id)
            if division_id is not None
            else None
        )
        modal = DivisionConfigModal(
            self,
            division_id=division_id,
            existing_config=existing,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="Update Registration Window",
        style=discord.ButtonStyle.primary,
    )
    async def update_window(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        series = storage.get_series(self.guild_id)
        await interaction.response.send_modal(RegistrationWindowModal(self, series))

    @discord.ui.button(
        label="Custom Division",
        style=discord.ButtonStyle.secondary,
    )
    async def add_division(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.open_division_modal(interaction, None)

    @discord.ui.button(
        label="Auto-create TH10-17 1v1",
        style=discord.ButtonStyle.success,
    )
    async def auto_create_divisions(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        created = []
        updated = []
        for level in range(10, 18):
            division_id = f"th{level}-1v1"
            display, allowed, team_size = infer_division_defaults(division_id)
            if not allowed:
                allowed = [level]
            config = TournamentConfig(
                guild_id=self.guild_id,
                division_id=division_id,
                division_name=display,
                team_size=1,
                allowed_town_halls=allowed,
                max_teams=258,
                updated_by=interaction.user.id,
                updated_at=utc_now_iso(),
            )
            existing = storage.get_config(self.guild_id, division_id)
            storage.save_config(config)
            storage.delete_registrations_for_division(self.guild_id, division_id)
            storage.delete_bracket(self.guild_id, division_id)
            if existing is None:
                created.append(display)
            else:
                updated.append(display)

        summary_parts: list[str] = []
        if created:
            summary_parts.append(f"Created {len(created)} division(s)")
        if updated:
            summary_parts.append(f"Reset {len(updated)} existing division(s)")
        message = ", ".join(summary_parts) if summary_parts else "No divisions changed."
        await interaction.response.send_message(message, ephemeral=True)
        await self.refresh()


class RegistrationWindowModal(discord.ui.Modal):
    def __init__(self, setup_view: SetupView, series: TournamentSeries | None) -> None:
        super().__init__(title="Update Registration Window")
        self._setup_view = setup_view
        default_opens = series.registration_opens_at if series else ""
        default_closes = series.registration_closes_at if series else ""
        self.opens_input = discord.ui.TextInput(
            label="Opens (UTC)",
            default=default_opens,
            placeholder="2024-05-01T18:00",
        )
        self.closes_input = discord.ui.TextInput(
            label="Closes (UTC)",
            default=default_closes,
            placeholder="2024-05-10T22:00",
        )
        self.add_item(self.opens_input)
        self.add_item(self.closes_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            opens_at = parse_registration_datetime(self.opens_input.value)
            closes_at = parse_registration_datetime(self.closes_input.value)
            opens_at, closes_at = validate_registration_window(opens_at, closes_at)
        except InvalidValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        series = TournamentSeries(
            guild_id=self._setup_view.guild_id,
            registration_opens_at=isoformat_utc(opens_at),
            registration_closes_at=isoformat_utc(closes_at),
            updated_by=interaction.user.id,
            updated_at=utc_now_iso(),
        )
        storage.save_series(series)
        await interaction.response.send_message(
            "Registration window updated.", ephemeral=True
        )
        await self._setup_view.refresh()


class DivisionPresetSelect(discord.ui.Select):
    PRESET_OPTIONS = [
        discord.SelectOption(label="TH12 1v1", value="th12-1v1"),
        discord.SelectOption(label="TH13 1v1", value="th13-1v1"),
        discord.SelectOption(label="TH14 1v1", value="th14-1v1"),
        discord.SelectOption(label="TH15 1v1", value="th15-1v1"),
    ]

    def __init__(self, setup_view: SetupView) -> None:
        super().__init__(
            placeholder="Quick add a division…",
            min_values=1,
            max_values=1,
            options=self.PRESET_OPTIONS,
        )
        self._setup_view = setup_view

    async def callback(
        self, interaction: discord.Interaction
    ) -> None:  # pragma: no cover - UI wiring
        division_id = normalize_division_value(self.values[0])
        existing = storage.get_config(self._setup_view.guild_id, division_id)
        if existing is not None:
            await interaction.response.send_message(
                f"Division {existing.division_name} already exists. Use the edit selector to modify it.",
                ephemeral=True,
            )
            return

        display_name, allowed_th, team_size = infer_division_defaults(division_id)
        if not allowed_th:
            allowed_th = [16, 17]
        config = TournamentConfig(
            guild_id=self._setup_view.guild_id,
            division_id=division_id,
            division_name=display_name or division_id.upper(),
            team_size=team_size,
            allowed_town_halls=allowed_th,
            max_teams=32,
            updated_by=interaction.user.id,
            updated_at=utc_now_iso(),
        )
        storage.save_config(config)
        await interaction.response.send_message(
            f"Division {config.division_name} created.", ephemeral=True
        )
        await self._setup_view.refresh()


class ExistingDivisionSelect(discord.ui.Select):
    def __init__(
        self, setup_view: SetupView, options: list[discord.SelectOption]
    ) -> None:
        super().__init__(
            placeholder="Edit an existing division…",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._setup_view = setup_view

    def refresh_options(self, options: list[discord.SelectOption]) -> None:
        self.options = options

    async def callback(
        self, interaction: discord.Interaction
    ) -> None:  # pragma: no cover - UI wiring
        division_id = self.values[0]
        await self._setup_view.open_division_modal(interaction, division_id)


class DivisionConfigModal(discord.ui.Modal):
    def __init__(
        self,
        setup_view: SetupView,
        *,
        division_id: str | None = None,
        existing_config: TournamentConfig | None = None,
    ) -> None:
        super().__init__(title="Add or Update Division")
        self._setup_view = setup_view
        defaults = infer_division_defaults(division_id) if division_id else ("", [], 1)
        if existing_config is not None:
            defaults = (
                existing_config.division_name,
                existing_config.allowed_town_halls,
                existing_config.team_size,
            )
        display_default = defaults[0]
        allowed_default = " ".join(str(level) for level in defaults[1])
        team_size_default = str(defaults[2])
        self._locked_division_id = (
            existing_config.division_id if existing_config is not None else None
        )
        division_default = self._locked_division_id or (
            division_id if division_id is not None else ""
        )
        self.division_id_input = discord.ui.TextInput(
            label=(
                "Division ID (cannot be changed)"
                if self._locked_division_id
                else "Division ID"
            ),
            placeholder="th12-1v1",
            min_length=2,
            max_length=32,
            default=division_default,
        )
        self.division_name_input = discord.ui.TextInput(
            label="Display Name",
            placeholder="TH12 1v1",
            required=False,
            default=display_default,
        )
        self.team_size_input = discord.ui.TextInput(
            label="Team Size",
            placeholder="1",
            default=team_size_default,
        )
        self.allowed_th_input = discord.ui.TextInput(
            label="Allowed Town Halls",
            placeholder="12 13",
            required=False,
            default=allowed_default,
        )
        self.max_teams_input = discord.ui.TextInput(
            label="Maximum Teams",
            placeholder="32",
            default=(
                str(existing_config.max_teams) if existing_config is not None else "32"
            ),
        )
        self.reset_input = discord.ui.TextInput(
            label="Reset tournament (type RESET)",
            placeholder="Leave blank to keep current bracket & registrations",
            required=False,
            max_length=5,
        )
        items = [
            self.division_name_input,
            self.team_size_input,
            self.allowed_th_input,
            self.max_teams_input,
        ]
        if self._locked_division_id is None:
            items.insert(0, self.division_id_input)
        else:
            items.append(self.reset_input)
        for item in items:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self._locked_division_id is not None:
            division_id = self._locked_division_id
        else:
            try:
                division_id = normalize_division_value(self.division_id_input.value)
            except InvalidValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return

        inferred_name, inferred_th, inferred_team_size = infer_division_defaults(
            division_id
        )

        division_name = self.division_name_input.value.strip()
        if not division_name:
            division_name = inferred_name or division_id.upper()

        try:
            team_size_raw = int(self.team_size_input.value)
        except ValueError:
            await interaction.response.send_message(
                "Team size must be a whole number.", ephemeral=True
            )
            return

        try:
            team_size = validate_team_size(team_size_raw)
            allowed_th_raw = self.allowed_th_input.value.strip()
            allowed_th = (
                parse_town_hall_levels(allowed_th_raw)
                if allowed_th_raw
                else inferred_th
            )
            if not allowed_th:
                raise InvalidTownHallError(
                    "Unable to infer allowed Town Halls; please specify them explicitly."
                )
            max_teams_raw = int(self.max_teams_input.value)
            max_teams = validate_max_teams(max_teams_raw)
        except (InvalidValueError, InvalidTownHallError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        config = TournamentConfig(
            guild_id=self._setup_view.guild_id,
            division_id=division_id,
            division_name=division_name,
            team_size=team_size,
            allowed_town_halls=allowed_th,
            max_teams=max_teams,
            updated_by=interaction.user.id,
            updated_at=utc_now_iso(),
        )
        storage.save_config(config)

        reset_requested = self.reset_input.value.strip().upper() == "RESET"
        reset_summary = ""
        if reset_requested:
            removed = storage.delete_registrations_for_division(
                self._setup_view.guild_id, division_id
            )
            storage.delete_bracket(self._setup_view.guild_id, division_id)
            reset_summary = (
                f" Removed {removed} registration(s) and cleared the bracket."
            )

        await interaction.response.send_message(
            f"Division {division_name} saved.{reset_summary}", ephemeral=True
        )
        await self._setup_view.refresh()


def format_lineup_table(
    players: list[PlayerEntry], *, substitute: PlayerEntry | None = None
) -> str:
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

    if substitute is not None:
        clan_parts: list[str] = []
        if substitute.clan_name:
            clan_parts.append(substitute.clan_name)
        if substitute.clan_tag:
            clan_parts.append(substitute.clan_tag)
        clan_value = " ".join(clan_parts) if clan_parts else "-"
        rows.append(
            (
                f"{substitute.name} (Sub)",
                f"TH{substitute.town_hall}",
                substitute.tag,
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


def is_tournament_admin(member: discord.abc.User) -> bool:
    roles = getattr(member, "roles", None)
    if not roles:
        return False
    for role in roles:
        if getattr(role, "id", None) == TOURNAMENT_ADMIN_ROLE_ID:
            return True
    return False


def resolve_registration_owner(
    interaction: discord.Interaction,
    captain: discord.Member | None,
) -> tuple[discord.Member, bool]:
    actor = interaction.user
    actor_id = getattr(actor, "id", None)
    if actor_id is None:
        raise RuntimeError("This command can only be used within a server")

    if captain is None or getattr(captain, "id", None) == actor_id:
        return actor, False

    if not is_tournament_admin(actor):
        raise PermissionError("Only tournament admins can manage other teams")

    if getattr(captain, "id", None) is None:
        raise RuntimeError("Unable to identify the selected captain")

    return captain, True


def normalize_division_value(raw: str) -> str:
    value = raw.strip().lower()
    if not value:
        raise InvalidValueError(
            "Division is required. Please select a tournament division."
        )
    return value


def infer_division_defaults(division_id: str | None) -> tuple[str, list[int], int]:
    if not division_id:
        return ("", [], 1)
    text = division_id.lower()
    display_name = division_id.upper().replace("-", " ")

    start = None
    end = None
    if text.startswith("th"):
        match = re.match(r"th(\d+)", text)
        if match:
            start = int(match.group(1))
            remainder = text[match.end() :]
            if remainder.startswith("-"):
                tail = remainder[1:]
                tail_match = re.fullmatch(r"(?:th)?(\d+)", tail)
                if tail_match:
                    end = int(tail_match.group(1))

    allowed: list[int] = []
    if start is not None:
        if end is not None:
            low, high = sorted((start, end))
            allowed = list(range(low, high + 1))
        else:
            allowed = [start]

    team_size = 1 if "1v1" in text else 5
    return (display_name, allowed, team_size)


async def division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    guild = interaction.guild
    if guild is None:
        return []

    try:
        storage.ensure_table()
    except RuntimeError:
        return []

    division_ids = storage.list_division_ids(guild.id)
    if not division_ids:
        return []

    current_lower = current.strip().lower()
    matches = [
        division_id
        for division_id in division_ids
        if not current_lower or current_lower in division_id.lower()
    ]
    if not matches:
        matches = division_ids

    choices: list[app_commands.Choice[str]] = []
    for division_id in matches[:25]:
        choices.append(app_commands.Choice(name=division_id.upper(), value=division_id))
    return choices


def build_registration_embed(
    registration: TeamRegistration,
    *,
    config: TournamentConfig,
    series: TournamentSeries,
    is_update: bool,
) -> discord.Embed:
    _, closes_at = series.registration_window()
    team_title = registration.team_name or "Unnamed Team"
    verb = "updated" if is_update else "registered"
    embed = discord.Embed(
        title=f"{config.division_name} | Team {verb}: {team_title}",
        description=f"Captain: {registration.user_name}",
        color=discord.Color.green(),
        timestamp=datetime.now(UTC),
    )
    embed.add_field(name="Division", value=config.division_name, inline=True)
    embed.add_field(
        name="Team Size (Required)", value=str(config.team_size), inline=True
    )
    players_table = format_lineup_table(
        registration.players, substitute=registration.substitute
    )
    embed.add_field(
        name="Lineup",
        value=f"```\n{players_table}\n```",
        inline=False,
    )
    embed.set_footer(text=f"Teams lock {format_display(closes_at)}")
    starters = len(registration.players)
    substitute_text = ""
    if registration.substitute is not None:
        substitute_text = " + 1 sub"
    embed.add_field(
        name="Team Size",
        value=f"{starters} starters{substitute_text}",
        inline=True,
    )
    town_halls = {f"TH{player.town_hall}" for player in registration.players}
    if registration.substitute is not None:
        town_halls.add(f"TH{registration.substitute.town_hall}")
    embed.add_field(
        name="Town Halls", value=", ".join(sorted(town_halls)) or "-", inline=True
    )
    return embed


async def post_registration_announcement(
    guild: discord.Guild, embed: discord.Embed
) -> str | None:
    if TOURNAMENT_REGISTRATION_CHANNEL_ID is None:
        log.warning("Registration channel ID not configured; skipping announcement")
        return (
            "Team registered, but the registration channel isn't configured. "
            "Please contact an admin."
        )

    channel = guild.get_channel(TOURNAMENT_REGISTRATION_CHANNEL_ID)
    if channel is None:
        try:
            channel = await guild.fetch_channel(TOURNAMENT_REGISTRATION_CHANNEL_ID)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "Failed to resolve registration channel %s: %s",
                TOURNAMENT_REGISTRATION_CHANNEL_ID,
                exc,
            )
            return (
                "Team registered, but I couldn't reach the registration channel. "
                "Please contact an admin."
            )

    if not isinstance(channel, Messageable):
        log.warning(
            "Registration channel %s is not messageable (type=%s)",
            TOURNAMENT_REGISTRATION_CHANNEL_ID,
            type(channel).__name__,
        )
        return (
            "Team registered, but the registration channel cannot accept messages. "
            "Please contact an admin."
        )

    try:
        await channel.send(embed=embed)
    except discord.HTTPException as exc:  # pragma: no cover - network failure
        log.warning("Failed to send registration announcement: %s", exc)
        return (
            "Team registered, but I couldn't post the announcement. "
            "Please contact an admin."
        )

    return None


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
    shrink_completed: bool = False,
) -> discord.Embed:
    graph = render_bracket(bracket, shrink_completed=shrink_completed)
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
@tournament_command(name="setup", description="Configure tournament registration rules")
async def setup_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        storage.ensure_table()
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    series = storage.get_series(guild.id)
    divisions = storage.list_division_configs(guild.id)
    embed = build_setup_overview_embed(series, divisions)
    view = SetupView(guild.id, interaction.user.id)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:  # pragma: no cover - defensive
        view.message = None


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
    division="Tournament division identifier (e.g. th12)",
    team_name="Team name to display on the bracket and announcements",
    player_tags="Provide player tags separated by spaces or commas (e.g. #ABCD123 #EFGH456)",
)
@tournament_command(
    name="registerteam", description="Register a team for the tournament"
)
async def register_team_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str,
    team_name: str,
    player_tags: str,
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

    try:
        division_id = normalize_division_value(division)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    series = storage.get_series(guild.id)
    if series is None:
        await interaction.response.send_message(
            "Tournament registration window is not configured. Please ask an admin to run /setup.",
            ephemeral=True,
        )
        return

    config = storage.get_config(guild.id, division_id)
    if config is None:
        await interaction.response.send_message(
            "That division is not configured. Please ask an admin to add it via /setup.",
            ephemeral=True,
        )
        return

    opens_at, closes_at = series.registration_window()

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
        name = validate_team_name(team_name)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        tags = parse_player_tags(player_tags)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    required = config.team_size
    if len(tags) < required:
        await interaction.response.send_message(
            f"At least {required} player tags are required for registration.",
            ephemeral=True,
        )
        return
    max_allowed = required if required <= 1 else required + 1
    if len(tags) > max_allowed:
        if required <= 1:
            await interaction.response.send_message(
                "Substitutes are not supported for this division.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"You can provide at most {required + 1} player tags including the optional sub.",
            ephemeral=True,
        )
        return

    existing_registration = storage.get_registration(
        guild.id, division_id, interaction.user.id
    )
    current_count = storage.registration_count(guild.id, division_id)
    if existing_registration is None and current_count >= config.max_teams:
        await interaction.response.send_message(
            "The registration limit has been reached. Please contact an admin.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

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

    starters = players[:required]
    substitute = players[required] if len(players) > required else None

    registration = TeamRegistration(
        guild_id=guild.id,
        division_id=division_id,
        user_id=interaction.user.id,
        user_name=str(interaction.user),
        players=starters,
        registered_at=utc_now_iso(),
        team_name=name,
        substitute=substitute,
    )
    storage.save_registration(registration)

    embed = build_registration_embed(
        registration,
        config=config,
        series=series,
        is_update=existing_registration is not None,
    )

    announcement_error = await post_registration_announcement(guild, embed)
    if announcement_error:
        await interaction.followup.send(announcement_error, ephemeral=True)
        return

    confirmation = "Team registered!"
    if TOURNAMENT_REGISTRATION_CHANNEL_ID is not None:
        confirmation = (
            "Team registered! Announcement posted in "
            f"<#{TOURNAMENT_REGISTRATION_CHANNEL_ID}>."
        )

    await interaction.followup.send(confirmation, ephemeral=True)


@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
    team_name="Team name to store",
    captain="Team captain to update (admins only)",
)
@tournament_command(name="teamname", description="Update or add a team name")
async def team_name_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str,
    team_name: str,
    captain: discord.Member | None = None,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        storage.ensure_table()
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        name = validate_team_name(team_name)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        owner, acting_for_other = resolve_registration_owner(interaction, captain)
    except PermissionError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        division_id = normalize_division_value(division)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    series = storage.get_series(guild.id)
    if series is None:
        await interaction.response.send_message(
            "Tournament registration window is not configured. Please ask an admin to run /setup.",
            ephemeral=True,
        )
        return

    registration = storage.get_registration(guild.id, division_id, owner.id)
    if registration is None:
        await interaction.response.send_message(
            "No registration found for that captain.", ephemeral=True
        )
        return

    if registration.division_id != division_id:
        await interaction.response.send_message(
            "That captain is not registered for the selected division.",
            ephemeral=True,
        )
        return

    config = storage.get_config(guild.id, division_id)
    if config is None:
        await interaction.response.send_message(
            "Tournament has not been configured yet. Please ask an admin to run /setup.",
            ephemeral=True,
        )
        return

    if registration.team_name == name:
        await interaction.response.send_message(
            "Team name is already set to that value.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    updated = TeamRegistration(
        guild_id=registration.guild_id,
        division_id=registration.division_id,
        user_id=registration.user_id,
        user_name=registration.user_name,
        players=list(registration.players),
        registered_at=registration.registered_at,
        team_name=name,
        substitute=registration.substitute,
    )
    storage.save_registration(updated)

    embed = build_registration_embed(
        updated,
        config=config,
        series=series,
        is_update=True,
    )
    announcement_error = await post_registration_announcement(guild, embed)

    ack_message = (
        "Team name updated."
        if not acting_for_other
        else "Team name updated for the selected captain."
    )
    await interaction.followup.send(ack_message, ephemeral=True)

    if announcement_error:
        await interaction.followup.send(announcement_error, ephemeral=True)


@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
    player_tag="Player tag for the substitute",
    captain="Team captain to update (admins only)",
)
@tournament_command(name="registersub", description="Add or replace a team substitute")
async def register_sub_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str,
    player_tag: str,
    captain: discord.Member | None = None,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        storage.ensure_table()
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        division_id = normalize_division_value(division)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    series = storage.get_series(guild.id)
    if series is None:
        await interaction.response.send_message(
            "Tournament registration window is not configured. Please ask an admin to run /setup.",
            ephemeral=True,
        )
        return

    config = storage.get_config(guild.id, division_id)
    if config is None:
        await interaction.response.send_message(
            "That division is not configured. Please ask an admin to add it via /setup.",
            ephemeral=True,
        )
        return

    if config.team_size <= 1:
        await interaction.response.send_message(
            "Substitutes are not supported for this division.",
            ephemeral=True,
        )
        return

    try:
        owner, acting_for_other = resolve_registration_owner(interaction, captain)
    except PermissionError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    registration = storage.get_registration(guild.id, division_id, owner.id)
    if registration is None:
        await interaction.response.send_message(
            "No registration found for that captain.", ephemeral=True
        )
        return

    required = config.team_size
    if len(registration.players) < required:
        await interaction.response.send_message(
            f"Team must have at least {required} registered players before adding a sub.",
            ephemeral=True,
        )
        return

    opens_at, closes_at = series.registration_window()

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
        tags = parse_player_tags(player_tag)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    if len(tags) != 1:
        await interaction.response.send_message(
            "Provide exactly one player tag for the substitute.",
            ephemeral=True,
        )
        return

    sub_tag = tags[0]
    existing_tags = {player.tag for player in registration.players}
    if registration.substitute is not None:
        existing_tags.add(registration.substitute.tag)
    if sub_tag in existing_tags:
        await interaction.response.send_message(
            "That player is already on the team.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        player = (await fetch_players([sub_tag]))[0]
    except InvalidValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Unexpected error fetching player data: %s", exc)
        await interaction.followup.send(
            "Failed to validate player tag against the Clash of Clans API.",
            ephemeral=True,
        )
        return

    if player.town_hall not in config.allowed_town_halls:
        await interaction.followup.send(
            f"This player has an unsupported Town Hall level: TH{player.town_hall}",
            ephemeral=True,
        )
        return

    updated = TeamRegistration(
        guild_id=registration.guild_id,
        division_id=registration.division_id,
        user_id=registration.user_id,
        user_name=registration.user_name,
        players=list(registration.players),
        registered_at=registration.registered_at,
        team_name=registration.team_name,
        substitute=player,
    )
    storage.save_registration(updated)

    embed = build_registration_embed(
        updated,
        config=config,
        series=series,
        is_update=True,
    )
    announcement_error = await post_registration_announcement(guild, embed)

    ack_message = (
        "Substitute registered!"
        if not acting_for_other
        else "Substitute registered for the selected captain!"
    )
    await interaction.followup.send(ack_message, ephemeral=True)
    if announcement_error:
        await interaction.followup.send(announcement_error, ephemeral=True)


@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
)
@tournament_command(name="showregistered", description="View registered teams")
async def show_registered_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        storage.ensure_table()
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        division_id = normalize_division_value(division)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    config = storage.get_config(guild.id, division_id)
    if config is None:
        await interaction.response.send_message(
            "That division is not configured. Please ask an admin to add it via /setup.",
            ephemeral=True,
        )
        return

    registrations = storage.list_registrations(guild.id, division_id)
    if not registrations:
        await interaction.response.send_message(
            "No teams have registered yet.", ephemeral=True
        )
        return

    lines: list[str] = []
    for idx, registration in enumerate(registrations, start=1):
        team_name = registration.team_name or "Unnamed Team"
        starters = len(registration.players)
        sub_text = " + sub" if registration.substitute is not None else ""
        lines.append(
            f"{idx}. {team_name} — Captain: {registration.user_name} "
            f"({starters} starters{sub_text})"
        )

    max_length = 1800
    chunks: list[list[str]] = []
    current: list[str] = []
    length = 0
    for line in lines:
        line_length = len(line) + (0 if not current else 1)
        if current and length + line_length > max_length:
            chunks.append(current)
            current = [line]
            length = len(line)
        else:
            current.append(line)
            length += line_length
    if current:
        chunks.append(current)

    header = f"Registered teams for {config.division_name}:\n"
    first_message = header + "\n".join(chunks[0])
    await interaction.response.send_message(first_message, ephemeral=True)

    for chunk in chunks[1:]:
        await interaction.followup.send("\n".join(chunk), ephemeral=True)


@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
)
@tournament_command(
    name="create-bracket", description="Seed registered teams into a bracket"
)
async def create_bracket_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    try:
        division_id = normalize_division_value(division)
    except InvalidValueError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException as exc:  # pragma: no cover - defensive
            log.warning("Failed to defer create-bracket interaction: %s", exc)

    try:
        storage.ensure_table()
    except RuntimeError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    series = storage.get_series(guild.id)
    if series is None:
        await send_ephemeral(
            interaction,
            "Tournament registration window is not configured. Please run /setup first.",
        )
        return

    config = storage.get_config(guild.id, division_id)
    if config is None:
        await send_ephemeral(
            interaction,
            "That division is not configured. Please add it via /setup before seeding a bracket.",
        )
        return

    registrations = storage.list_registrations(guild.id, division_id)
    if len(registrations) < 2:
        await send_ephemeral(
            interaction,
            "At least two registered teams are required in this division to create a bracket.",
        )
        return

    existing = storage.get_bracket(guild.id, division_id)
    bracket = create_bracket_state(guild.id, division_id, registrations)
    apply_team_names(bracket, registrations)
    storage.save_bracket(bracket)

    bye_count = sum(
        1
        for match in bracket.rounds[0].matches
        for slot in (match.competitor_one, match.competitor_two)
        if slot.team_id is None and slot.team_label == "BYE"
    )
    note_parts = [f"Seeded {len(registrations)} registration(s)"]
    if bye_count:
        note_parts.append(f"Auto-advances applied for {bye_count} bye(s)")
    if existing is not None:
        note_parts.append("Previous bracket replaced")
    note = " | ".join(note_parts)

    await send_ephemeral(
        interaction,
        f"Bracket created for {config.division_name}."
        + (" Replaced previous bracket." if existing is not None else ""),
    )

    channel = interaction.channel
    if isinstance(channel, Messageable):
        embed = build_bracket_embed(
            bracket,
            title=f"{config.division_name} | Tournament Bracket Created",
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


@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
)
@tournament_command(
    name="showbracket", description="Display the current tournament bracket"
)
async def show_bracket_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        storage.ensure_table()
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    try:
        division_id = normalize_division_value(division)
    except InvalidValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    bracket = storage.get_bracket(guild.id, division_id)
    if bracket is None:
        await interaction.response.send_message(
            "No bracket found for that division. Ask an admin to run /create-bracket.",
            ephemeral=True,
        )
        return

    config = storage.get_config(guild.id, division_id)
    if config is None:
        await interaction.response.send_message(
            "That division is not configured. Please ask an admin to add it via /setup.",
            ephemeral=True,
        )
        return

    registrations = storage.list_registrations(guild.id, division_id)

    bracket_for_display = bracket.clone()
    names_changed = apply_team_names(bracket_for_display, registrations)
    if names_changed:
        storage.save_bracket(bracket_for_display)
    bracket = bracket_for_display

    champion = bracket_champion_name(bracket)
    summary_note = f"Current champion: {champion}" if champion else None

    embed = build_bracket_embed(
        bracket,
        title=f"{config.division_name} | Current Tournament Bracket",
        requested_by=interaction.user,
        summary_note=summary_note,
    )

    captain_lines = team_captain_lines(bracket, registrations)
    if captain_lines:
        chunks: list[str] = []
        current: list[str] = []
        length = 0
        for line in captain_lines:
            addition = len(line) + (1 if current else 0)
            if current and length + addition > 1024:
                chunks.append("\n".join(current))
                current = [line]
                length = len(line)
            else:
                current.append(line)
                length += addition
        if current:
            chunks.append("\n".join(current))

        for idx, chunk in enumerate(chunks):
            field_name = "Teams & Captains" if idx == 0 else "Teams & Captains (cont.)"
            embed.add_field(name=field_name, value=chunk, inline=False)

    try:
        await interaction.response.send_message(embed=embed)
    except discord.HTTPException as exc:  # pragma: no cover - defensive
        log.warning("Failed to send showbracket response: %s", exc)
        await send_ephemeral(
            interaction,
            "Failed to display the bracket. Please try again shortly.",
        )


@show_bracket_command.error
async def show_bracket_error_handler(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    log.exception("Unhandled showbracket error: %s", error)
    await send_ephemeral(
        interaction,
        "An unexpected error occurred while showing the bracket.",
    )


@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
    winner_captain="Team captain (Discord user) for the winning team",
)
@tournament_command(
    name="select-round-winner",
    description="Record the winner for a bracket match",
)
async def select_round_winner_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str,
    winner_captain: discord.Member,
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

    try:
        division_id = normalize_division_value(division)
    except InvalidValueError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    bracket = storage.get_bracket(guild.id, division_id)
    if bracket is None:
        await send_ephemeral(
            interaction,
            "No bracket found for that division. Run /create-bracket before selecting winners.",
        )
        return

    registrations = storage.list_registrations(guild.id, division_id)
    registration_lookup = {entry.user_id: entry for entry in registrations}
    if apply_team_names(bracket, registrations):
        storage.save_bracket(bracket)

    eligible_matches: list[tuple[BracketMatch, BracketSlot, int]] = []
    for match in bracket.all_matches():
        if match.winner_index is not None:
            continue
        slots = (
            (match.competitor_one, 0),
            (match.competitor_two, 1),
        )
        for slot, index in slots:
            if slot.team_id != winner_captain.id:
                continue
            opponent_slot = slots[1 - index][0]
            if slot.team_id is None or opponent_slot.team_id is None:
                continue
            eligible_matches.append((match, slot, index))

    if not eligible_matches:
        # Determine if the captain is registered but already advanced or missing.
        registration = registration_lookup.get(winner_captain.id)
        if registration is None:
            await send_ephemeral(
                interaction,
                (
                    f"{winner_captain.mention} is not the registered captain of any team "
                    "in the current bracket."
                ),
            )
            return

        pending_matches: list[str] = []
        for match in bracket.all_matches():
            for slot in (match.competitor_one, match.competitor_two):
                if slot.team_id == winner_captain.id:
                    status = "decided" if match.winner_index is not None else "waiting"
                    pending_matches.append(f"- {match.match_id} ({status})")
        if pending_matches:
            match_summary = "\n".join(pending_matches)
            await send_ephemeral(
                interaction,
                (
                    f"No undecided match found for {winner_captain.mention}.\n"
                    "Tracked matches:\n"
                    f"{match_summary}"
                ),
            )
        else:
            await send_ephemeral(
                interaction,
                (f"{winner_captain.mention} does not currently appear in the bracket."),
            )
        return

    match_obj, selected_slot, selected_index = min(
        eligible_matches, key=lambda item: (item[0].round_index, item[0].match_id)
    )
    match_identifier = match_obj.match_id
    previous_winner = match_obj.winner_index
    try:
        set_match_winner(bracket, match_identifier, selected_index + 1)
    except ValueError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    storage.save_bracket(bracket)
    winner_slot_obj = match_obj.winner_slot()
    selected_registration = registration_lookup.get(selected_slot.team_id)
    if winner_slot_obj is not None:
        captain_text = (
            f"<@{selected_registration.user_id}> ({selected_registration.user_name})"
            if selected_registration is not None
            else winner_captain.mention
        )
    else:
        captain_text = winner_captain.mention

    if previous_winner == match_obj.winner_index and winner_slot_obj is not None:
        ack_message = f"Winner for {match_identifier} remains {winner_slot_obj.display()} (Captain: {captain_text})."
    elif winner_slot_obj is not None:
        ack_message = f"Recorded {winner_slot_obj.display()} (Captain: {captain_text}) as the winner for {match_identifier}."
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
@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
)
@tournament_command(
    name="simulate-tourney", description="Simulate the full tournament flow"
)
async def simulate_tourney_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException as exc:  # pragma: no cover - defensive
            log.warning("Failed to defer simulate-tourney interaction: %s", exc)

    try:
        division_id = normalize_division_value(division)
    except InvalidValueError as exc:
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

    registrations = (
        storage.list_registrations(guild.id, division_id) if storage_available else []
    )
    use_seeded_registrations = False

    if len(registrations) < 2:
        try:
            registrations = await build_seeded_registrations_for_guild(
                guild.id, division_id
            )
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

    bracket = create_bracket_state(guild.id, division_id, registrations)
    final_state, snapshots = simulate_tournament(bracket)
    messages_posted = 0
    for idx, (label, snapshot) in enumerate(snapshots, start=1):
        embed = build_bracket_embed(
            snapshot,
            title=f"{division_id.upper()} Simulation – {label}",
            requested_by=interaction.user,
            summary_note=f"Snapshot {idx} of {len(snapshots)}",
            shrink_completed=True,
        )
        try:
            await interaction.followup.send(embed=embed)
            messages_posted += 1
        except discord.HTTPException as exc:  # pragma: no cover - network failure
            log.warning("Failed to send simulation snapshot: %s", exc)
            break

    if messages_posted == 0:
        try:
            fallback_embed = build_bracket_embed(
                final_state,
                title=f"{division_id.upper()} Simulation – Final Bracket",
                requested_by=interaction.user,
                summary_note="Delivered via follow-up",
                shrink_completed=True,
            )
            await interaction.followup.send(embed=fallback_embed, ephemeral=True)
            messages_posted = 1
        except discord.HTTPException as exc:  # pragma: no cover - defensive
            log.warning("Failed to send fallback simulation snapshot: %s", exc)

    champion = bracket_champion_name(final_state)
    ack_message_parts = [
        "Simulation complete.",
        f"Posted {messages_posted} snapshot(s).",
    ]
    if use_seeded_registrations:
        ack_message_parts.append("Used seeded roster for simulation.")
    if not storage_available:
        ack_message_parts.append("Storage unavailable; results were not persisted.")

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


# ---------- Autocomplete Wiring ----------


@register_team_command.autocomplete("division")
async def _register_team_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@team_name_command.autocomplete("division")
async def _team_name_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@register_sub_command.autocomplete("division")
async def _register_sub_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@show_registered_command.autocomplete("division")
async def _show_registered_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@create_bracket_command.autocomplete("division")
async def _create_bracket_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@show_bracket_command.autocomplete("division")
async def _show_bracket_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@select_round_winner_command.autocomplete("division")
async def _select_round_winner_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@simulate_tourney_command.autocomplete("division")
async def _simulate_tourney_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


# ---------- Lifecycle ----------
@bot.event
async def on_ready() -> None:  # pragma: no cover - Discord lifecycle hook
    if TOURNAMENT_GUILD_ID is not None:
        guild = discord.Object(id=TOURNAMENT_GUILD_ID)
        await tree.sync(guild=guild)
        log.info("Commands synced to guild %s", TOURNAMENT_GUILD_ID)
    else:
        await tree.sync()
        log.info("Commands synced globally")
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
