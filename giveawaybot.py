import asyncio
import datetime
import logging
import os
import random
import uuid
from dataclasses import dataclass
from typing import Final
from zoneinfo import ZoneInfo

import boto3
import coc
import discord
from boto3.dynamodb import conditions
from discord import app_commands
from discord.errors import InteractionResponded
from discord.ext import tasks

# Import fairness system
from giveaway_fairness import select_fair_winners, update_giveaway_stats

log = logging.getLogger("giveaway-bot")

TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
GIVEAWAY_CHANNEL_ID: Final[int] = int(os.getenv("GIVEAWAY_CHANNEL_ID"))
GIVEAWAY_TABLE_NAME: Final[str | None] = os.getenv("GIVEAWAY_TABLE_NAME")
AWS_REGION: Final[str] = os.getenv("AWS_REGION", "us-east-1")
TEST_MODE: Final[bool] = os.getenv("GIVEAWAY_TEST", "false").lower() in {
    "1",
    "true",
    "yes",
}
USE_FAIRNESS_SYSTEM: Final[bool] = os.getenv("USE_FAIRNESS_SYSTEM", "true").lower() in {
    "1",
    "true",
    "yes",
}
COC_EMAIL: Final[str | None] = os.getenv("COC_EMAIL")
COC_PASSWORD: Final[str | None] = os.getenv("COC_PASSWORD")
CLAN_TAG: Final[str | None] = os.getenv("CLAN_TAG")
FEEDER_CLAN_TAG: Final[str | None] = os.getenv("FEEDER_CLAN_TAG")
DDB_TABLE_NAME: Final[str | None] = os.getenv("DDB_TABLE_NAME")
GUILD_ID_RAW: Final[str | None] = os.getenv("TOURNAMENT_GUILD_ID") or os.getenv(
    "GIVEAWAY_GUILD_ID"
)

if GUILD_ID_RAW:
    try:
        _guild_id = int(GUILD_ID_RAW)
    except ValueError:
        log.warning(
            "Invalid guild id %s provided; expected an integer.",
            GUILD_ID_RAW,
        )
        _guild_id = None
else:
    _guild_id = None

GIVEAWAY_GUILD_ID: Final[int | None] = _guild_id

REQUIRED_VARS = (
    "DISCORD_TOKEN",
    "GIVEAWAY_CHANNEL_ID",
    "GIVEAWAY_TABLE_NAME",
    "COC_EMAIL",
    "COC_PASSWORD",
    "CLAN_TAG",
    "DDB_TABLE_NAME",
)

intents = discord.Intents.default()
intents.guilds = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

GUILD_OBJECT = (
    discord.Object(id=GIVEAWAY_GUILD_ID) if GIVEAWAY_GUILD_ID is not None else None
)


def giveaway_command(*args, **kwargs):
    """Register a giveaway slash command scoped to the configured guild."""

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


coc_client = coc.Client()

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(GIVEAWAY_TABLE_NAME) if GIVEAWAY_TABLE_NAME else None
ver_table = dynamodb.Table(DDB_TABLE_NAME) if DDB_TABLE_NAME else None

_views_restored = False


@dataclass(slots=True)
class GiveawayStatistics:
    """Aggregate view of giveaway performance metrics."""

    total_giveaways: int = 0
    completed_giveaways: int = 0
    active_giveaways: int = 0
    ready_to_draw: int = 0
    scheduled_giveaways: int = 0
    total_entries: int = 0
    average_entries: float = 0.0
    total_winners_recorded: int = 0
    giveaways_with_winners: int = 0
    successful_payouts: int = 0


SUCCESSFUL_PAYOUT_VALUES: Final[set[str]] = {
    "1",
    "true",
    "yes",
    "y",
    "on",
    "success",
    "successful",
    "complete",
    "completed",
    "paid",
    "payment_complete",
    "payout_complete",
    "payout_completed",
    "settled",
    "done",
}


def _is_truthy(value: object) -> bool:
    """Best-effort conversion of DynamoDB truthy values to booleans."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
            "done",
            "complete",
            "completed",
            "finished",
        }
    return False


def _is_success_state(value: object) -> bool:
    """Return True when a DynamoDB value represents a successful payout."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in SUCCESSFUL_PAYOUT_VALUES
    return False


