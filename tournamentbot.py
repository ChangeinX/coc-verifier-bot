#!/usr/bin/env python3
"""Discord bot managing Clash of Clans tournament registrations."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Final, Literal

import boto3
import coc
import discord
from discord import app_commands
from discord.abc import Messageable
from discord.app_commands import errors as app_errors

from tournament_bot import (
    BracketMatch,
    BracketRound,
    BracketSlot,
    BracketState,
    InvalidTownHallError,
    InvalidValueError,
    PlayerEntry,
    RoundWindowDefinition,
    TeamRegistration,
    TournamentConfig,
    TournamentRoundWindows,
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
from tournament_bot.models import ISO_FORMAT
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
DEFAULT_TOURNAMENT_ADMIN_ROLE_ID: Final[int] = 1_400_887_994_445_205_707
ADMIN_ROLE_ID_RAW: Final[str | None] = os.getenv("TOURNAMENT_ADMIN_ROLE_ID")
if ADMIN_ROLE_ID_RAW:
    try:
        _admin_role_id = int(ADMIN_ROLE_ID_RAW)
    except ValueError:
        log = logging.getLogger("tournament-bot")
        log.warning(
            "Invalid TOURNAMENT_ADMIN_ROLE_ID=%s; falling back to default",
            ADMIN_ROLE_ID_RAW,
        )
        _admin_role_id = DEFAULT_TOURNAMENT_ADMIN_ROLE_ID
else:
    _admin_role_id = DEFAULT_TOURNAMENT_ADMIN_ROLE_ID
TOURNAMENT_ADMIN_ROLE_ID: Final[int] = _admin_role_id

CAPTAIN_ROLE_ID_RAW: Final[str | None] = os.getenv("TOURNAMENT_CAPTAIN_ROLE_ID")
if CAPTAIN_ROLE_ID_RAW:
    try:
        _captain_role_id: int | None = int(CAPTAIN_ROLE_ID_RAW)
    except ValueError:
        log = logging.getLogger("tournament-bot")
        log.warning(
            "Invalid TOURNAMENT_CAPTAIN_ROLE_ID=%s; captain role syncing disabled.",
            CAPTAIN_ROLE_ID_RAW,
        )
        _captain_role_id = None
else:
    _captain_role_id = None
TOURNAMENT_CAPTAIN_ROLE_ID: Final[int | None] = _captain_role_id
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
intents.members = True

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


# ---------- Permission Checks ----------


def _has_admin_or_tournament_role(interaction: discord.Interaction) -> bool:
    member = interaction.user
    guild_perms = getattr(member, "guild_permissions", None)
    if getattr(guild_perms, "administrator", False):
        return True
    roles = getattr(member, "roles", [])
    for role in roles or []:
        if getattr(role, "id", None) == TOURNAMENT_ADMIN_ROLE_ID:
            return True
    return False


def require_admin_or_tournament_role():
    async def predicate(interaction: discord.Interaction) -> bool:
        if _has_admin_or_tournament_role(interaction):
            return True
        raise app_commands.CheckFailure(
            "You need administrator or tournament-admin role to run this command."
        )

    return app_commands.check(predicate)


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


ROUND_WINDOW_ENTRY_SPLIT = re.compile(r"[;\n]+")


def _normalize_round_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.strip().lower())


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


def parse_round_window_timestamp(raw: str) -> datetime:
    return datetime.strptime(raw, ISO_FORMAT).replace(tzinfo=UTC)


def parse_round_window_spec(
    raw_spec: str, rounds: Sequence[BracketRound]
) -> dict[int, tuple[str, str]]:
    if not rounds:
        raise InvalidValueError("This bracket does not have any rounds to configure.")

    spec = raw_spec.strip()
    if not spec:
        raise InvalidValueError(
            "Provide round windows using the format R1=2024-05-01T18:00..2024-05-05T18:00"
        )

    alias_map: dict[str, int] = {}
    for idx, round_obj in enumerate(rounds):
        aliases = {
            f"r{idx + 1}",
            f"round{idx + 1}",
            str(idx + 1),
            round_obj.name,
            round_obj.name.rstrip("s"),
        }
        cleaned_aliases = {
            _normalize_round_identifier(alias)
            for alias in aliases
            if alias and _normalize_round_identifier(alias)
        }
        for alias in cleaned_aliases:
            alias_map.setdefault(alias, idx)

    updates: dict[int, tuple[str, str]] = {}
    entries = [
        part.strip() for part in ROUND_WINDOW_ENTRY_SPLIT.split(spec) if part.strip()
    ]

    if not entries:
        raise InvalidValueError(
            "Provide round windows using the format R1=2024-05-01T18:00..2024-05-05T18:00"
        )

    for entry in entries:
        if "=" not in entry:
            raise InvalidValueError(
                "Each round window entry must include '=' between the round and window."
            )
        round_key_raw, window_values_raw = entry.split("=", 1)
        round_key = _normalize_round_identifier(round_key_raw)
        if not round_key:
            raise InvalidValueError("Round identifier cannot be empty.")
        if round_key not in alias_map:
            valid_examples = ", ".join(
                f"R{idx + 1}" for idx in range(min(4, len(rounds)))
            )
            raise InvalidValueError(
                f"Unknown round identifier '{round_key_raw.strip()}'. Try one of: {valid_examples}"
            )
        round_index = alias_map[round_key]
        if round_index in updates:
            raise InvalidValueError(
                f"Round '{rounds[round_index].name}' is specified more than once."
            )

        if ".." not in window_values_raw:
            raise InvalidValueError(
                "Use '..' between the start and end of each window (e.g. 2024-05-01T18:00..2024-05-05T18:00)."
            )
        start_raw, end_raw = (part.strip() for part in window_values_raw.split("..", 1))
        if not start_raw or not end_raw:
            raise InvalidValueError(
                "Both start and end times are required for each round window."
            )

        opens_at = parse_registration_datetime(start_raw)
        closes_at = parse_registration_datetime(end_raw)
        opens_at_utc, closes_at_utc = validate_registration_window(opens_at, closes_at)

        updates[round_index] = (
            isoformat_utc(opens_at_utc),
            isoformat_utc(closes_at_utc),
        )

    return updates


def _definitions_from_config(
    config: TournamentRoundWindows | None,
) -> list[RoundWindowDefinition]:
    if config is None:
        return []
    config.ensure_sequential_positions()
    return list(config.rounds)


def _format_window_input_value(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parsed = parse_round_window_timestamp(raw)
    except ValueError:
        return raw
    return parsed.strftime("%Y-%m-%dT%H:%M")


def apply_round_windows_to_bracket(
    bracket: BracketState,
    config: TournamentRoundWindows,
    *,
    clear_missing: bool,
) -> tuple[bool, int, int]:
    definitions = _definitions_from_config(config)
    changed = False
    aligned = 0
    cleared = 0
    for index, round_obj in enumerate(bracket.rounds):
        definition = definitions[index] if index < len(definitions) else None
        if definition is not None:
            if (
                round_obj.window_opens_at != definition.opens_at
                or round_obj.window_closes_at != definition.closes_at
            ):
                round_obj.window_opens_at = definition.opens_at
                round_obj.window_closes_at = definition.closes_at
                changed = True
            aligned += 1
        elif clear_missing and (
            round_obj.window_opens_at is not None
            or round_obj.window_closes_at is not None
        ):
            round_obj.window_opens_at = None
            round_obj.window_closes_at = None
            cleared += 1
            changed = True
    return changed, aligned, cleared


def apply_round_windows_to_guild(
    guild_id: int,
    config: TournamentRoundWindows,
    *,
    clear_missing: bool,
) -> tuple[int, int, int]:
    divisions_updated = 0
    total_aligned = 0
    total_cleared = 0
    for division_id in storage.list_division_ids(guild_id):
        bracket = storage.get_bracket(guild_id, division_id)
        if bracket is None:
            continue
        changed, aligned, cleared = apply_round_windows_to_bracket(
            bracket, config, clear_missing=clear_missing
        )
        total_aligned += aligned
        total_cleared += cleared
        if changed:
            storage.save_bracket(bracket)
            divisions_updated += 1
    return divisions_updated, total_aligned, total_cleared


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


def build_round_windows_embed(
    guild_id: int, config: TournamentRoundWindows | None
) -> discord.Embed:
    definitions = _definitions_from_config(config)
    embed = discord.Embed(
        title="Round Windows",
        description=(
            "Configure match windows for each round. These windows are applied "
            "to every division bracket that has been seeded."
        ),
        color=discord.Color.blurple(),
        timestamp=datetime.now(UTC),
    )

    if definitions:
        lines: list[str] = []
        for definition in definitions:
            try:
                opens_at = parse_round_window_timestamp(definition.opens_at)
                closes_at = parse_round_window_timestamp(definition.closes_at)
                window_text = (
                    f"{format_display(opens_at)} — {format_display(closes_at)}"
                )
            except ValueError:  # pragma: no cover - defensive, malformed data
                window_text = f"{definition.opens_at} — {definition.closes_at}"
            lines.append(f"R{definition.position}: {window_text}")
        embed.add_field(
            name="Configured Windows",
            value="\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="Configured Windows",
            value="No rounds configured yet. Use Add Round to create the first window.",
            inline=False,
        )

    division_configs = storage.list_division_configs(guild_id)
    coverage_lines: list[str] = []
    awaiting_bracket: list[str] = []
    for config_entry in division_configs:
        bracket = storage.get_bracket(guild_id, config_entry.division_id)
        division_name = config_entry.division_name or config_entry.division_id.upper()
        if bracket is None:
            awaiting_bracket.append(division_name)
            continue
        total_rounds = len(bracket.rounds)
        available = len(definitions)
        applied = min(total_rounds, available)
        missing = max(0, total_rounds - available)
        summary = f"{division_name}: {applied}/{total_rounds} round windows"
        if missing:
            summary += f" (missing {missing})"
        coverage_lines.append(summary)

    if coverage_lines:
        limited = coverage_lines[:12]
        remainder = len(coverage_lines) - len(limited)
        value = "\n".join(limited)
        if remainder > 0:
            value += f"\n… {remainder} more division(s)"
        embed.add_field(name="Bracket Coverage", value=value, inline=False)
    else:
        embed.add_field(
            name="Bracket Coverage",
            value="No brackets have been seeded yet.",
            inline=False,
        )

    if awaiting_bracket:
        limited = awaiting_bracket[:6]
        remainder = len(awaiting_bracket) - len(limited)
        value = ", ".join(limited)
        if remainder > 0:
            value += f" (+{remainder} more)"
        embed.add_field(
            name="Awaiting Bracket",
            value=value,
            inline=False,
        )

    if config is not None and config.updated_at:
        try:
            updated_at = datetime.strptime(config.updated_at, ISO_FORMAT).replace(
                tzinfo=UTC
            )
            embed.timestamp = updated_at
        except ValueError:  # pragma: no cover - defensive
            pass
        if config.updated_by:
            embed.set_footer(text=f"Last updated by <@{config.updated_by}>")

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


class BracketDivisionSelect(discord.ui.Select):
    def __init__(self, adjust_view: BracketAdjustView) -> None:
        self.adjust_view = adjust_view
        options = self.adjust_view._build_division_options()
        super().__init__(
            placeholder="Select a division",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.disabled = not options or options[0].value == "__none__"

    def refresh_options(self) -> None:
        options = self.adjust_view._build_division_options()
        self.options = options
        self.disabled = not options or options[0].value == "__none__"

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        selected = self.values[0]
        if selected == "__none__":
            await interaction.response.send_message(
                "No divisions are available to adjust.", ephemeral=True
            )
            return
        self.adjust_view.division_id = selected
        self.adjust_view.round_index = 0
        self.adjust_view.match_id = None
        self.adjust_view.match_page = 0
        self.adjust_view.round_select.refresh_options()
        self.adjust_view.match_select.refresh_options()
        await interaction.response.edit_message(
            embed=self.adjust_view.build_embed(), view=self.adjust_view
        )


class BracketRoundSelect(discord.ui.Select):
    def __init__(self, adjust_view: BracketAdjustView) -> None:
        self.adjust_view = adjust_view
        options = self.adjust_view._build_round_options()
        super().__init__(
            placeholder="Select a round",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.disabled = not options or options[0].value == "__none__"

    def refresh_options(self) -> None:
        options = self.adjust_view._build_round_options()
        self.options = options
        self.disabled = not options or options[0].value == "__none__"

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        selected = self.values[0]
        if selected == "__none__":
            await interaction.response.send_message(
                "No rounds available in this division.", ephemeral=True
            )
            return
        try:
            self.adjust_view.round_index = int(selected)
        except ValueError:
            self.adjust_view.round_index = 0
        self.adjust_view.match_id = None
        self.adjust_view.match_page = 0
        self.refresh_options()
        self.adjust_view.match_select.refresh_options()
        await interaction.response.edit_message(
            embed=self.adjust_view.build_embed(), view=self.adjust_view
        )


class BracketMatchSelect(discord.ui.Select):
    def __init__(self, adjust_view: BracketAdjustView) -> None:
        self.adjust_view = adjust_view
        options = self.adjust_view._build_match_options()
        super().__init__(
            placeholder="Select a match",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.disabled = not options or options[0].value in {"__none__", "__info__"}

    def refresh_options(self) -> None:
        options = self.adjust_view._build_match_options()
        self.options = options
        first_value = options[0].value if options else None
        self.disabled = not options or first_value in {"__none__", "__info__"}

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        selected = self.values[0]
        if selected == "__info__":
            await interaction.response.send_message(
                "More matches exist in this round. Use the *Find Match* button to look up a specific match ID.",
                ephemeral=True,
            )
            return
        if selected == "__none__":
            await interaction.response.send_message(
                "No matches available to select.", ephemeral=True
            )
            return
        if not self.adjust_view.set_match_id(selected):
            await interaction.response.send_message(
                f"Match {selected} is no longer available. Refresh and try again.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=self.adjust_view.build_embed(), view=self.adjust_view
        )


class MatchPickerModal(discord.ui.Modal):
    def __init__(self, adjust_view: BracketAdjustView) -> None:
        super().__init__(title="Find Match")
        self.adjust_view = adjust_view
        self.match_input = discord.ui.TextInput(
            label="Match ID",
            placeholder="R1M60",
            max_length=50,
        )
        self.add_item(self.match_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        match_id = self.match_input.value.strip()
        if not match_id:
            await interaction.response.send_message(
                "Enter a match ID to locate.", ephemeral=True
            )
            return
        if not self.adjust_view.set_match_id(match_id):
            await interaction.response.send_message(
                f"Match {match_id} was not found in this division.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Now showing match {match_id}.", ephemeral=True
        )
        await self.adjust_view.refresh_message()


class ReplaceCompetitorModal(discord.ui.Modal):
    def __init__(
        self,
        adjust_view: BracketAdjustView,
        *,
        slot_index: int,
    ) -> None:
        title = (
            "Replace Competitor One" if slot_index == 0 else "Replace Competitor Two"
        )
        super().__init__(title=title)
        self.adjust_view = adjust_view
        self.slot_index = slot_index
        self.captain_input = discord.ui.TextInput(
            label="Captain Discord ID",
            placeholder="1208203695028961330",
            max_length=32,
        )
        self.set_winner_input = discord.ui.TextInput(
            label="Set as match winner? (yes/no)",
            default="no",
            required=False,
            max_length=5,
        )
        self.add_item(self.captain_input)
        self.add_item(self.set_winner_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_id = self.captain_input.value.strip()
        try:
            captain_id = int(raw_id)
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "Provide a valid Discord user ID for the captain.",
                ephemeral=True,
            )
            return
        set_as_winner = self.set_winner_input.value.strip().lower() in {
            "yes",
            "true",
            "y",
            "1",
        }
        await self.adjust_view.replace_competitor(
            interaction,
            slot_index=self.slot_index,
            captain_id=captain_id,
            set_as_winner=set_as_winner,
        )


class BracketAdjustView(discord.ui.View):
    MATCH_PAGE_SIZE: ClassVar[int] = 25

    def __init__(
        self,
        guild_id: int,
        requester_id: int,
        *,
        initial_division: str | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.division_id: str | None = None
        self.round_index: int = 0
        self.match_id: str | None = None
        self.match_page: int = 0
        self.message: discord.Message | None = None

        self.division_select = BracketDivisionSelect(self)
        self.division_select.row = 0
        self.round_select = BracketRoundSelect(self)
        self.round_select.row = 1
        self.match_select = BracketMatchSelect(self)
        self.match_select.row = 2

        self.add_item(self.division_select)
        self.add_item(self.round_select)
        self.add_item(self.match_select)

        self._initialize_state(initial_division=initial_division)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id or is_tournament_admin(
            interaction.user
        ):
            return True
        await interaction.response.send_message(
            "Only tournament admins may adjust brackets.",
            ephemeral=True,
        )
        return False

    def _initialize_state(self, *, initial_division: str | None) -> None:
        division_ids = [
            cfg.division_id for cfg in storage.list_division_configs(self.guild_id)
        ]
        if not division_ids:
            self.division_id = None
            self.division_select.refresh_options()
            self.round_select.refresh_options()
            self.match_select.refresh_options()
            return
        if initial_division and initial_division in division_ids:
            self.division_id = initial_division
        else:
            for candidate in division_ids:
                bracket = storage.get_bracket(self.guild_id, candidate)
                if bracket is not None and bracket.rounds:
                    self.division_id = candidate
                    break
            if self.division_id is None:
                self.division_id = division_ids[0]

        bracket = (
            storage.get_bracket(self.guild_id, self.division_id)
            if self.division_id is not None
            else None
        )
        if bracket and bracket.rounds:
            self.round_index = 0
            self.match_page = 0
            first_round = bracket.rounds[0]
            if first_round.matches:
                self.match_id = first_round.matches[0].match_id

        self.division_select.refresh_options()
        self.round_select.refresh_options()
        self.match_select.refresh_options()

    async def on_timeout(self) -> None:  # pragma: no cover - UI timeout
        for child in self.children:
            if isinstance(child, discord.ui.Button) or isinstance(
                child, discord.ui.Select
            ):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def _build_division_options(self) -> list[discord.SelectOption]:
        configs = storage.list_division_configs(self.guild_id)
        if not configs:
            return [
                discord.SelectOption(
                    label="No divisions configured", value="__none__", default=True
                )
            ]
        options: list[discord.SelectOption] = []
        for cfg in configs[:25]:
            value = cfg.division_id
            default = value == self.division_id
            options.append(
                discord.SelectOption(
                    label=cfg.division_name[:100],
                    value=value,
                    description=cfg.division_id,
                    default=default,
                )
            )
        if self.division_id is None and options:
            options[0].default = True
        return options

    def _build_round_options(self) -> list[discord.SelectOption]:
        if self.division_id is None:
            return [
                discord.SelectOption(
                    label="Select a division first", value="__none__", default=True
                )
            ]
        bracket = storage.get_bracket(self.guild_id, self.division_id)
        if bracket is None or not bracket.rounds:
            return [
                discord.SelectOption(
                    label="No rounds available", value="__none__", default=True
                )
            ]
        options: list[discord.SelectOption] = []
        for idx, round_obj in enumerate(bracket.rounds[:25]):
            name = round_obj.name or f"Round {idx + 1}"
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(idx),
                    default=idx == self.round_index,
                )
            )
        if self.round_index >= len(bracket.rounds):
            self.round_index = max(0, len(bracket.rounds) - 1)
        return options or [
            discord.SelectOption(
                label="No rounds found", value="__none__", default=True
            )
        ]

    def _list_matches_for_round(self) -> list[BracketMatch]:
        if self.division_id is None:
            return []
        bracket = storage.get_bracket(self.guild_id, self.division_id)
        if bracket is None or not bracket.rounds:
            return []
        if self.round_index >= len(bracket.rounds):
            self.round_index = max(0, len(bracket.rounds) - 1)
        round_obj = bracket.rounds[self.round_index]
        return list(round_obj.matches)

    def _build_match_options(self) -> list[discord.SelectOption]:
        matches = self._list_matches_for_round()
        if not matches:
            return [
                discord.SelectOption(
                    label="No matches available", value="__none__", default=True
                )
            ]
        options: list[discord.SelectOption] = []
        overflow = len(matches) > self.MATCH_PAGE_SIZE
        limit = self.MATCH_PAGE_SIZE - 1 if overflow else self.MATCH_PAGE_SIZE
        for match in matches[: max(limit, 0)]:
            label = (
                f"{match.match_id}: {match.competitor_one.display()} vs "
                f"{match.competitor_two.display()}"
            )[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=match.match_id,
                    default=match.match_id == self.match_id,
                )
            )
        if overflow:
            options.append(
                discord.SelectOption(
                    label="More matches available…",
                    value="__info__",
                    description="Use the Find Match button",
                    default=False,
                )
            )
        if self.match_id is None and options:
            first_value = options[0].value
            if first_value not in {"__none__", "__info__"}:
                options[0].default = True
                self.match_id = first_value
        return options

    def set_match_id(self, match_id: str) -> bool:
        if self.division_id is None:
            return False
        bracket = storage.get_bracket(self.guild_id, self.division_id)
        if bracket is None:
            return False
        match = bracket.find_match(match_id)
        if match is None:
            return False
        self.match_id = match_id
        self.round_index = match.round_index
        self.round_select.refresh_options()
        self.match_select.refresh_options()
        return True

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Adjust Tournament Bracket",
            color=discord.Color.blurple(),
            timestamp=datetime.now(UTC),
        )
        if self.division_id is None:
            embed.description = "No tournament divisions are available. Add divisions with /setup before adjusting a bracket."
            return embed

        bracket = storage.get_bracket(self.guild_id, self.division_id)
        if bracket is None or not bracket.rounds:
            embed.description = "No bracket data found for this division. Create a bracket with /create-bracket first."
            embed.add_field(name="Division", value=self.division_id, inline=False)
            return embed

        match = bracket.find_match(self.match_id) if self.match_id else None
        if match is None:
            embed.description = "Select a match using the dropdown or the *Find Match* button to view and adjust details."
            embed.add_field(name="Division", value=self.division_id, inline=True)
            round_name = (
                describe_round(bracket, bracket.rounds[self.round_index])
                if bracket.rounds
                else "—"
            )
            embed.add_field(name="Round", value=round_name, inline=True)
            embed.add_field(
                name="Matches in round",
                value=str(len(self._list_matches_for_round())),
                inline=True,
            )
            return embed

        registrations = {
            registration.user_id: registration
            for registration in storage.list_registrations(
                self.guild_id, self.division_id
            )
        }

        embed.add_field(name="Division", value=self.division_id, inline=True)
        embed.add_field(name="Round", value=describe_round(bracket, match), inline=True)
        embed.add_field(name="Match", value=match.match_id, inline=True)
        embed.add_field(
            name="Competitor One",
            value=self._format_competitor(match.competitor_one, registrations),
            inline=False,
        )
        embed.add_field(
            name="Competitor Two",
            value=self._format_competitor(match.competitor_two, registrations),
            inline=False,
        )
        winner_slot = match.winner_slot()
        winner_text = (
            self._format_competitor(winner_slot, registrations)
            if winner_slot is not None
            else "Undecided"
        )
        embed.add_field(name="Winner", value=winner_text, inline=False)
        embed.set_footer(
            text="Use the buttons below to change the winner or replace a competitor."
        )
        return embed

    def _format_competitor(
        self,
        slot: BracketSlot,
        registrations: dict[int, TeamRegistration],
    ) -> str:
        if slot.team_id is None:
            return slot.display()
        registration = registrations.get(slot.team_id)
        label = slot.display()
        if registration is None:
            return f"{label}\nCaptain ID: {slot.team_id}"
        return f"{label}\nCaptain: <@{registration.user_id}> ({registration.user_name})"

    async def refresh_message(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(embed=self.build_embed(), view=self)
        except discord.HTTPException as exc:  # pragma: no cover - network failure
            log.warning("Failed to refresh bracket adjuster view: %s", exc)

    async def _finalize_action(
        self, interaction: discord.Interaction, message: str
    ) -> None:
        self.round_select.refresh_options()
        self.match_select.refresh_options()
        await self.refresh_message()
        await interaction.followup.send(message, ephemeral=True)

    async def set_winner(
        self, interaction: discord.Interaction, winner_index: int
    ) -> None:
        if self.division_id is None or self.match_id is None:
            await interaction.response.send_message(
                "Select a division and match before setting a winner.",
                ephemeral=True,
            )
            return
        bracket = storage.get_bracket(self.guild_id, self.division_id)
        if bracket is None:
            await interaction.response.send_message(
                "No bracket stored for this division.", ephemeral=True
            )
            return
        match = bracket.find_match(self.match_id)
        if match is None:
            await interaction.response.send_message(
                "Match could not be located. Select it again and retry.",
                ephemeral=True,
            )
            return
        if winner_index not in (0, 1):
            await interaction.response.send_message(
                "Winner index must be 0 or 1.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        assign_match_winner(bracket, match, winner_index)
        storage.save_bracket(bracket)
        winner_slot = match.winner_slot()
        label = winner_slot.display() if winner_slot is not None else "Undecided"
        await self._finalize_action(
            interaction,
            f"Updated winner for {match.match_id} to {label}.",
        )

    async def clear_winner(self, interaction: discord.Interaction) -> None:
        if self.division_id is None or self.match_id is None:
            await interaction.response.send_message(
                "Select a division and match before clearing a winner.",
                ephemeral=True,
            )
            return
        bracket = storage.get_bracket(self.guild_id, self.division_id)
        if bracket is None:
            await interaction.response.send_message(
                "No bracket stored for this division.", ephemeral=True
            )
            return
        match = bracket.find_match(self.match_id)
        if match is None:
            await interaction.response.send_message(
                "Match could not be located. Select it again and retry.",
                ephemeral=True,
            )
            return
        if match.winner_index is None:
            await interaction.response.send_message(
                "This match is already undecided.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        clear_match_winner(bracket, match)
        storage.save_bracket(bracket)
        await self._finalize_action(
            interaction, f"Cleared the recorded winner for {match.match_id}."
        )

    async def replace_competitor(
        self,
        interaction: discord.Interaction,
        *,
        slot_index: int,
        captain_id: int,
        set_as_winner: bool,
    ) -> None:
        if self.division_id is None or self.match_id is None:
            await interaction.response.send_message(
                "Select a division and match before replacing a competitor.",
                ephemeral=True,
            )
            return
        registration = storage.get_registration(
            self.guild_id, self.division_id, captain_id
        )
        if registration is None:
            await interaction.response.send_message(
                "That captain is not registered for the selected division.",
                ephemeral=True,
            )
            return
        bracket = storage.get_bracket(self.guild_id, self.division_id)
        if bracket is None:
            await interaction.response.send_message(
                "No bracket stored for this division.", ephemeral=True
            )
            return
        match = bracket.find_match(self.match_id)
        if match is None:
            await interaction.response.send_message(
                "Match could not be located. Select it again and retry.",
                ephemeral=True,
            )
            return
        if slot_index not in (0, 1):
            await interaction.response.send_message(
                "Slot index must be 0 or 1.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        replace_match_competitor(
            bracket,
            match,
            slot_index=slot_index,
            registration=registration,
            set_as_winner=set_as_winner,
        )
        storage.save_bracket(bracket)
        slot = match.competitor_one if slot_index == 0 else match.competitor_two
        action_label = f"Replaced competitor {slot_index + 1} with {slot.display()}"
        if set_as_winner:
            action_label += ", set as winner"
        await self._finalize_action(interaction, action_label + ".")

    @discord.ui.button(
        label="Set Winner → Competitor One",
        style=discord.ButtonStyle.primary,
        row=3,
    )
    async def declare_competitor_one(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.set_winner(interaction, 0)

    @discord.ui.button(
        label="Set Winner → Competitor Two",
        style=discord.ButtonStyle.primary,
        row=3,
    )
    async def declare_competitor_two(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.set_winner(interaction, 1)

    @discord.ui.button(
        label="Find Match",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def open_match_modal(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if self.division_id is None:
            await interaction.response.send_message(
                "Select a division before searching for matches.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(MatchPickerModal(self))

    @discord.ui.button(
        label="Clear Winner",
        style=discord.ButtonStyle.danger,
        row=3,
    )
    async def clear_winner_button(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.clear_winner(interaction)

    @discord.ui.button(
        label="Replace Competitor One",
        style=discord.ButtonStyle.secondary,
        row=4,
    )
    async def replace_competitor_one(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if self.match_id is None:
            await interaction.response.send_message(
                "Select a match before replacing a competitor.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            ReplaceCompetitorModal(self, slot_index=0)
        )

    @discord.ui.button(
        label="Replace Competitor Two",
        style=discord.ButtonStyle.secondary,
        row=4,
    )
    async def replace_competitor_two(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if self.match_id is None:
            await interaction.response.send_message(
                "Select a match before replacing a competitor.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            ReplaceCompetitorModal(self, slot_index=1)
        )


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


class RoundSelect(discord.ui.Select):
    def __init__(
        self,
        windows_view: RoundWindowsView,
        rounds: Sequence[RoundWindowDefinition],
    ) -> None:
        options = self._build_options(rounds)
        super().__init__(
            placeholder="Edit a configured round…",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._windows_view = windows_view

    @staticmethod
    def _build_options(
        rounds: Sequence[RoundWindowDefinition],
    ) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for definition in rounds:
            try:
                opens = parse_round_window_timestamp(definition.opens_at)
                closes = parse_round_window_timestamp(definition.closes_at)
                description = f"{format_display(opens)} — {format_display(closes)}"
            except ValueError:  # pragma: no cover - defensive
                description = f"{definition.opens_at} — {definition.closes_at}"
            options.append(
                discord.SelectOption(
                    label=f"Round {definition.position}",
                    value=str(definition.position - 1),
                    description=description[:100],
                )
            )
        return options

    def refresh(self, rounds: Sequence[RoundWindowDefinition]) -> None:
        self.options = self._build_options(rounds)
        self.disabled = not rounds

    async def callback(
        self, interaction: discord.Interaction
    ) -> None:  # pragma: no cover - UI wiring
        try:
            selection = int(self.values[0])
        except (ValueError, IndexError):
            await interaction.response.send_message(
                "Unable to determine the selected round.",
                ephemeral=True,
            )
            return
        await self._windows_view.open_round_modal(interaction, selection)


class RoundWindowsView(discord.ui.View):
    def __init__(
        self,
        guild_id: int,
        requester_id: int,
        *,
        initial_config: TournamentRoundWindows | None,
    ) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.message: discord.Message | None = None
        self.round_select: RoundSelect | None = None
        definitions = _definitions_from_config(initial_config)
        if definitions:
            self.round_select = RoundSelect(self, definitions)
            self.add_item(self.round_select)
        self._sync_button_state(definitions)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id or is_tournament_admin(
            interaction.user
        ):
            return True
        await interaction.response.send_message(
            "Only tournament admins may use this session.",
            ephemeral=True,
        )
        return False

    def _load_config(self) -> TournamentRoundWindows | None:
        config = storage.get_round_windows(self.guild_id)
        if config is not None:
            config.ensure_sequential_positions()
        return config

    def _sync_button_state(self, definitions: Sequence[RoundWindowDefinition]) -> None:
        has_rounds = bool(definitions)
        if hasattr(self, "remove_round"):
            self.remove_round.disabled = not has_rounds
        if self.round_select is not None:
            self.round_select.disabled = not has_rounds

    def _refresh_round_select(
        self, definitions: Sequence[RoundWindowDefinition]
    ) -> None:
        if definitions and self.round_select is None:
            self.round_select = RoundSelect(self, definitions)
            self.add_item(self.round_select)
        elif definitions and self.round_select is not None:
            self.round_select.refresh(definitions)
        elif not definitions and self.round_select is not None:
            self.remove_item(self.round_select)
            self.round_select = None

    async def refresh(self) -> None:
        config = self._load_config()
        definitions = _definitions_from_config(config)
        self._refresh_round_select(definitions)
        self._sync_button_state(definitions)
        if self.message is None:
            return
        embed = build_round_windows_embed(self.guild_id, config)
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException as exc:  # pragma: no cover - network failure
            log.warning("Failed to refresh round windows view: %s", exc)

    async def on_timeout(self) -> None:  # pragma: no cover - UI timeout
        for child in self.children:
            if isinstance(child, discord.ui.Button) or isinstance(
                child, discord.ui.Select
            ):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def open_round_modal(
        self, interaction: discord.Interaction, round_index: int
    ) -> None:
        config = self._load_config()
        definitions = _definitions_from_config(config)
        existing = (
            definitions[round_index] if 0 <= round_index < len(definitions) else None
        )
        modal = RoundWindowModal(
            self,
            round_index=round_index,
            existing=existing,
            is_new=existing is None,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Add Round", style=discord.ButtonStyle.primary)
    async def add_round(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        config = self._load_config()
        definitions = _definitions_from_config(config)
        modal = RoundWindowModal(
            self,
            round_index=len(definitions),
            existing=None,
            is_new=True,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="Remove Last Round",
        style=discord.ButtonStyle.danger,
    )
    async def remove_round(  # type: ignore[override]
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        config = self._load_config()
        definitions = _definitions_from_config(config)
        if not definitions:
            await interaction.response.send_message(
                "No round windows configured yet.", ephemeral=True
            )
            return
        removed = definitions.pop()
        if config is None:
            config = TournamentRoundWindows(
                guild_id=self.guild_id,
                rounds=[],
                updated_by=interaction.user.id,
                updated_at=utc_now_iso(),
            )
        config.rounds = list(definitions)
        config.updated_by = interaction.user.id
        config.updated_at = utc_now_iso()
        storage.save_round_windows(config)
        divisions_updated, _, cleared = apply_round_windows_to_guild(
            self.guild_id, config, clear_missing=True
        )
        ack_parts = [f"Removed round {removed.position} window."]
        if divisions_updated:
            ack_parts.append(f"Updated {divisions_updated} bracket(s).")
        if cleared:
            ack_parts.append(f"Cleared {cleared} round(s) without windows.")
        if not divisions_updated and not cleared:
            ack_parts.append("No existing brackets required changes.")
        await interaction.response.send_message(" ".join(ack_parts), ephemeral=True)
        await self.refresh()


class RoundWindowModal(discord.ui.Modal):
    def __init__(
        self,
        windows_view: RoundWindowsView,
        *,
        round_index: int,
        existing: RoundWindowDefinition | None,
        is_new: bool,
    ) -> None:
        title_action = "Add" if is_new else "Edit"
        super().__init__(title=f"{title_action} Round {round_index + 1} Window")
        self._windows_view = windows_view
        self._round_index = round_index
        self._is_new = is_new
        default_opens = _format_window_input_value(
            existing.opens_at if existing is not None else None
        )
        default_closes = _format_window_input_value(
            existing.closes_at if existing is not None else None
        )
        self.opens_input = discord.ui.TextInput(
            label="Opens (UTC)",
            placeholder="2024-05-01T18:00",
            default=default_opens,
        )
        self.closes_input = discord.ui.TextInput(
            label="Closes (UTC)",
            placeholder="2024-05-05T18:00",
            default=default_closes,
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

        config = self._windows_view._load_config()
        if config is None:
            config = TournamentRoundWindows(
                guild_id=self._windows_view.guild_id,
                rounds=[],
                updated_by=interaction.user.id,
                updated_at=utc_now_iso(),
            )
        definitions = _definitions_from_config(config)
        iso_opens = isoformat_utc(opens_at)
        iso_closes = isoformat_utc(closes_at)
        definition = RoundWindowDefinition(
            position=self._round_index + 1,
            opens_at=iso_opens,
            closes_at=iso_closes,
        )
        if self._is_new:
            definitions.append(definition)
        elif 0 <= self._round_index < len(definitions):
            definitions[self._round_index] = definition
        else:
            definitions.append(definition)

        config.rounds = list(definitions)
        config.updated_by = interaction.user.id
        config.updated_at = utc_now_iso()
        storage.save_round_windows(config)

        divisions_updated, aligned, _ = apply_round_windows_to_guild(
            self._windows_view.guild_id,
            config,
            clear_missing=False,
        )

        action = "Added" if self._is_new else "Updated"
        window_text = f"{format_display(opens_at)} — {format_display(closes_at)}"
        ack_parts = [
            f"{action} round {self._round_index + 1} window ({window_text}).",
        ]
        if divisions_updated:
            ack_parts.append(f"Applied to {divisions_updated} bracket(s).")
        elif aligned:
            ack_parts.append("Aligned existing brackets.")
        else:
            ack_parts.append("Brackets will inherit this window when seeded.")

        await interaction.response.send_message(" ".join(ack_parts), ephemeral=True)
        await self._windows_view.refresh()


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


def team_display_name(registration: TeamRegistration) -> str:
    label_source = registration.team_name or registration.user_name
    label = label_source.strip() if label_source else ""
    return label or "Unnamed Team"


def format_player_names(registration: TeamRegistration) -> str:
    names = [player.name for player in registration.players if player.name]
    return ", ".join(names) if names else "No player names on file"


def describe_round(bracket: BracketState, match: BracketMatch) -> str:
    if 0 <= match.round_index < len(bracket.rounds):
        return bracket.rounds[match.round_index].name
    return f"Round {match.round_index + 1}"


def propagate_match_result(bracket: BracketState, match: BracketMatch) -> None:
    winner_slot = match.winner_slot()
    for downstream in bracket.all_matches():
        for candidate in (downstream.competitor_one, downstream.competitor_two):
            if candidate.source_match_id != match.match_id:
                continue
            if winner_slot is None:
                candidate.seed = None
                candidate.team_id = None
                candidate.team_label = f"Winner {match.match_id}"
            else:
                candidate.seed = winner_slot.seed
                candidate.team_id = winner_slot.team_id
                candidate.team_label = winner_slot.team_label


def assign_match_winner(
    bracket: BracketState, match: BracketMatch, winner_index: int
) -> None:
    match.winner_index = winner_index
    propagate_match_result(bracket, match)


def clear_match_winner(bracket: BracketState, match: BracketMatch) -> None:
    match.winner_index = None
    propagate_match_result(bracket, match)


def replace_match_competitor(
    bracket: BracketState,
    match: BracketMatch,
    *,
    slot_index: int,
    registration: TeamRegistration,
    set_as_winner: bool = False,
) -> None:
    slot = match.competitor_one if slot_index == 0 else match.competitor_two
    slot.team_id = registration.user_id
    slot.team_label = team_display_name(registration)
    if set_as_winner:
        match.winner_index = slot_index
    propagate_match_result(bracket, match)


OpponentStatus = Literal[
    "no_bracket",
    "pending",
    "awaiting_opponent",
    "eliminated",
    "champion",
    "not_seeded",
]


@dataclass(slots=True)
class OpponentContext:
    division_id: str
    division_name: str
    registration: TeamRegistration
    status: OpponentStatus
    match: BracketMatch | None = None
    round_name: str | None = None
    opponent_slot: BracketSlot | None = None
    opponent_registration: TeamRegistration | None = None
    elimination_match: BracketMatch | None = None
    elimination_round_name: str | None = None
    elimination_opponent_slot: BracketSlot | None = None
    elimination_opponent_registration: TeamRegistration | None = None


def gather_opponent_contexts(guild_id: int, user_id: int) -> list[OpponentContext]:
    configs = {cfg.division_id: cfg for cfg in storage.list_division_configs(guild_id)}
    contexts: list[OpponentContext] = []

    for division_id, config in configs.items():
        registration = storage.get_registration(guild_id, division_id, user_id)
        if registration is None:
            continue

        registrations = storage.list_registrations(guild_id, division_id)
        registration_lookup = {reg.user_id: reg for reg in registrations}
        registration = registration_lookup.get(user_id, registration)

        bracket = storage.get_bracket(guild_id, division_id)
        division_name = config.division_name or division_id.upper()

        if bracket is None:
            contexts.append(
                OpponentContext(
                    division_id=division_id,
                    division_name=division_name,
                    registration=registration,
                    status="no_bracket",
                )
            )
            continue

        if apply_team_names(bracket, registrations):
            storage.save_bracket(bracket)

        pending_items: list[
            tuple[BracketMatch, BracketSlot, str, TeamRegistration | None]
        ] = []
        completed_items: list[
            tuple[
                BracketMatch,
                BracketSlot,
                str,
                TeamRegistration | None,
                bool,
            ]
        ] = []

        for match in bracket.all_matches():
            slots = (match.competitor_one, match.competitor_two)
            for idx, slot in enumerate(slots):
                if slot.team_id != registration.user_id:
                    continue
                opponent_slot = slots[1 - idx]
                round_name = describe_round(bracket, match)
                opponent_registration = (
                    registration_lookup.get(opponent_slot.team_id)
                    if opponent_slot.team_id is not None
                    else None
                )
                user_won = match.winner_index == idx
                if match.winner_index is None:
                    pending_items.append(
                        (match, opponent_slot, round_name, opponent_registration)
                    )
                else:
                    completed_items.append(
                        (
                            match,
                            opponent_slot,
                            round_name,
                            opponent_registration,
                            user_won,
                        )
                    )
                break

        if pending_items:
            pending_items.sort(key=lambda item: (item[0].round_index, item[0].match_id))
            next_match, opponent_slot, round_name, opponent_registration = (
                pending_items[0]
            )
            status: OpponentStatus
            if opponent_slot.team_id is not None:
                status = "pending"
            else:
                status = "awaiting_opponent"
            contexts.append(
                OpponentContext(
                    division_id=division_id,
                    division_name=division_name,
                    registration=registration,
                    status=status,
                    match=next_match,
                    round_name=round_name,
                    opponent_slot=opponent_slot,
                    opponent_registration=opponent_registration,
                )
            )
            continue

        if completed_items:
            completed_items.sort(
                key=lambda item: (item[0].round_index, item[0].match_id)
            )
            last_match, opponent_slot, round_name, opponent_registration, user_won = (
                completed_items[-1]
            )

            final_round = bracket.rounds[-1] if bracket.rounds else None
            if final_round and final_round.matches:
                final_match = final_round.matches[-1]
            else:
                final_match = None
            winner_slot = final_match.winner_slot() if final_match else None
            if winner_slot and winner_slot.team_id == registration.user_id:
                contexts.append(
                    OpponentContext(
                        division_id=division_id,
                        division_name=division_name,
                        registration=registration,
                        status="champion",
                        match=final_match,
                        round_name=describe_round(bracket, final_match)
                        if final_match is not None
                        else round_name,
                    )
                )
                continue

            if user_won:
                contexts.append(
                    OpponentContext(
                        division_id=division_id,
                        division_name=division_name,
                        registration=registration,
                        status="awaiting_opponent",
                    )
                )
                continue

            contexts.append(
                OpponentContext(
                    division_id=division_id,
                    division_name=division_name,
                    registration=registration,
                    status="eliminated",
                    elimination_match=last_match,
                    elimination_round_name=round_name,
                    elimination_opponent_slot=opponent_slot,
                    elimination_opponent_registration=opponent_registration,
                )
            )
            continue

        contexts.append(
            OpponentContext(
                division_id=division_id,
                division_name=division_name,
                registration=registration,
                status="not_seeded",
            )
        )

    return contexts


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


BRACKET_EMBED_FIRST_CHUNK_LIMIT: Final[int] = 3200
BRACKET_EMBED_CONTINUATION_LIMIT: Final[int] = 3900


def bracket_graph_requires_chunking(graph: str) -> bool:
    """Return True when the rendered bracket exceeds the safe embed size."""

    if not graph:
        return False
    return len(graph) > BRACKET_EMBED_FIRST_CHUNK_LIMIT


def _chunk_bracket_graph(graph: str) -> list[str]:
    """Split a rendered bracket into safe chunks for embed descriptions."""

    if not graph:
        return [""]

    chunks: list[str] = []
    current = ""
    limit = BRACKET_EMBED_FIRST_CHUNK_LIMIT
    for line in graph.splitlines():
        addition = ("\n" if current else "") + line
        if len(current) + len(addition) > limit:
            chunks.append(current)
            current = line
            limit = BRACKET_EMBED_CONTINUATION_LIMIT
        else:
            current += addition
    if current or not chunks:
        chunks.append(current)
    return chunks


def build_bracket_embed(
    bracket: BracketState,
    *,
    title: str,
    requested_by: discord.abc.User | None,
    summary_note: str | None = None,
    shrink_completed: bool = False,
    graph: str | None = None,
) -> discord.Embed:
    if graph is None:
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


async def send_bracket_embed_chunked_to_channel(
    channel: Messageable,
    bracket: BracketState,
    *,
    title: str,
    requested_by: discord.abc.User | None,
    summary_note: str | None = None,
    shrink_completed: bool = False,
    graph: str | None = None,
) -> int:
    """Post the bracket to a channel, splitting into multiple embeds if too large.

    Discord imposes a ~6000 character total size per-embed and 4096 character
    description limit. Larger brackets (e.g., Round of 64) can exceed this.

    This helper renders the bracket once (or uses the provided ``graph``), then
    sends one or more embeds where the first includes the Summary/Note fields
    and subsequent messages contain continued graph content only.

    Returns the number of messages posted.
    """
    if graph is None:
        graph = render_bracket(bracket, shrink_completed=shrink_completed)

    summary_value = bracket_summary(bracket)

    chunks = _chunk_bracket_graph(graph)

    messages_posted = 0
    for index, text in enumerate(chunks, start=1):
        embed = discord.Embed(
            title=title + (f" (cont. {index})" if index > 1 else ""),
            description=f"```\n{text}\n```" if text else "Bracket is empty",
            color=discord.Color.blurple(),
            timestamp=datetime.now(UTC),
        )
        if index == 1:
            if summary_value:
                embed.add_field(name="Summary", value=summary_value, inline=False)
            if summary_note:
                embed.add_field(name="Note", value=summary_note, inline=False)
        if requested_by is not None:
            embed.set_footer(text=f"Updated by {requested_by}")

        await channel.send(embed=embed)
        messages_posted += 1

    return messages_posted


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


async def resolve_captain_role(
    guild: discord.Guild,
) -> tuple[discord.Role | None, str | None]:
    if TOURNAMENT_CAPTAIN_ROLE_ID is None:
        return None, (
            "Captain role is not configured. Ask a tournament admin to set the "
            "captain role ID."
        )

    role = guild.get_role(TOURNAMENT_CAPTAIN_ROLE_ID)
    if role is not None:
        return role, None

    try:
        roles = await guild.fetch_roles()
    except discord.Forbidden:
        log.warning(
            "Forbidden when fetching roles in guild %s to resolve captain role",
            guild.id,
        )
        return None, (
            "Bot lacks permission to view roles. Grant **Manage Roles** so the "
            "bot can manage captain roles."
        )
    except discord.HTTPException as exc:
        log.warning("HTTPException fetching roles in guild %s: %s", guild.id, exc)
        return None, "Discord API error prevented resolving the captain role."

    for role in roles:
        if role.id == TOURNAMENT_CAPTAIN_ROLE_ID:
            return role, None

    return None, (
        f"Captain role ID {TOURNAMENT_CAPTAIN_ROLE_ID} was not found in this "
        "guild. Update the configured value."
    )


def division_role_name(config: TournamentConfig) -> str:
    base = config.division_name.strip() if config.division_name else ""
    if not base:
        base = config.division_id.upper()
    return f"{base} Division"


async def ensure_division_role(
    guild: discord.Guild, config: TournamentConfig
) -> tuple[discord.Role | None, str | None]:
    desired_name = division_role_name(config)
    stored_role_id = getattr(config, "division_role_id", None)

    def update_config(role: discord.Role) -> None:
        if getattr(config, "division_role_id", None) == role.id:
            return
        config.division_role_id = role.id
        try:
            storage.save_config(config)
        except RuntimeError:  # pragma: no cover - storage unavailable
            log.debug(
                "Storage unavailable while persisting division role id for %s",
                desired_name,
            )

    if stored_role_id is not None:
        role = guild.get_role(stored_role_id)
        if role is not None:
            return role, None

    try:
        roles = await guild.fetch_roles()
    except discord.Forbidden:
        log.warning(
            "Forbidden when fetching roles in guild %s to resolve division role",
            guild.id,
        )
        return None, (
            "Bot lacks permission to view roles. Grant **Manage Roles** so the "
            "bot can manage division roles."
        )
    except discord.HTTPException as exc:
        log.warning("HTTPException fetching roles in guild %s: %s", guild.id, exc)
        return None, "Discord API error prevented resolving the division role."

    for role in roles:
        if stored_role_id is not None and role.id == stored_role_id:
            update_config(role)
            return role, None

    lower_name = desired_name.lower()
    for role in roles:
        if role.name.lower() == lower_name:
            update_config(role)
            return role, None

    try:
        new_role = await guild.create_role(
            name=desired_name,
            mentionable=True,
            reason=f"Ensure division role for {desired_name}",
        )
    except discord.Forbidden:
        log.warning(
            "Forbidden when creating division role '%s' in guild %s",
            desired_name,
            guild.id,
        )
        return None, (
            "Bot lacks permission to create division roles. Grant **Manage Roles** "
            "so the bot can create them."
        )
    except discord.HTTPException as exc:
        log.warning(
            "HTTPException creating division role '%s' in guild %s: %s",
            desired_name,
            guild.id,
            exc,
        )
        return None, "Discord API error prevented creating the division role."

    update_config(new_role)
    log.info(
        "Created division role '%s' (id=%s) for guild %s",
        new_role.name,
        new_role.id,
        guild.id,
    )
    return new_role, None


async def fetch_guild_member(
    guild: discord.Guild, user_id: int
) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        log.warning("Member %s not found in guild %s", user_id, guild.id)
    except discord.Forbidden:
        log.warning(
            "Forbidden fetching member %s in guild %s for captain role sync",
            user_id,
            guild.id,
        )
    except discord.HTTPException as exc:
        log.warning(
            "HTTPException fetching member %s in guild %s: %s",
            user_id,
            guild.id,
            exc,
        )
    return None


def categorize_captains_for_division(
    bracket: BracketState | None, registrations: Sequence[TeamRegistration]
) -> tuple[set[int], set[int]]:
    active: set[int] = {registration.user_id for registration in registrations}
    eliminated: set[int] = set()

    if bracket is None:
        return active, eliminated

    registration_ids = {registration.user_id for registration in registrations}
    for match in bracket.all_matches():
        winner_index = match.winner_index
        if winner_index is None:
            continue
        for idx, slot in enumerate((match.competitor_one, match.competitor_two)):
            team_id = slot.team_id
            if team_id is None or team_id not in registration_ids:
                continue
            if winner_index != idx:
                eliminated.add(team_id)

    active -= eliminated
    return active, eliminated


def gather_captain_role_targets(
    guild_id: int,
) -> tuple[set[int], set[int]]:
    active_ids: set[int] = set()
    eliminated_ids: set[int] = set()

    for config in storage.list_division_configs(guild_id):
        registrations = storage.list_registrations(guild_id, config.division_id)
        if not registrations:
            continue

        bracket = storage.get_bracket(guild_id, config.division_id)
        if bracket is not None and apply_team_names(bracket, registrations):
            storage.save_bracket(bracket)

        active, eliminated = categorize_captains_for_division(bracket, registrations)
        active_ids.update(active)
        eliminated_ids.update(eliminated)

    return active_ids, eliminated_ids


async def _add_member_role(
    member: discord.Member,
    role: discord.Role,
    *,
    reason: str,
    label: str,
) -> str | None:
    if role in getattr(member, "roles", []):
        return None
    try:
        await member.add_roles(role, reason=reason)
    except discord.Forbidden:
        log.warning(
            "Forbidden when adding captain role to %s (guild %s)",
            member,
            member.guild.id,
        )
        return f"Bot lacks permission to assign the {label}."
    except discord.HTTPException as exc:
        log.warning(
            "HTTPException adding role to %s in guild %s: %s",
            member,
            member.guild.id,
            exc,
        )
        return f"Discord API error prevented assigning the {label}."
    return None


async def _remove_member_role(
    member: discord.Member,
    role: discord.Role,
    *,
    reason: str,
    label: str,
) -> str | None:
    if role not in getattr(member, "roles", []):
        return None
    try:
        await member.remove_roles(role, reason=reason)
    except discord.Forbidden:
        log.warning(
            "Forbidden when removing captain role from %s (guild %s)",
            member,
            member.guild.id,
        )
        return f"Bot lacks permission to remove the {label}."
    except discord.HTTPException as exc:
        log.warning(
            "HTTPException removing role from %s in guild %s: %s",
            member,
            member.guild.id,
            exc,
        )
        return f"Discord API error prevented removing the {label}."
    return None


async def add_captain_role(
    member: discord.Member, role: discord.Role, *, reason: str
) -> str | None:
    return await _add_member_role(member, role, reason=reason, label="captain role")


async def remove_captain_role(
    member: discord.Member, role: discord.Role, *, reason: str
) -> str | None:
    return await _remove_member_role(member, role, reason=reason, label="captain role")


async def add_division_role(
    member: discord.Member, role: discord.Role, *, reason: str
) -> str | None:
    return await _add_member_role(member, role, reason=reason, label="division role")


async def remove_division_role(
    member: discord.Member, role: discord.Role, *, reason: str
) -> str | None:
    return await _remove_member_role(member, role, reason=reason, label="division role")


# ---------- Slash Commands ----------
@require_admin_or_tournament_role()
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
    if isinstance(error, app_errors.MissingPermissions) or isinstance(
        error, app_errors.CheckFailure
    ):
        await interaction.response.send_message(
            "You need administrator or tournament-admin role to run this command.",
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
    name="registerplayer", description="Register for a tournament division"
)
async def register_player_command(  # pragma: no cover - Discord slash command wiring
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

    role_notes: list[str] = []
    role, role_error = await resolve_captain_role(guild)
    if role is None:
        if role_error is not None:
            role_notes.append(role_error)
    else:
        assignment_error = await add_captain_role(
            interaction.user,
            role,
            reason="Registered for tournament",
        )
        if assignment_error is not None:
            role_notes.append(assignment_error)

    division_role, division_role_error = await ensure_division_role(guild, config)
    if division_role is None:
        if division_role_error is not None:
            role_notes.append(division_role_error)
    else:
        division_assign_error = await add_division_role(
            interaction.user,
            division_role,
            reason=f"Registered for {config.division_name} division",
        )
        if division_assign_error is not None:
            role_notes.append(division_assign_error)

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

    if role_notes:
        confirmation = f"{confirmation}\n" + "\n".join(role_notes)

    await interaction.followup.send(confirmation, ephemeral=True)


@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
)
@require_admin_or_tournament_role()
@tournament_command(
    name="assignrole",
    description="Sync division roles for all participants in a bracket",
)
async def assign_role_command(  # pragma: no cover - Discord slash command wiring
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
            "That division is not configured. Please ask an admin to run /setup.",
            ephemeral=True,
        )
        return

    registrations = storage.list_registrations(guild.id, division_id)
    if not registrations:
        await interaction.response.send_message(
            "No registrations found for that division.", ephemeral=True
        )
        return

    bracket = storage.get_bracket(guild.id, division_id)
    if bracket is not None and apply_team_names(bracket, registrations):
        storage.save_bracket(bracket)

    active_ids, eliminated_ids = categorize_captains_for_division(
        bracket, registrations
    )

    division_role, division_note = await ensure_division_role(guild, config)
    if division_role is None:
        await interaction.response.send_message(
            division_note or "Unable to resolve the division role.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    assigned = 0
    removed = 0
    missing_assign: set[int] = set()
    missing_remove: set[int] = set()
    error_notes: list[str] = []

    for captain_id in sorted(active_ids):
        member = await fetch_guild_member(guild, captain_id)
        if member is None:
            missing_assign.add(captain_id)
            continue
        had_role = division_role in getattr(member, "roles", [])
        assign_error = await add_division_role(
            member,
            division_role,
            reason=f"Division role sync for {config.division_name}",
        )
        if assign_error is not None:
            error_notes.append(assign_error)
            continue
        if not had_role:
            assigned += 1

    for captain_id in sorted(eliminated_ids):
        member = await fetch_guild_member(guild, captain_id)
        if member is None:
            missing_remove.add(captain_id)
            continue
        if division_role not in getattr(member, "roles", []):
            continue
        remove_error = await remove_division_role(
            member,
            division_role,
            reason=f"Division role sync for {config.division_name}",
        )
        if remove_error is not None:
            error_notes.append(remove_error)
            continue
        removed += 1

    lines = [
        (
            f"Synced {division_role.name} for {config.division_name} "
            f"({config.division_id})."
        )
    ]
    lines.append(f"Assigned role to {assigned} member(s).")
    lines.append(f"Removed role from {removed} member(s).")

    if missing_assign:
        mentions = " ".join(f"<@{user_id}>" for user_id in sorted(missing_assign))
        lines.append("Could not locate these captains to assign the role: " + mentions)
    if missing_remove:
        mentions = " ".join(f"<@{user_id}>" for user_id in sorted(missing_remove))
        lines.append(
            "Could not locate these eliminated captains to remove the role: " + mentions
        )
    if error_notes:
        unique_notes = list(dict.fromkeys(error_notes))
        lines.extend(unique_notes)

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@app_commands.describe(
    division="Optional tournament division identifier to pre-select",
)
@require_admin_or_tournament_role()
@tournament_command(
    name="adjust-bracket",
    description="Interactively adjust match winners or competitors in the bracket",
)
async def adjust_bracket_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str | None = None,
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

    initial_division: str | None = None
    if division is not None:
        try:
            initial_division = normalize_division_value(division)
        except InvalidValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

    view = BracketAdjustView(
        guild.id,
        interaction.user.id,
        initial_division=initial_division,
    )

    if view.division_id is None:
        await interaction.response.send_message(
            "No tournament divisions are configured. Use /setup to add one first.",
            ephemeral=True,
        )
        return

    embed = view.build_embed()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:  # pragma: no cover - defensive
        view.message = None


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


@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
)
@tournament_command(
    name="create-bracket", description="Seed registered teams into a bracket"
)
@require_admin_or_tournament_role()
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
    round_windows = storage.get_round_windows(guild.id)
    if round_windows is not None:
        apply_round_windows_to_bracket(bracket, round_windows, clear_missing=False)
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
        title = f"{config.division_name} | Tournament Bracket Created"
        graph = render_bracket(bracket)
        if bracket_graph_requires_chunking(graph):
            try:
                posted = await send_bracket_embed_chunked_to_channel(
                    channel,
                    bracket,
                    title=title,
                    requested_by=interaction.user,
                    summary_note=note,
                    graph=graph,
                )
                log.info(
                    "Posted bracket announcement in %s chunk(s) due to size", posted
                )
            except discord.HTTPException as exc:  # pragma: no cover - network failure
                log.warning("Failed to send chunked bracket announcement: %s", exc)
        else:
            try:
                embed = build_bracket_embed(
                    bracket,
                    title=title,
                    requested_by=interaction.user,
                    summary_note=note,
                    graph=graph,
                )
                await channel.send(embed=embed)
            except discord.HTTPException as exc:  # pragma: no cover - network failure
                # Fallback: break into multiple embeds when too large or other errors
                log.warning("Failed to send bracket announcement: %s", exc)
                try:
                    posted = await send_bracket_embed_chunked_to_channel(
                        channel,
                        bracket,
                        title=title,
                        requested_by=interaction.user,
                        summary_note=note,
                        graph=graph,
                    )
                    log.info(
                        "Posted bracket announcement in %s chunk(s) after failure",
                        posted,
                    )
                except discord.HTTPException as exc2:  # pragma: no cover - defensive
                    log.warning("Failed to send chunked bracket announcement: %s", exc2)
    else:
        log.debug("Skipping bracket announcement; channel not messageable")


@create_bracket_command.error
async def create_bracket_error_handler(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_errors.MissingPermissions) or isinstance(
        error, app_errors.CheckFailure
    ):
        await send_ephemeral(
            interaction,
            "You need administrator or tournament-admin role to run this command.",
        )
        return
    log.exception("Unhandled create-bracket error: %s", error)
    await send_ephemeral(
        interaction,
        "An unexpected error occurred while creating the bracket.",
    )


@tournament_command(
    name="setwindows",
    description="Configure match windows for tournament rounds",
)
@require_admin_or_tournament_role()
async def set_round_windows_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
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

    config = storage.get_round_windows(guild.id)
    if config is not None:
        config.ensure_sequential_positions()

    embed = build_round_windows_embed(guild.id, config)
    view = RoundWindowsView(guild.id, interaction.user.id, initial_config=config)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:  # pragma: no cover - defensive
        view.message = None


@set_round_windows_command.error
async def set_round_windows_error_handler(  # pragma: no cover - Discord wiring
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_errors.MissingPermissions) or isinstance(
        error, app_errors.CheckFailure
    ):
        await send_ephemeral(
            interaction,
            "You need administrator or tournament-admin role to run this command.",
        )
        return
    log.exception("Unhandled setwindows error: %s", error)
    await send_ephemeral(
        interaction,
        "An unexpected error occurred while updating round windows.",
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

    graph = render_bracket(bracket)
    captain_lines = team_captain_lines(bracket, registrations)
    captain_chunks: list[str] = []
    if captain_lines:
        current: list[str] = []
        length = 0
        for line in captain_lines:
            addition = len(line) + (1 if current else 0)
            if current and length + addition > 1024:
                captain_chunks.append("\n".join(current))
                current = [line]
                length = len(line)
            else:
                current.append(line)
                length += addition
        if current:
            captain_chunks.append("\n".join(current))

    async def _send_chunked_response() -> None:
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.HTTPException as exc:  # pragma: no cover - defensive
                log.warning("Failed to defer showbracket interaction: %s", exc)
        channel = interaction.channel
        if not isinstance(channel, Messageable):
            await send_ephemeral(
                interaction,
                "Unable to display the bracket in this location.",
            )
            return
        try:
            posted = await send_bracket_embed_chunked_to_channel(
                channel,
                bracket,
                title=f"{config.division_name} | Current Tournament Bracket",
                requested_by=interaction.user,
                summary_note=summary_note,
                graph=graph,
            )
        except discord.HTTPException as exc:  # pragma: no cover - defensive
            log.warning("Failed to send chunked showbracket response: %s", exc)
            await send_ephemeral(
                interaction,
                "Failed to display the bracket. Please try again shortly.",
            )
            return

        if captain_chunks:
            captain_embed = discord.Embed(
                title=f"{config.division_name} | Teams & Captains",
                color=discord.Color.blurple(),
                timestamp=datetime.now(UTC),
            )
            for idx, chunk in enumerate(captain_chunks):
                field_name = (
                    "Teams & Captains" if idx == 0 else "Teams & Captains (cont.)"
                )
                captain_embed.add_field(name=field_name, value=chunk, inline=False)
            try:
                await channel.send(embed=captain_embed)
            except discord.HTTPException as exc:  # pragma: no cover - defensive
                log.warning("Failed to send captain listing: %s", exc)

        await send_ephemeral(
            interaction,
            f"Bracket posted in {posted} message(s) due to size.",
        )

    if bracket_graph_requires_chunking(graph):
        await _send_chunked_response()
        return

    embed = build_bracket_embed(
        bracket,
        title=f"{config.division_name} | Current Tournament Bracket",
        requested_by=interaction.user,
        summary_note=summary_note,
        graph=graph,
    )

    for idx, chunk in enumerate(captain_chunks):
        field_name = "Teams & Captains" if idx == 0 else "Teams & Captains (cont.)"
        embed.add_field(name=field_name, value=chunk, inline=False)

    try:
        await interaction.response.send_message(embed=embed)
    except discord.HTTPException as exc:  # pragma: no cover - defensive
        log.warning("Failed to send showbracket response: %s", exc)
        await _send_chunked_response()


@show_bracket_command.error
async def show_bracket_error_handler(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    log.exception("Unhandled showbracket error: %s", error)
    await send_ephemeral(
        interaction,
        "An unexpected error occurred while showing the bracket.",
    )


@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
)
@tournament_command(
    name="broadcast-bracket", description="Post the current tournament bracket"
)
@require_admin_or_tournament_role()
async def broadcast_bracket_command(  # pragma: no cover - Discord slash command wiring
    interaction: discord.Interaction,
    division: str,
) -> None:
    try:
        guild = ensure_guild(interaction)
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    channel = interaction.channel
    if not isinstance(channel, Messageable):
        await interaction.response.send_message(
            "Unable to post the bracket here. Try running this command in a text channel.",
            ephemeral=True,
        )
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
            "That division is not configured. Please add it via /setup before broadcasting.",
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

    deferred = False
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=True)
            deferred = True
        except discord.HTTPException as exc:  # pragma: no cover - defensive
            log.debug("Failed to defer broadcast-bracket interaction: %s", exc)

    title = f"{config.division_name} | Tournament Bracket"
    graph = render_bracket(bracket)
    if bracket_graph_requires_chunking(graph):
        try:
            posted_messages = await send_bracket_embed_chunked_to_channel(
                channel,
                bracket,
                title=title,
                requested_by=interaction.user,
                summary_note=summary_note,
                graph=graph,
            )
        except discord.HTTPException as exc:  # pragma: no cover - defensive
            log.warning("Failed to send chunked broadcast bracket: %s", exc)
            if deferred:
                await interaction.followup.send(
                    "Unable to post the bracket due to Discord API errors.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Unable to post the bracket due to Discord API errors.",
                    ephemeral=True,
                )
            return
    else:
        try:
            embed = build_bracket_embed(
                bracket,
                title=title,
                requested_by=interaction.user,
                summary_note=summary_note,
                graph=graph,
            )
            await channel.send(embed=embed)
            posted_messages = 1
        except discord.HTTPException as exc:
            log.warning("Failed to send broadcast bracket embed: %s", exc)
            try:
                posted_messages = await send_bracket_embed_chunked_to_channel(
                    channel,
                    bracket,
                    title=title,
                    requested_by=interaction.user,
                    summary_note=summary_note,
                    graph=graph,
                )
            except discord.HTTPException as exc2:  # pragma: no cover - defensive
                log.warning("Failed to send chunked broadcast bracket: %s", exc2)
                if deferred:
                    await interaction.followup.send(
                        "Unable to post the bracket due to Discord API errors.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "Unable to post the bracket due to Discord API errors.",
                        ephemeral=True,
                    )
                return

    ack_message = "Posted the bracket to this channel."
    if posted_messages > 1:
        ack_message += f" Sent in {posted_messages} messages due to size."

    if deferred:
        await interaction.followup.send(ack_message, ephemeral=True)
    else:
        await interaction.response.send_message(ack_message, ephemeral=True)


@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
    winner_captain="Team captain (Discord user) for the winning team",
)
@tournament_command(
    name="select-round-winner",
    description="Record the winner for a bracket match",
)
@require_admin_or_tournament_role()
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

    config = storage.get_config(guild.id, division_id)
    if config is None:
        await send_ephemeral(
            interaction,
            "That division is not configured. Please ask an admin to run /setup.",
        )
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
    opponent_slot_pre = (
        match_obj.competitor_two if selected_index == 0 else match_obj.competitor_one
    )
    opponent_registration_pre = registration_lookup.get(opponent_slot_pre.team_id)
    match_identifier = match_obj.match_id
    previous_winner = match_obj.winner_index

    try:
        round_obj = bracket.rounds[match_obj.round_index]
    except IndexError:  # pragma: no cover - defensive
        await send_ephemeral(
            interaction,
            "Unable to determine the round for this match. Please refresh the bracket and try again.",
        )
        return

    opens_raw = round_obj.window_opens_at
    closes_raw = round_obj.window_closes_at
    if opens_raw is None or closes_raw is None:
        await send_ephemeral(
            interaction,
            (
                f"Round '{round_obj.name}' does not have a match window configured. "
                "Ask an admin to run /setwindows before recording winners."
            ),
        )
        return

    opens_at = parse_round_window_timestamp(opens_raw)
    closes_at = parse_round_window_timestamp(closes_raw)
    now = datetime.now(UTC)
    if now < opens_at:
        await send_ephemeral(
            interaction,
            (
                f"Match window for {round_obj.name} opens {format_display(opens_at)}. "
                "You cannot record this winner yet."
            ),
        )
        return
    if now > closes_at:
        await send_ephemeral(
            interaction,
            (
                f"Match window for {round_obj.name} closed {format_display(closes_at)}. "
                "Extend it with /setwindows before recording a winner."
            ),
        )
        return

    try:
        set_match_winner(bracket, match_identifier, selected_index + 1)
    except ValueError as exc:
        await send_ephemeral(interaction, str(exc))
        return

    storage.save_bracket(bracket)
    winner_slot_obj = match_obj.winner_slot()
    selected_registration = registration_lookup.get(selected_slot.team_id)

    loser_id: int | None = None
    if opponent_registration_pre is not None:
        loser_id = opponent_registration_pre.user_id
    elif opponent_slot_pre.team_id is not None:
        loser_id = opponent_slot_pre.team_id

    loser_member: discord.Member | None = None
    if loser_id is not None and loser_id != winner_captain.id:
        loser_member = await fetch_guild_member(guild, loser_id)

    role_notes: list[str] = []
    missing_role_updates: dict[int, set[str]] = {}

    def record_missing(member_id: int, label: str) -> None:
        missing_role_updates.setdefault(member_id, set()).add(label)

    role, role_error = await resolve_captain_role(guild)
    if role is None:
        if role_error is not None:
            role_notes.append(role_error)
    else:
        add_error = await add_captain_role(
            winner_captain,
            role,
            reason="Advanced in tournament bracket",
        )
        if add_error is not None:
            role_notes.append(add_error)
        if loser_member is not None:
            remove_error = await remove_captain_role(
                loser_member,
                role,
                reason="Eliminated from tournament bracket",
            )
            if remove_error is not None:
                role_notes.append(remove_error)
        elif loser_id is not None and loser_id != winner_captain.id:
            record_missing(loser_id, "captain role")

    division_role, division_note = await ensure_division_role(guild, config)
    if division_role is None:
        if division_note is not None:
            role_notes.append(division_note)
    else:
        division_add_error = await add_division_role(
            winner_captain,
            division_role,
            reason=f"Advanced in {config.division_name} division",
        )
        if division_add_error is not None:
            role_notes.append(division_add_error)

        if loser_member is not None:
            division_remove_error = await remove_division_role(
                loser_member,
                division_role,
                reason=f"Eliminated from {config.division_name} division",
            )
            if division_remove_error is not None:
                role_notes.append(division_remove_error)
        elif loser_id is not None and loser_id != winner_captain.id:
            record_missing(loser_id, "division role")

    for member_id, labels in missing_role_updates.items():
        sorted_labels = sorted(labels)
        if not sorted_labels:
            continue
        if len(sorted_labels) == 1:
            label_text = sorted_labels[0]
        else:
            label_text = " and ".join(sorted_labels)
        role_notes.append(
            f"Unable to locate <@{member_id}> to update their {label_text}."
        )

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

    if role_notes:
        ack_message += "\n" + "\n".join(role_notes)

    await send_ephemeral(interaction, ack_message)

    channel = interaction.channel
    if isinstance(channel, Messageable):
        winner_label = (
            winner_slot_obj.display() if winner_slot_obj is not None else "Winner"
        )
        winner_captain_mention = (
            f"<@{selected_registration.user_id}>"
            if selected_registration is not None
            else winner_captain.mention
        )
        if opponent_registration_pre is not None:
            loser_label = team_display_name(opponent_registration_pre)
            loser_captain = f"<@{opponent_registration_pre.user_id}>"
        elif opponent_slot_pre.team_id is not None:
            loser_label = opponent_slot_pre.display()
            loser_captain = f"<@{opponent_slot_pre.team_id}>"
        else:
            loser_label = opponent_slot_pre.display()
            loser_captain = None

        next_match: BracketMatch | None = None
        next_opponent_slot: BracketSlot | None = None
        for downstream in bracket.all_matches():
            for candidate_slot in (
                downstream.competitor_one,
                downstream.competitor_two,
            ):
                if candidate_slot.source_match_id == match_identifier:
                    next_match = downstream
                    next_opponent_slot = (
                        downstream.competitor_two
                        if candidate_slot is downstream.competitor_one
                        else downstream.competitor_one
                    )
                    break
            if next_match is not None:
                break

        lines = [
            f"Winner recorded for {match_identifier}: {winner_label} defeated {loser_label}.",
            f"Captain: {winner_captain_mention}",
        ]
        if loser_captain is not None:
            lines.append(f"Opponent captain: {loser_captain}")

        if next_match is not None:
            next_round_name = describe_round(bracket, next_match)
            if next_opponent_slot is not None:
                if next_opponent_slot.team_id is not None:
                    next_reg = registration_lookup.get(next_opponent_slot.team_id)
                    if next_reg is not None:
                        opponent_text = (
                            f"{team_display_name(next_reg)} (<@{next_reg.user_id}>)"
                        )
                    else:
                        opponent_text = next_opponent_slot.display()
                else:
                    opponent_text = next_opponent_slot.display()
            else:
                opponent_text = "TBD"
            lines.append(
                f"Next match: {next_round_name} ({next_match.match_id}) vs {opponent_text}."
            )

        if champion:
            lines.append(f"Current champion: {champion}.")

        channel_message = "\n".join(lines)
        try:
            await channel.send(channel_message)
        except discord.HTTPException as exc:  # pragma: no cover - network failure
            log.warning("Failed to post bracket update note: %s", exc)
    else:
        log.debug("Skipping bracket update; channel not messageable")


@select_round_winner_command.error
async def select_round_winner_error_handler(  # pragma: no cover - Discord wiring
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_errors.MissingPermissions) or isinstance(
        error, app_errors.CheckFailure
    ):
        await send_ephemeral(
            interaction,
            "You need administrator or tournament-admin role to run this command.",
        )
        return
    log.exception("Unhandled select-round-winner error: %s", error)
    await send_ephemeral(
        interaction,
        "An unexpected error occurred while recording the winner.",
    )


@tournament_command(
    name="setcaptains",
    description="Sync the captain role with active tournament registrations",
)
@require_admin_or_tournament_role()
async def set_captains_command(  # pragma: no cover - Discord wiring
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

    await interaction.response.defer(ephemeral=True, thinking=True)

    role, role_error = await resolve_captain_role(guild)
    if role is None:
        await interaction.followup.send(
            role_error or "Captain role not configured.", ephemeral=True
        )
        return

    active_ids, eliminated_ids = gather_captain_role_targets(guild.id)

    try:
        await guild.chunk()
    except discord.HTTPException as exc:
        log.warning("Failed to chunk guild %s before syncing roles: %s", guild.id, exc)

    current_members = list(role.members)
    current_ids = {member.id for member in current_members}
    members_by_id: dict[int, discord.Member] = {
        member.id: member for member in current_members
    }

    to_remove_ids = current_ids - active_ids

    added = 0
    removed = 0
    missing_add: list[int] = []
    missing_remove: list[int] = []
    assign_errors = 0
    remove_errors = 0

    for user_id in sorted(active_ids):
        if user_id in current_ids:
            continue
        member = members_by_id.get(user_id) or await fetch_guild_member(guild, user_id)
        if member is None:
            missing_add.append(user_id)
            continue
        error = await add_captain_role(
            member,
            role,
            reason="Synced via /setcaptains",
        )
        if error is None:
            added += 1
            members_by_id[user_id] = member
        else:
            assign_errors += 1

    for user_id in sorted(to_remove_ids):
        member = members_by_id.get(user_id) or await fetch_guild_member(guild, user_id)
        if member is None:
            missing_remove.append(user_id)
            continue
        error = await remove_captain_role(
            member,
            role,
            reason="Synced via /setcaptains",
        )
        if error is None:
            removed += 1
        else:
            remove_errors += 1

    summary_lines = [
        f"Captain role synced. Active captains: {len(active_ids)}",
        f"Eliminated captains detected: {len(eliminated_ids)}",
        f"Added role to {added} member(s).",
        f"Removed role from {removed} member(s).",
    ]

    if missing_add:
        summary_lines.append(
            f"Unable to locate {len(missing_add)} member(s) to assign the role."
        )
    if missing_remove:
        summary_lines.append(
            f"Unable to locate {len(missing_remove)} member(s) to remove the role."
        )
    if assign_errors:
        summary_lines.append(
            f"Encountered {assign_errors} error(s) assigning the role; check logs."
        )
    if remove_errors:
        summary_lines.append(
            f"Encountered {remove_errors} error(s) removing the role; check logs."
        )

    await interaction.followup.send("\n".join(summary_lines), ephemeral=True)


@tournament_command(
    name="myopponent", description="Show your current tournament opponent"
)
async def my_opponent_command(  # pragma: no cover - Discord slash command wiring
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

    user_id = getattr(interaction.user, "id", None)
    if user_id is None:
        await interaction.response.send_message(
            "Unable to determine your Discord account.", ephemeral=True
        )
        return

    contexts = gather_opponent_contexts(guild.id, user_id)
    if not contexts:
        await interaction.response.send_message(
            "You are not registered as a team captain in any tournament divisions.",
            ephemeral=True,
        )
        return

    blocks: list[str] = []
    for context in contexts:
        header = f"{context.division_name} ({context.division_id})"
        if context.status == "no_bracket":
            blocks.append(f"{header}: No bracket has been created yet.")
            continue

        if context.status == "pending" and context.match is not None:
            opponent_registration = context.opponent_registration
            if opponent_registration is not None:
                opponent_display = f"<@{opponent_registration.user_id}> ({opponent_registration.user_name})"
                opponent_players = format_player_names(opponent_registration)
                opponent_team = team_display_name(opponent_registration)
            else:
                opponent_display = (
                    context.opponent_slot.display()
                    if context.opponent_slot is not None
                    else "Unknown opponent"
                )
                opponent_players = "No roster on file"
                opponent_team = opponent_display

            match_label = (
                f"{context.round_name or 'Upcoming round'} — {context.match.match_id}"
            )
            block_lines = [
                header,
                f"- Match: {match_label}",
                f"- Opponent: {opponent_display} | Team: {opponent_team}",
                f"- Opponent players: {opponent_players}",
            ]
            blocks.append("\n".join(block_lines))
            continue

        if context.status == "awaiting_opponent":
            if context.match is not None and context.opponent_slot is not None:
                waiting_label = context.opponent_slot.display()
                blocks.append(
                    f"{header}: Waiting for {waiting_label} to be decided "
                    f"({context.match.match_id})."
                )
            else:
                blocks.append(
                    f"{header}: Awaiting bracket updates for your next opponent."
                )
            continue

        if context.status == "eliminated":
            if context.elimination_opponent_registration is not None:
                opponent_label = context.elimination_opponent_registration.user_name
            elif context.elimination_opponent_slot is not None:
                opponent_label = context.elimination_opponent_slot.display()
            else:
                opponent_label = "another team"
            match_id = (
                context.elimination_match.match_id
                if context.elimination_match is not None
                else "a recorded match"
            )
            round_name = context.elimination_round_name or "Previous round"
            elimination_message = (
                f"{header}: Eliminated in {round_name} ({match_id}) by "
                f"{opponent_label}."
            )
            blocks.append(elimination_message)
            continue

        if context.status == "champion":
            match_id = (
                context.match.match_id if context.match is not None else "Final match"
            )
            round_name = context.round_name or "Final"
            champion_message = (
                f"{header}: You are the champion! Last recorded match: "
                f"{round_name} ({match_id})."
            )
            blocks.append(champion_message)
            continue

        if context.status == "not_seeded":
            unseeded_message = (
                f"{header}: Registered, but not currently seeded in the bracket. "
                "Please contact a tournament admin."
            )
            blocks.append(unseeded_message)

    message = (
        "\n\n".join(blocks)
        if blocks
        else ("Unable to find any bracket information for your registrations.")
    )
    await interaction.response.send_message(message, ephemeral=True)


@tournament_command(
    name="alertopponent",
    description="Ping your opponent with match details so you can schedule",
)
async def alert_opponent_command(  # pragma: no cover - Discord slash command wiring
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

    user_id = getattr(interaction.user, "id", None)
    if user_id is None:
        await interaction.response.send_message(
            "Unable to determine your Discord account.", ephemeral=True
        )
        return

    contexts = gather_opponent_contexts(guild.id, user_id)
    if not contexts:
        await interaction.response.send_message(
            "You are not registered as a team captain in any tournament divisions.",
            ephemeral=True,
        )
        return

    ready_contexts = [
        context
        for context in contexts
        if context.status == "pending"
        and context.match is not None
        and context.opponent_slot is not None
        and context.opponent_slot.team_id is not None
    ]

    if not ready_contexts:
        notes: list[str] = []
        for context in contexts:
            header = f"{context.division_name} ({context.division_id})"
            if context.status == "no_bracket":
                notes.append(f"{header}: Bracket not created yet.")
            elif context.status == "awaiting_opponent":
                if context.match is not None and context.opponent_slot is not None:
                    notes.append(
                        f"{header}: Waiting for {context.opponent_slot.display()} to advance "
                        f"({context.match.match_id})."
                    )
                else:
                    notes.append(
                        f"{header}: Waiting for the bracket to update with your opponent."
                    )
            elif context.status == "eliminated":
                notes.append(f"{header}: You have been eliminated from the bracket.")
            elif context.status == "champion":
                notes.append(f"{header}: You have already won the bracket — congrats!")
            elif context.status == "not_seeded":
                note = (
                    f"{header}: Registered, but not currently seeded. "
                    "Contact a tournament admin."
                )
                notes.append(note)

        fallback_message = (
            "No opponents are ready to ping right now."
            if not notes
            else "No opponents are ready to ping right now:\n" + "\n".join(notes)
        )
        await interaction.response.send_message(fallback_message, ephemeral=True)
        return

    alert_blocks: list[str] = []
    private_notes: list[str] = []

    for context in contexts:
        header = f"{context.division_name} ({context.division_id})"
        if (
            context.status == "pending"
            and context.match is not None
            and context.opponent_slot is not None
            and context.opponent_slot.team_id is not None
        ):
            opponent_registration = context.opponent_registration
            opponent_id = context.opponent_slot.team_id
            opponent_mention = f"<@{opponent_id}>"
            opponent_team = (
                team_display_name(opponent_registration)
                if opponent_registration is not None
                else context.opponent_slot.display()
            )
            opponent_players = (
                format_player_names(opponent_registration)
                if opponent_registration is not None
                else "No player names on file"
            )
            my_team = team_display_name(context.registration)
            my_players = format_player_names(context.registration)
            match_label = (
                f"{context.round_name or 'Upcoming round'} — {context.match.match_id}"
            )
            alert_lines = [
                f"{opponent_mention} {header}",
                f"- Match: {match_label}",
                f"- Your team: {opponent_team} ({opponent_players})",
                f"- Our team: {my_team} ({my_players})",
                "Please reply with your availability so we can schedule our match.",
            ]
            alert_blocks.append("\n".join(alert_lines))
        else:
            if context.status == "no_bracket":
                private_notes.append(f"{header}: Bracket not created yet.")
            elif context.status == "awaiting_opponent":
                if context.match is not None and context.opponent_slot is not None:
                    private_notes.append(
                        f"{header}: Waiting for {context.opponent_slot.display()} to advance "
                        f"({context.match.match_id})."
                    )
                else:
                    private_notes.append(
                        f"{header}: Waiting for the bracket to update with your opponent."
                    )
            elif context.status == "eliminated":
                private_notes.append(
                    f"{header}: You have been eliminated from the bracket."
                )
            elif context.status == "champion":
                private_notes.append(
                    f"{header}: You have already won the bracket — congrats!"
                )
            elif context.status == "not_seeded":
                private_note = (
                    f"{header}: Registered, but not currently seeded. "
                    "Contact a tournament admin."
                )
                private_notes.append(private_note)

    body = "\n\n".join(alert_blocks)
    await interaction.response.send_message(body)

    if private_notes:
        note_text = "\n".join(private_notes)
        try:
            await interaction.followup.send(note_text, ephemeral=True)
        except discord.HTTPException as exc:  # pragma: no cover - defensive
            log.debug("Failed to send alertopponent follow-up: %s", exc)


@app_commands.describe(
    division="Tournament division identifier (e.g. th12)",
)
@tournament_command(
    name="simulate-tourney", description="Simulate the full tournament flow"
)
@require_admin_or_tournament_role()
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
    if isinstance(error, app_errors.MissingPermissions) or isinstance(
        error, app_errors.CheckFailure
    ):
        await send_ephemeral(
            interaction,
            "You need administrator or tournament-admin role to run this command.",
        )
        return
    log.exception("Unhandled simulate-tourney error: %s", error)
    await send_ephemeral(
        interaction,
        "An unexpected error occurred while simulating the tournament.",
    )


# ---------- Autocomplete Wiring ----------


@register_player_command.autocomplete("division")
async def _register_player_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@team_name_command.autocomplete("division")
async def _team_name_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@assign_role_command.autocomplete("division")
async def _assign_role_division_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await division_autocomplete(interaction, current)


@adjust_bracket_command.autocomplete("division")
async def _adjust_bracket_division_autocomplete(
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


@broadcast_bracket_command.autocomplete("division")
async def _broadcast_bracket_division_autocomplete(
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
        # Remove any previously-synced global commands so only the guild copy remains.
        tree.clear_commands(guild=None)
        await tree.sync(guild=None)
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
