import asyncio
import datetime
import logging
import os
import random
import uuid
from typing import Final
from zoneinfo import ZoneInfo

import boto3
import coc
import discord
from boto3.dynamodb import conditions
from discord import app_commands
from discord.ext import tasks

from bots.config import ShadowConfig, read_shadow_config
from bots.shadow import ShadowReporter

# Import fairness system
from giveaway_fairness import select_fair_winners, update_giveaway_stats

TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
GIVEAWAY_CHANNEL_ID: Final[int | None] = (
    int(os.getenv("GIVEAWAY_CHANNEL_ID")) if os.getenv("GIVEAWAY_CHANNEL_ID") else None
)
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

_shadow_config = read_shadow_config(default_enabled=False)
shadow_reporter = ShadowReporter(bot, _shadow_config)

coc_client = None

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(GIVEAWAY_TABLE_NAME) if GIVEAWAY_TABLE_NAME else None
ver_table = dynamodb.Table(DDB_TABLE_NAME) if DDB_TABLE_NAME else None

log = logging.getLogger("giveaway-bot")

_views_restored = False


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
        if shadow_reporter.enabled:
            await interaction.response.send_message(
                "Giveaway entry recorded in shadow mode.", ephemeral=True
            )
            await shadow_reporter.report(
                interaction.guild,
                f"[giveaway] simulated entry for {interaction.user.id}",
            )
            return

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
    if shadow_reporter.enabled:
        embed = discord.Embed(title=title, description=description, timestamp=draw_time)
        await shadow_reporter.report(
            None,
            f"[giveaway] would create giveaway {giveaway_id}",
            embeds=[embed],
        )
        return

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
    if shadow_reporter.enabled:
        await shadow_reporter.report(None, "[giveaway] schedule_check skipped")
        return
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
    if TEST_MODE or ver_table is None or not CLAN_TAG:
        resp = ver_table.get_item(Key={"discord_id": discord_id})
        item = resp.get("Item")
        tag = item.get("player_tag")
        clan_tag = item.get("clan_tag", CLAN_TAG)
        log.info(f"TEST_MODE: {TEST_MODE}, tag: {tag}, clan_tag: {clan_tag}")
        raid_log = await coc_client.get_raid_log(clan_tag, limit=1)
        entry = raid_log[0]
        log.info(f"entry: {entry}")
        member = entry.get_member(tag)
        log.info(
            "Capital loot: %s",
            member.capital_resources_looted if member else "None",
        )
        if member is None:
            return False
        return member.capital_resources_looted >= 23_000
    try:
        resp = ver_table.get_item(Key={"discord_id": discord_id})
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to get verification: %s", exc)
        return False
    item = resp.get("Item")
    if not item:
        return False
    tag = item.get("player_tag")
    if not tag:
        return False

    clan_tag = item.get("clan_tag")
    if not clan_tag:
        clan_tag = CLAN_TAG

    try:
        raid_log = await coc_client.get_raid_log(clan_tag, limit=1)
        if not raid_log:
            return False
        entry = raid_log[0]
        member = entry.get_member(tag)
        if member is None:
            return False
        return member.capital_resources_looted >= 23_000
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Raid log check failed for clan %s: %s", clan_tag, exc)
    return False


async def finish_giveaway(gid: str) -> None:
    if shadow_reporter.enabled:
        await shadow_reporter.report(None, f"[giveaway] would finalize {gid}")
        return
    if table is None:
        return
    try:
        meta = table.get_item(Key={"giveaway_id": gid, "user_id": "META"}).get("Item")
        if not meta or meta.get("drawn"):
            return
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
        return

    if gid.startswith("giftcard"):
        entries = [e for e in entries if await eligible_for_giftcard(e)]
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

    channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
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
        await channel.send(f"🎉 Giveaway **{gid}** winners: {mention}")

    try:
        table.update_item(
            Key={"giveaway_id": gid, "user_id": "META"},
            UpdateExpression="SET drawn = :d",
            ExpressionAttributeValues={":d": "1"},
        )
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to mark giveaway drawn: %s", exc)


@tasks.loop(minutes=1 if TEST_MODE else 10)
async def draw_check() -> None:
    if shadow_reporter.enabled:
        return
    if table is None:
        return
    try:
        resp = table.scan(FilterExpression=conditions.Attr("user_id").eq("META"))
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
            await finish_giveaway(item["giveaway_id"])


@tasks.loop(hours=24)  # Run daily
async def fairness_maintenance() -> None:
    """Perform daily maintenance on the fairness system."""
    if shadow_reporter.enabled:
        return
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
    if shadow_reporter.enabled:
        await shadow_reporter.report(None, "[giveaway] seed_initial_giveaways skipped")
        return
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
    if shadow_reporter.enabled:
        return
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


@tree.command(
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


@tree.command(
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


@tree.command(
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
    await tree.sync()
    if shadow_reporter.enabled:
        await shadow_reporter.report(None, "[giveaway] on_ready shadow mode active")
        return

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
        if not shadow_reporter.enabled:
            await coc_client.login(COC_EMAIL, COC_PASSWORD)
        await bot.start(TOKEN)  # type: ignore[arg-type]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())


def configure_runtime(
    *,
    client: discord.Client | None = None,
    command_tree: app_commands.CommandTree | None = None,
    dynamodb_resource=None,
    giveaway_table: str | None = None,
    verification_table: str | None = None,
    coc_client_override: coc.Client | None = None,
    shadow_enabled: bool | None = None,
    shadow_channel_id: int | None = None,
    test_mode: bool | None = None,
) -> None:
    """Reconfigure module globals for use in the unified bot runtime."""

    global bot, tree, dynamodb, table, ver_table, coc_client, shadow_reporter
    global GIVEAWAY_TABLE_NAME, DDB_TABLE_NAME, TEST_MODE, _shadow_config

    if client is not None:
        bot = client

    prev_tree = tree
    if command_tree is not None:
        tree = command_tree
    elif tree is None:
        tree = app_commands.CommandTree(bot)

    if prev_tree is not tree:
        for command in prev_tree.get_commands():
            if tree.get_command(command.name) is None:
                tree.add_command(command.copy())

    if dynamodb_resource is not None:
        dynamodb = dynamodb_resource

    if giveaway_table is not None:
        GIVEAWAY_TABLE_NAME = giveaway_table
        table = dynamodb.Table(GIVEAWAY_TABLE_NAME)
    elif table is None and GIVEAWAY_TABLE_NAME:
        table = dynamodb.Table(GIVEAWAY_TABLE_NAME)

    if verification_table is not None:
        DDB_TABLE_NAME = verification_table
        ver_table = dynamodb.Table(DDB_TABLE_NAME)
    elif ver_table is None and DDB_TABLE_NAME:
        ver_table = dynamodb.Table(DDB_TABLE_NAME)

    if coc_client_override is not None:
        coc_client = coc_client_override
    elif coc_client is None:
        coc_client = coc.Client()

    if test_mode is not None:
        TEST_MODE = test_mode

    if shadow_enabled is not None or shadow_channel_id is not None:
        _shadow_config = ShadowConfig(
            enabled=
            shadow_enabled if shadow_enabled is not None else _shadow_config.enabled,
            channel_id=
            shadow_channel_id
            if shadow_channel_id is not None
            else _shadow_config.channel_id,
        )

    shadow_reporter = ShadowReporter(bot, _shadow_config)