def _parse_draw_time(raw: str | None) -> datetime.datetime | None:
    """Parse stored draw time values into aware datetimes."""

    if not raw:
        return None
    try:
        draw_time = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if draw_time.tzinfo is None:
        draw_time = draw_time.replace(tzinfo=datetime.UTC)
    return draw_time


async def _collect_giveaway_statistics() -> GiveawayStatistics:
    """Aggregate giveaway statistics from DynamoDB."""

    stats = GiveawayStatistics()
    if table is None:
        return stats

    now = datetime.datetime.now(tz=datetime.UTC)
    scan_kwargs: dict[str, object] = {
        "FilterExpression": conditions.Attr("user_id").eq("META")
    }

    try:
        while True:
            response = table.scan(**scan_kwargs)
            items = response.get("Items", [])
            for item in items:
                giveaway_id = item.get("giveaway_id")
                if not giveaway_id:
                    continue

                stats.total_giveaways += 1
                drawn = _is_truthy(item.get("drawn"))
                if drawn:
                    stats.completed_giveaways += 1
                else:
                    stats.active_giveaways += 1
                    draw_time = _parse_draw_time(item.get("draw_time"))
                    if draw_time is not None:
                        if now >= draw_time:
                            stats.ready_to_draw += 1
                        else:
                            stats.scheduled_giveaways += 1

                if any(
                    _is_success_state(item.get(field))
                    for field in (
                        "payout_status",
                        "payout_confirmed",
                        "payout_complete",
                    )
                ):
                    stats.successful_payouts += 1

                run_id = item.get("run_id")
                if isinstance(run_id, str) and run_id:
                    try:
                        entry_resp = table.query(
                            KeyConditionExpression=conditions.Key("giveaway_id").eq(
                                giveaway_id
                            )
                            & conditions.Key("user_id").begins_with(f"{run_id}#")
                        )
                        participants = {
                            entry.get("user_id", "").split("#", 1)[1]
                            for entry in entry_resp.get("Items", [])
                            if entry.get("user_id", "").startswith(f"{run_id}#")
                        }
                        stats.total_entries += len(participants)
                    except Exception as exc:  # pylint: disable=broad-except
                        log.exception(
                            "Failed to count entries for giveaway %s: %s",
                            giveaway_id,
                            exc,
                        )

            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to scan giveaway metadata: %s", exc)

    winner_giveaways: set[str] = set()
    history_kwargs: dict[str, object] = {
        "KeyConditionExpression": conditions.Key("giveaway_id").eq("WINNER_HISTORY")
        & conditions.Key("user_id").begins_with("HISTORY#")
    }

    try:
        while True:
            history_resp = table.query(**history_kwargs)
            history_items = history_resp.get("Items", [])
            stats.total_winners_recorded += len(history_items)
            for winner_item in history_items:
                original = winner_item.get("original_giveaway_id")
                if original:
                    winner_giveaways.add(str(original))

            last_key = history_resp.get("LastEvaluatedKey")
            if not last_key:
                break
            history_kwargs["ExclusiveStartKey"] = last_key
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to load winner history: %s", exc)

    stats.giveaways_with_winners = len(winner_giveaways)
    if stats.total_giveaways:
        stats.average_entries = stats.total_entries / stats.total_giveaways

    return stats


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str, run_id: str) -> None:
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.run_id = run_id

        if hasattr(self, "enter"):
            self.enter.custom_id = f"enter-{giveaway_id}-{run_id}"

    async def _update_entry_count(self) -> int:
        """Update the embed footer with the current entry count."""
        if table is None:
            return 0
        try:
            resp = table.query(
                KeyConditionExpression=conditions.Key("giveaway_id").eq(
                    self.giveaway_id
                )
                & conditions.Key("user_id").begins_with(f"{self.run_id}#")
            )
            users = {
                it["user_id"].split("#", 1)[1]
                for it in resp.get("Items", [])
                if it.get("user_id", "").startswith(f"{self.run_id}#")
            }
            count = len(users)
            meta = table.get_item(
                Key={"giveaway_id": self.giveaway_id, "user_id": "META"}
            ).get("Item")
            if meta and meta.get("message_id"):
                channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
                if isinstance(channel, discord.TextChannel):
                    try:
                        msg = await channel.fetch_message(int(meta["message_id"]))
                        embed = msg.embeds[0] if msg.embeds else discord.Embed()
                        embed.set_footer(text=f"{count} entries")
                        await msg.edit(embed=embed)
                    except Exception as exc:  # pylint: disable=broad-except
                        log.exception("Failed to update entry count: %s", exc)
            return count
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to query entry count: %s", exc)
            return 0

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.green)
    async def enter(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:  # pylint: disable=unused-argument
        if table is None:
            await interaction.response.send_message(
                "Database not configured", ephemeral=True
            )
            return
        try:
            table.put_item(
                Item={
                    "giveaway_id": self.giveaway_id,
                    "user_id": f"{self.run_id}#{interaction.user.id}",
                },
                ConditionExpression="attribute_not_exists(user_id)",
            )
            count = await self._update_entry_count()
            await interaction.response.send_message(
                f"You're entered! ({count} entries)",
                ephemeral=True,
            )
        except Exception as exc:  # pylint: disable=broad-except
            # Handle conditional check failure (already entered) and generic errors
            handled = False
            try:
                from botocore.exceptions import ClientError  # type: ignore
            except Exception:  # pragma: no cover
                ClientError = None  # type: ignore

            if ClientError is not None and isinstance(exc, ClientError):
                code = exc.response.get("Error", {}).get("Code")
                if code == "ConditionalCheckFailedException":
                    count = await self._update_entry_count()
                    await interaction.response.send_message(
                        f"You're already entered! ({count} entries)",
                        ephemeral=True,
                    )
                    handled = True
            if not handled:
                try:
                    _typed = (
                        dynamodb.meta.client.exceptions.ConditionalCheckFailedException
                    )  # type: ignore[attr-defined]
                except Exception:
                    _typed = None  # type: ignore
                if _typed is not None and isinstance(exc, _typed):
                    count = await self._update_entry_count()
                    await interaction.response.send_message(
                        f"You're already entered! ({count} entries)",
                        ephemeral=True,
                    )
                    handled = True
            if not handled:
                log.exception("Failed to record entry: %s", exc)
                await interaction.response.send_message("Entry failed", ephemeral=True)


async def create_giveaway(
    giveaway_id: str, title: str, description: str, draw_time: datetime.datetime
) -> None:
    """Create and announce a giveaway message."""
    if table is None or not bot.guilds:
        return
    if TEST_MODE:
        draw_time = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(
            minutes=1
        )
    channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        log.warning("Giveaway channel not found or not text")
        return
    run_id = uuid.uuid4().hex
    view = GiveawayView(giveaway_id, run_id)
    draw_time = (
        draw_time if draw_time.tzinfo else draw_time.replace(tzinfo=datetime.UTC)
    )
    ts = int(draw_time.timestamp())
    embed = discord.Embed(title=title, description=description, timestamp=draw_time)
    embed.add_field(name="Draw Time", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
    embed.set_footer(text="0 entries")
    msg = await channel.send(embed=embed, view=view)
    # Register the view so the interaction survives bot restarts
    bot.add_view(view, message_id=msg.id)
    try:
        table.put_item(
            Item={
                "giveaway_id": giveaway_id,
                "user_id": "META",
                "message_id": str(msg.id),
                "draw_time": draw_time.isoformat(),
                "run_id": run_id,
            }
        )
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to store meta: %s", exc)


def month_end_giveaway_id(date: datetime.date) -> str:
    return f"goldpass-{date:%Y-%m}"


def weekly_giveaway_id(date: datetime.date) -> str:
    return f"giftcard-{date:%Y-%m-%d}"


@tasks.loop(hours=12)
async def schedule_check() -> None:
    today = datetime.date.today()

    # Gold pass 5 days before month end (avoid constructing datetime.date class directly)
    # Robust last-day calculation that works even if datetime.date is patched in tests
    next_month = (today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    last_date = next_month - datetime.timedelta(days=1)
    target = last_date - datetime.timedelta(days=5)
    if today == target:
        gid = month_end_giveaway_id(today)
        if not await giveaway_exists(gid):
            await create_giveaway(
                gid,
                "🏆 Gold Pass Giveaway",
                "Click the button to enter for a chance to win a Clash of Clans Gold Pass!",
                datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=1),
            )

    # Gift card every Thursday
    if today.weekday() == 3:  # Thursday
        gid = weekly_giveaway_id(today)
        if not await giveaway_exists(gid):
            sunday = today + datetime.timedelta(days=3)
            draw_time = datetime.datetime.combine(
                sunday,
                datetime.time(hour=18, tzinfo=ZoneInfo("America/Chicago")),
            ).astimezone(datetime.UTC)
            await create_giveaway(
                gid,
                "🎁 $10 Gift Card Giveaway",
                (
                    "If you earned at least 23,000 capital raid loot: "
                    "Enter for a chance to win a $10 gift card! Up to 3 winners.\n"
                    "Some regions can't receive gift cards; we'll try PayPal, "
                    "or Gold Passes if PayPal isn't available."
                ),
                draw_time,
            )


async def giveaway_exists(giveaway_id: str) -> bool:
    if table is None:
        return False
    try:
        resp = table.get_item(Key={"giveaway_id": giveaway_id, "user_id": "META"})
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("get_item failed: %s", exc)
        return False
    item = resp.get("Item")
    return bool(item and not item.get("drawn"))


async def eligible_for_giftcard(discord_id: str) -> bool:
    item: dict | None = None
    if ver_table is not None:
        try:
            resp = ver_table.get_item(Key={"discord_id": discord_id})
            item = resp.get("Item")
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to get verification for %s: %s", discord_id, exc)
    else:
        if TEST_MODE:
            log.info(
                "TEST_MODE: verification table unavailable while checking %s",
                discord_id,
            )
        else:
            log.warning(
                "Verification table unavailable; cannot validate %s for gift card",
                discord_id,
            )

    if not item:
        if TEST_MODE:
            log.info("TEST_MODE: no verification record for %s", discord_id)
        return False

    tag = item.get("player_tag")
    if not tag:
        log.debug("No player tag recorded for %s", discord_id)
        return False

    clan_tag = item.get("clan_tag") or CLAN_TAG
    if not clan_tag:
        log.warning(
            "No clan tag available for %s; cannot validate eligibility", discord_id
        )
        return False

    try:
        raid_log = await coc_client.get_raid_log(clan_tag, limit=1)
        if not raid_log:
            return False
        entry = raid_log[0]
        member = entry.get_member(tag)
        if TEST_MODE:
            log.info(
                "TEST_MODE: capital loot check for %s -> %s",
                discord_id,
                member.capital_resources_looted if member else "None",
            )
        if member is None:
            return False
        return member.capital_resources_looted >= 23_000
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Raid log check failed for clan %s: %s", clan_tag, exc)
    return False


async def finish_giveaway(
    gid: str,
    *,
    discord_client: discord.Client | None = None,
    announcement_template: str | None = None,
) -> list[str]:
    if table is None:
        return []
    try:
        meta = table.get_item(Key={"giveaway_id": gid, "user_id": "META"}).get("Item")
        if not meta or meta.get("drawn"):
            return []
        run_id = meta.get("run_id", "")
        resp = table.query(
            KeyConditionExpression=conditions.Key("giveaway_id").eq(gid)
            & conditions.Key("user_id").begins_with(f"{run_id}#")
        )
        entries = {
            it["user_id"].split("#", 1)[1]
            for it in resp.get("Items", [])
            if it.get("user_id", "").startswith(f"{run_id}#")
        }
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to query giveaway %s: %s", gid, exc)
        return []

    if gid.startswith("giftcard"):
        filtered_entries: list[str] = []
        for entry in entries:
            try:
                if await eligible_for_giftcard(entry):
                    filtered_entries.append(entry)
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Eligibility check failed for %s: %s", entry, exc)
        entries = filtered_entries
        winners_needed = 3
        giveaway_type = "giftcard"
    else:
        entries = list(entries)
        winners_needed = 1
        giveaway_type = "goldpass"

    if not entries:
        winners: list[str] = []
    else:
        if USE_FAIRNESS_SYSTEM:
            try:
                # Use fairness system for winner selection
                winners = await select_fair_winners(
                    table, entries, giveaway_type, winners_needed
                )
                log.info(
                    f"Selected {len(winners)} winners using fairness system for {gid}"
                )

                # Update statistics for all participants and winners
                await update_giveaway_stats(table, winners, entries, gid, giveaway_type)

            except Exception as exc:
                log.exception(
                    f"Fairness system failed for {gid}, falling back to random: {exc}"
                )
                # Fallback to original random selection
                random.shuffle(entries)
                winners = entries[: min(winners_needed, len(entries))]
        else:
            # Original random selection (backward compatibility)
            random.shuffle(entries)
            winners = entries[: min(winners_needed, len(entries))]
            log.info(
                f"Selected {len(winners)} winners using random selection for {gid}"
            )

    client = discord_client or bot
    channel = client.get_channel(GIVEAWAY_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel) and meta.get("message_id"):
        try:
            msg = await channel.fetch_message(int(meta["message_id"]))
            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    label="Giveaway Closed",
                    style=discord.ButtonStyle.grey,
                    disabled=True,
                )
            )

            embed = msg.embeds[0] if msg.embeds else discord.Embed()
            draw_time_str = meta.get("draw_time")
            if draw_time_str:
                try:
                    draw_time = datetime.datetime.fromisoformat(draw_time_str)
                    ts = int(draw_time.timestamp())
                    for idx, field in enumerate(embed.fields):
                        if field.name == "Draw Time":
                            embed.set_field_at(
                                idx,
                                name="Draw Time",
                                value=f"<t:{ts}:F>",
                                inline=field.inline,
                            )
                            break
                except Exception as exc:  # pylint: disable=broad-except
                    log.exception("Failed to update draw time field: %s", exc)
            embed.timestamp = None

            await msg.edit(embed=embed, view=view)
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to update message: %s", exc)

        winner_parts: list[str] = []
        for w in winners:
            name: str | None = None
            if ver_table is not None:
                try:
                    resp = ver_table.get_item(Key={"discord_id": w})
                    item = resp.get("Item")
                    if item:
                        name = item.get("player_name")
                except Exception as exc:  # pylint: disable=broad-except
                    log.exception("Failed to fetch player name for %s: %s", w, exc)
            if name:
                winner_parts.append(f"<@{w}> ({name})")
            else:
                winner_parts.append(f"<@{w}>")

        mention = " ".join(winner_parts) if winners else "No valid entries"
        announcement = (
            announcement_template.format(giveaway_id=gid, winners=mention)
            if announcement_template
            else f"🎉 Giveaway **{gid}** winners: {mention}"
        )
        await channel.send(announcement)

    try:
        table.update_item(
            Key={"giveaway_id": gid, "user_id": "META"},
            UpdateExpression="SET drawn = :d",
            ExpressionAttributeValues={":d": "1"},
        )
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to mark giveaway drawn: %s", exc)

    return winners


