import asyncio
import calendar
import datetime
import logging
import os
import uuid
from typing import Final
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb import conditions
import coc
import random
import discord
from discord import app_commands
from discord.ext import tasks

TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
GIVEAWAY_CHANNEL_ID: Final[int] = int(os.getenv("GIVEAWAY_CHANNEL_ID"))
GIVEAWAY_TABLE_NAME: Final[str | None] = os.getenv("GIVEAWAY_TABLE_NAME")
AWS_REGION: Final[str] = os.getenv("AWS_REGION", "us-east-1")
TEST_MODE: Final[bool] = os.getenv("GIVEAWAY_TEST").lower() in {"1", "true", "yes"}
COC_EMAIL: Final[str | None] = os.getenv("COC_EMAIL")
COC_PASSWORD: Final[str | None] = os.getenv("COC_PASSWORD")
CLAN_TAG: Final[str | None] = os.getenv("CLAN_TAG")
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

coc_client = coc.Client()

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(GIVEAWAY_TABLE_NAME) if GIVEAWAY_TABLE_NAME else None
ver_table = dynamodb.Table(DDB_TABLE_NAME) if DDB_TABLE_NAME else None

log = logging.getLogger("giveaway-bot")


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
                KeyConditionExpression=
                conditions.Key("giveaway_id").eq(self.giveaway_id)
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
    async def enter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # pylint: disable=unused-argument
        if table is None:
            await interaction.response.send_message("Database not configured", ephemeral=True)
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
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:  # type: ignore[attr-defined]
            count = await self._update_entry_count()
            await interaction.response.send_message(
                f"You're already entered! ({count} entries)",
                ephemeral=True,
            )
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to record entry: %s", exc)
            await interaction.response.send_message("Entry failed", ephemeral=True)