@tasks.loop(minutes=1 if TEST_MODE else 10)
async def draw_check() -> None:
    if table is None:
        return
    scan_kwargs: dict[str, object] = {
        "FilterExpression": conditions.Attr("user_id").eq("META")
    }

    while True:
        try:
            resp = table.scan(**scan_kwargs)
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Scan failed: %s", exc)
            return

        now = datetime.datetime.now(tz=datetime.UTC)
        for item in resp.get("Items", []):
            if item.get("drawn"):
                continue
            draw_time_str = item.get("draw_time")
            if not draw_time_str:
                continue
            try:
                draw_time = datetime.datetime.fromisoformat(draw_time_str)
            except ValueError:
                continue
            if draw_time.tzinfo is None:
                draw_time = draw_time.replace(tzinfo=datetime.UTC)
            if now >= draw_time:
                gid = item.get("giveaway_id")
                if not gid:
                    continue
                try:
                    await finish_giveaway(str(gid))
                except Exception as exc:  # pylint: disable=broad-except
                    log.exception("Failed to finish giveaway %s: %s", gid, exc)

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key


@tasks.loop(hours=24)  # Run daily
async def fairness_maintenance() -> None:
    """Perform daily maintenance on the fairness system."""
    if not USE_FAIRNESS_SYSTEM or table is None:
        return

    try:
        from giveaway_fairness import GiveawayFairness

        fairness = GiveawayFairness(table)

        # Apply time-based pity decay for inactive users
        await fairness.apply_time_based_decay()

        # Check if population pity reset is needed
        analytics = await fairness.get_fairness_analytics()
        avg_pity = analytics.get("average_pity", 0)

        if fairness.should_reset_population_pity(avg_pity):
            await fairness.apply_population_pity_reset(0.6)
            log.info(
                f"Automatic population pity reset applied (avg pity was {avg_pity:.2f})"
            )

        log.debug("Daily fairness maintenance completed")

    except Exception as exc:
        log.exception(f"Failed to perform fairness maintenance: {exc}")


@fairness_maintenance.before_loop
async def before_fairness_maintenance():
    """Wait for bot to be ready before starting fairness maintenance."""
    await bot.wait_until_ready()


async def _table_is_empty() -> bool:
    """Return True if the giveaway table has no items."""
    if table is None:
        return False
    try:
        resp = table.scan(Limit=1)
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Table scan failed: %s", exc)
        return False
    return not resp.get("Items")


async def seed_initial_giveaways() -> None:
    """Create giveaways on first run if the table is empty."""
    if not await _table_is_empty():
        return
    log.info("Giveaway table empty – creating initial giveaways")
    today = datetime.date.today()

    # Gold pass giveaway drawn in 1 day
    await create_giveaway(
        month_end_giveaway_id(today),
        "\U0001f3c6 Gold Pass Giveaway",
        "Click the button to enter for a chance to win a Clash of Clans Gold Pass!",
        datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=1),
    )

    # Gift card giveaway drawn on the upcoming Sunday at 18:00 Central
    sunday = today + datetime.timedelta(days=(6 - today.weekday()))
    draw_time = datetime.datetime.combine(
        sunday,
        datetime.time(hour=18, tzinfo=ZoneInfo("America/Chicago")),
    ).astimezone(datetime.UTC)
    await create_giveaway(
        weekly_giveaway_id(today),
        "\U0001f381 $10 Gift Card Giveaway",
        "If you earned at least 23,000 capital raid loot: "
        "Enter for a chance to win a $10 gift card! Up to 3 winners.",
        draw_time,
    )