async def create_giveaway(
    giveaway_id: str, title: str, description: str, draw_time: datetime.datetime
) -> None:
    """Create and announce a giveaway message."""
    if table is None or not bot.guilds:
        return
    if TEST_MODE:
        draw_time = (
            datetime.datetime.now(tz=datetime.timezone.utc)
            + datetime.timedelta(minutes=1)
        )
    channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        log.warning("Giveaway channel not found or not text")
        return
    run_id = uuid.uuid4().hex
    view = GiveawayView(giveaway_id, run_id)
    draw_time = (
        draw_time if draw_time.tzinfo else draw_time.replace(tzinfo=datetime.timezone.utc)
    )
    ts = int(draw_time.timestamp())
    embed = discord.Embed(title=title, description=description, timestamp=draw_time)
    embed.add_field(name="Draw Time", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
    embed.set_footer(text="0 entries")
    msg = await channel.send(embed=embed, view=view)
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

    # Gold pass 5 days before month end
    last_day = calendar.monthrange(today.year, today.month)[1]
    target = datetime.date(today.year, today.month, last_day) - datetime.timedelta(days=5)
    if today == target:
        gid = month_end_giveaway_id(today)
        if not await giveaway_exists(gid):
            await create_giveaway(
                gid,
                "🏆 Gold Pass Giveaway",
                "Click the button to enter for a chance to win a Clash of Clans Gold Pass!",
                datetime.datetime.now(tz=datetime.timezone.utc)
                + datetime.timedelta(days=1),
            )

    # Gift card every Thursday
    if today.weekday() == 3:  # Thursday
        gid = weekly_giveaway_id(today)
        if not await giveaway_exists(gid):
            sunday = today + datetime.timedelta(days=3)
            draw_time = datetime.datetime.combine(
                sunday,
                datetime.time(hour=18, tzinfo=ZoneInfo("America/Chicago")),
            ).astimezone(datetime.timezone.utc)
            await create_giveaway(
                gid,
                "🎁 $10 Gift Card Giveaway",
                "If you used all your attacks in raid weekend: "
                "Enter for a chance to win a $10 gift card! Up to 3 winners.",
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
        log.info(f"TEST_MODE: {TEST_MODE}, tag: {tag}")
        raid_log = await coc_client.get_raid_log(CLAN_TAG, limit=1)
        entry = raid_log[0]
        log.info(f"entry: {entry}")
        member = entry.get_member(tag)
        log.info("Attack count:", member.attack_count if member else "None")
        log.info("Attack limit:", member.attack_limit if member else "None")
        return member.attack_count >= member.attack_limit
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
    try:
        raid_log = await coc_client.get_raid_log(CLAN_TAG, limit=1)
        if not raid_log:
            return False
        entry = raid_log[0]
        member = entry.get_member(tag)
        if member is None:
            return False
        return member.attack_count >= member.attack_limit
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Raid log check failed: %s", exc)
    return False


async def finish_giveaway(gid: str) -> None:
    if table is None:
        return
    try:
        meta = table.get_item(Key={"giveaway_id": gid, "user_id": "META"}).get("Item")
        if not meta or meta.get("drawn"):
            return
        run_id = meta.get("run_id", "")
        resp = table.query(
            KeyConditionExpression=
            conditions.Key("giveaway_id").eq(gid)
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
    else:
        entries = list(entries)
        winners_needed = 1

    if not entries:
        winners: list[str] = []
    else:
        random.shuffle(entries)
        winners = entries[: min(winners_needed, len(entries))]

    channel = bot.get_channel(GIVEAWAY_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel) and meta.get("message_id"):
        try:
            msg = await channel.fetch_message(int(meta["message_id"]))
            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(label="Giveaway Closed", style=discord.ButtonStyle.grey, disabled=True)
            )
            await msg.edit(view=view)
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
    if table is None:
        return
    try:
        resp = table.scan(FilterExpression=conditions.Attr("user_id").eq("META"))
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Scan failed: %s", exc)
        return
    now = datetime.datetime.now(tz=datetime.timezone.utc)
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
            draw_time = draw_time.replace(tzinfo=datetime.timezone.utc)
        if now >= draw_time:
            await finish_giveaway(item["giveaway_id"])


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
        "\U0001F3C6 Gold Pass Giveaway",
        "Click the button to enter for a chance to win a Clash of Clans Gold Pass!",
        datetime.datetime.now(tz=datetime.timezone.utc)
        + datetime.timedelta(days=1),
    )

    # Gift card giveaway drawn on the upcoming Sunday at 18:00 Central
    sunday = today + datetime.timedelta(days=(6 - today.weekday()))
    draw_time = datetime.datetime.combine(
        sunday,
        datetime.time(hour=18, tzinfo=ZoneInfo("America/Chicago")),
    ).astimezone(datetime.timezone.utc)
    await create_giveaway(
        weekly_giveaway_id(today),
        "\U0001F381 $10 Gift Card Giveaway",
        "If you used all your attacks in raid weekend: "
        "Enter for a chance to win a $10 gift card! Up to 3 winners.",
        draw_time,
    )


@bot.event
async def on_ready() -> None:
    await tree.sync()
    await coc_client.login(COC_EMAIL, COC_PASSWORD)
    schedule_check.start()
    draw_check.start()
    await seed_initial_giveaways()
    await schedule_check()
    await draw_check()
    log.info("Giveaway bot ready as %s", bot.user)
    if TEST_MODE:
        await create_giveaway(
            month_end_giveaway_id(datetime.date.today()),
            "🏆 Gold Pass Giveaway (Test)",
            "Test mode giveaway! Click to enter.",
            datetime.datetime.now(tz=datetime.timezone.utc)
            + datetime.timedelta(minutes=1),
        )
        await create_giveaway(
            weekly_giveaway_id(datetime.date.today()),
            "🎁 $10 Gift Card Giveaway (Test)",
            "Test mode gift card giveaway!",
            datetime.datetime.now(tz=datetime.timezone.utc)
            + datetime.timedelta(minutes=1),
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