async def restore_persistent_giveaway_views() -> None:
    """Register persistent giveaway buttons created before a restart."""
    global _views_restored  # pylint: disable=global-statement

    if _views_restored or table is None:
        return

    restored = 0
    scan_kwargs: dict[str, object] = {
        "FilterExpression": conditions.Attr("user_id").eq("META")
    }

    while True:
        try:
            resp = table.scan(**scan_kwargs)
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to scan for persistent views: %s", exc)
            return

        for item in resp.get("Items", []):
            if item.get("drawn"):
                continue
            giveaway_id = item.get("giveaway_id")
            run_id = item.get("run_id")
            message_id = item.get("message_id")
            if not (giveaway_id and run_id and message_id):
                continue

            try:
                view = GiveawayView(str(giveaway_id), str(run_id))
                bot.add_view(view, message_id=int(message_id))
                restored += 1
            except Exception as exc:  # pylint: disable=broad-except
                log.exception(
                    "Failed to restore persistent view for %s: %s", giveaway_id, exc
                )

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    _views_restored = True
    if restored:
        log.info("Restored %s persistent giveaway views", restored)


@giveaway_command(name="stats", description="Show giveaway statistics")
async def giveaway_stats(interaction: discord.Interaction) -> None:
    """Show aggregated giveaway statistics to the requesting user."""

    if table is None:
        await interaction.response.send_message(
            "Giveaway database is not configured.",
            ephemeral=True,
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)
    except InteractionResponded:
        pass
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to defer stats interaction: %s", exc)
        try:
            await interaction.response.send_message(
                "Failed to retrieve giveaway statistics.",
                ephemeral=True,
            )
        except InteractionResponded:
            await interaction.followup.send(
                "Failed to retrieve giveaway statistics.", ephemeral=True
            )
        return

    try:
        stats = await _collect_giveaway_statistics()
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to collect giveaway statistics: %s", exc)
        await interaction.followup.send(
            "Failed to retrieve giveaway statistics.", ephemeral=True
        )
        return

    pending_payouts = max(stats.completed_giveaways - stats.successful_payouts, 0)
    entries_value = (
        f"{stats.total_entries} (avg {stats.average_entries:.1f})"
        if stats.total_entries
        else "0"
    )

    embed = discord.Embed(
        title="Giveaway Statistics",
        colour=discord.Color.blurple(),
        timestamp=datetime.datetime.now(tz=datetime.UTC),
    )

    if not stats.total_giveaways:
        embed.description = "No giveaway records were found."

    embed.add_field(
        name="Total Giveaways", value=str(stats.total_giveaways), inline=True
    )
    embed.add_field(name="Completed", value=str(stats.completed_giveaways), inline=True)
    embed.add_field(name="Active", value=str(stats.active_giveaways), inline=True)
    embed.add_field(name="Ready To Draw", value=str(stats.ready_to_draw), inline=True)
    embed.add_field(name="Scheduled", value=str(stats.scheduled_giveaways), inline=True)
    embed.add_field(
        name="Successful Payouts", value=str(stats.successful_payouts), inline=True
    )
    embed.add_field(name="Pending Payouts", value=str(pending_payouts), inline=True)
    embed.add_field(
        name="Giveaways With Winners",
        value=str(stats.giveaways_with_winners),
        inline=True,
    )
    embed.add_field(
        name="Winners Logged", value=str(stats.total_winners_recorded), inline=True
    )
    embed.add_field(name="Entries Recorded", value=entries_value, inline=False)
    embed.set_footer(text="Data pulled from giveaway tracking records")

    await interaction.followup.send(embed=embed, ephemeral=True)


@giveaway_command(
    name="fairness_stats", description="Show giveaway fairness statistics (Admin only)"
)
async def fairness_stats(interaction: discord.Interaction) -> None:
    """Show fairness system statistics."""
    if not USE_FAIRNESS_SYSTEM:
        await interaction.response.send_message(
            "Fairness system is disabled.", ephemeral=True
        )
        return

    try:
        from giveaway_fairness import GiveawayFairness

        fairness = GiveawayFairness(table)
        analytics = await fairness.get_fairness_analytics()

        if "error" in analytics:
            await interaction.response.send_message(
                f"Error retrieving stats: {analytics['error']}", ephemeral=True
            )
            return

        embed = discord.Embed(title="🎯 Giveaway Fairness Statistics", color=0x00FF00)

        if "message" in analytics:
            embed.add_field(name="Status", value=analytics["message"], inline=False)
        else:
            embed.add_field(
                name="Total Users", value=analytics.get("total_users", 0), inline=True
            )
            embed.add_field(
                name="Average Pity",
                value=f"{analytics.get('average_pity', 0):.2f}",
                inline=True,
            )
            embed.add_field(
                name="Average Wins",
                value=f"{analytics.get('average_wins', 0):.2f}",
                inline=True,
            )
            embed.add_field(
                name="Average Entries",
                value=f"{analytics.get('average_entries', 0):.2f}",
                inline=True,
            )
            embed.add_field(
                name="High Pity Users",
                value=analytics.get("high_pity_count", 0),
                inline=True,
            )
            embed.add_field(
                name="Never Won Users",
                value=analytics.get("never_won_count", 0),
                inline=True,
            )

            health = analytics.get("system_health", "unknown")
            health_emoji = "🟢" if health == "good" else "🟡"
            embed.add_field(
                name="System Health",
                value=f"{health_emoji} {health.title()}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as exc:
        log.exception(f"Failed to get fairness stats: {exc}")
        await interaction.response.send_message(
            "Failed to retrieve fairness statistics.", ephemeral=True
        )


@giveaway_command(
    name="reset_population_pity", description="Reset pity for all users (Admin only)"
)
async def reset_population_pity(
    interaction: discord.Interaction, factor: float = 0.5
) -> None:
    """Reset pity levels for all users."""
    if not USE_FAIRNESS_SYSTEM:
        await interaction.response.send_message(
            "Fairness system is disabled.", ephemeral=True
        )
        return

    # Simple admin check - you might want to make this more sophisticated
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "This command requires administrator permissions.", ephemeral=True
        )
        return

    if factor <= 0 or factor > 1:
        await interaction.response.send_message(
            "Reset factor must be between 0 and 1.", ephemeral=True
        )
        return

    try:
        from giveaway_fairness import GiveawayFairness

        fairness = GiveawayFairness(table)

        await interaction.response.defer(ephemeral=True)

        await fairness.apply_population_pity_reset(factor)

        await interaction.followup.send(
            f"✅ Applied population pity reset with factor {factor}. "
            f"All user pity values have been multiplied by {factor}.",
            ephemeral=True,
        )

        log.info(
            f"Population pity reset applied by {interaction.user} with factor {factor}"
        )

    except Exception as exc:
        log.exception(f"Failed to apply population pity reset: {exc}")
        await interaction.followup.send(
            "Failed to apply population pity reset.", ephemeral=True
        )


@giveaway_command(
    name="fairness_decay", description="Apply time-based pity decay (Admin only)"
)
async def apply_fairness_decay(interaction: discord.Interaction) -> None:
    """Apply time-based pity decay for inactive users."""
    if not USE_FAIRNESS_SYSTEM:
        await interaction.response.send_message(
            "Fairness system is disabled.", ephemeral=True
        )
        return

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "This command requires administrator permissions.", ephemeral=True
        )
        return

    try:
        from giveaway_fairness import GiveawayFairness

        fairness = GiveawayFairness(table)

        await interaction.response.defer(ephemeral=True)

        await fairness.apply_time_based_decay()

        await interaction.followup.send(
            "✅ Applied time-based pity decay for inactive users.", ephemeral=True
        )
        log.info(f"Time-based pity decay applied by {interaction.user}")

    except Exception as exc:
        log.exception(f"Failed to apply pity decay: {exc}")
        await interaction.followup.send("Failed to apply pity decay.", ephemeral=True)


@bot.event
async def on_ready() -> None:
    if GUILD_OBJECT is not None:
        await tree.sync(guild=GUILD_OBJECT)
        log.info("Commands synced to guild %s", GIVEAWAY_GUILD_ID)
    else:
        await tree.sync()
        log.info("Commands synced globally")
    await coc_client.login(COC_EMAIL, COC_PASSWORD)
    await restore_persistent_giveaway_views()
    schedule_check.start()
    draw_check.start()

    # Start fairness maintenance if enabled
    if USE_FAIRNESS_SYSTEM:
        fairness_maintenance.start()

    await seed_initial_giveaways()
    await schedule_check()
    await draw_check()

    # Log fairness system status
    fairness_status = "enabled" if USE_FAIRNESS_SYSTEM else "disabled"
    log.info(
        "Giveaway bot ready as %s (Fairness system: %s)", bot.user, fairness_status
    )

    if TEST_MODE:
        await create_giveaway(
            month_end_giveaway_id(datetime.date.today()),
            "🏆 Gold Pass Giveaway (Test)",
            "Test mode giveaway! Click to enter.",
            datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(minutes=1),
        )
        await create_giveaway(
            weekly_giveaway_id(datetime.date.today()),
            "🎁 $10 Gift Card Giveaway (Test)",
            "Test mode gift card giveaway!",
            datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(minutes=1),
        )


async def main() -> None:
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    async with bot:
        await coc_client.login(COC_EMAIL, COC_PASSWORD)
        await bot.start(TOKEN)  # type: ignore[arg-type]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
