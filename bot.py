#!/usr/bin/env python3
"""Discord–Clash-of-Clans gateway verification bot
-------------------------------------------------
Changes in this version (2025‑07‑09 b)
* Added *robust* logging‑channel handling:
  - Uses `bot.get_channel` (global cache) then falls back to `bot.fetch_channel`.
  - Warns if the bot lacks permission to send or the channel ID is wrong.
* Extra debug output when ADMIN_LOG_CHANNEL_ID is set but unavailable.

Required env‑vars: DISCORD_TOKEN, COC_EMAIL, COC_PASSWORD, CLAN_TAG,
VERIFIED_ROLE_ID, DDB_TABLE_NAME
Optional: ADMIN_LOG_CHANNEL_ID (numeric), AWS_REGION
"""

import asyncio
import logging
import os
from typing import Final

import boto3
import coc
import discord
from discord import app_commands
from discord.ext import tasks

# ---------- Environment ----------
DISCORD_TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
COC_EMAIL: Final[str | None] = os.getenv("COC_EMAIL")
COC_PASSWORD: Final[str | None] = os.getenv("COC_PASSWORD")
CLAN_TAG: Final[str | None] = os.getenv("CLAN_TAG")
VERIFIED_ROLE_ID: Final[int] = int(os.getenv("VERIFIED_ROLE_ID", "0"))
ADMIN_LOG_CHANNEL_ID: Final[int] = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0"))
DDB_TABLE_NAME: Final[str | None] = os.getenv("DDB_TABLE_NAME")
AWS_REGION: Final[str] = os.getenv("AWS_REGION", "us-east-1")

REQUIRED_VARS = (
    "DISCORD_TOKEN",
    "COC_EMAIL",
    "COC_PASSWORD",
    "CLAN_TAG",
    "VERIFIED_ROLE_ID",
    "DDB_TABLE_NAME",
)

# ---------- Discord client ----------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------- AWS / CoC clients ----------
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DDB_TABLE_NAME) if DDB_TABLE_NAME else None

coc_client = coc.Client()

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("coc-gateway")


async def resolve_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Return a TextChannel object or None if unavailable."""
    if not ADMIN_LOG_CHANNEL_ID:
        return None

    # First try guild cache -> global cache -> REST fetch
    channel = guild.get_channel(ADMIN_LOG_CHANNEL_ID) or bot.get_channel(
        ADMIN_LOG_CHANNEL_ID
    )
    if channel is None:
        try:
            channel = await bot.fetch_channel(ADMIN_LOG_CHANNEL_ID)
        except discord.HTTPException:
            log.warning(
                "Cannot fetch channel %s – invalid ID or not accessible",
                ADMIN_LOG_CHANNEL_ID,
            )
            return None

    if isinstance(channel, discord.TextChannel):
        return channel
    log.warning("Channel ID %s is not a text channel", ADMIN_LOG_CHANNEL_ID)
    return None


# ---------- Clash API ----------


async def get_player(player_tag: str) -> coc.Player | None:
    """Return player object or None on API error."""
    try:
        player = await coc_client.get_player(player_tag)
    except coc.NotFound:
        log.warning("Player %s not found", player_tag)
        return None
    except coc.HTTPException as exc:
        log.error("CoC API error: %s", exc)
        return None
    return player


async def is_member_of_clan(player_tag: str) -> bool:
    player = await get_player(player_tag)
    if not player or not player.clan:
        return False
    return player.clan.tag.upper() == CLAN_TAG.upper()


# ---------- /verify command ----------
@tree.command(
    name="verify",
    description="Verify yourself as a clan member by providing your player tag.",
)
@app_commands.describe(player_tag="Your Clash of Clans player tag, e.g. #ABCD123")
async def verify(interaction: discord.Interaction, player_tag: str):
    await interaction.response.defer(ephemeral=True)

    player_tag = player_tag.strip().upper()
    if not player_tag.startswith("#"):
        player_tag = "#" + player_tag

    player = await get_player(player_tag)
    if player is None or not player.clan or player.clan.tag.upper() != CLAN_TAG.upper():
        await interaction.followup.send(
            "❌ Verification failed – you are not listed in the clan.",
            ephemeral=True,
        )
        return

    role = interaction.guild.get_role(VERIFIED_ROLE_ID)
    if role is None:
        await interaction.followup.send(
            "Setup error: verified role not found – contact an admin.", ephemeral=True
        )
        log.error(
            "Verified role ID %s not found in guild %s",
            VERIFIED_ROLE_ID,
            interaction.guild.id,
        )
        return

    try:
        await interaction.user.add_roles(role, reason="Passed CoC verification")
    except discord.Forbidden:
        await interaction.followup.send(
            "🚫 Bot lacks **Manage Roles** permission or the role hierarchy is incorrect.",
            ephemeral=True,
        )
        log.warning("Forbidden when adding role to %s", interaction.user)
        return
    except discord.HTTPException as exc:
        await interaction.followup.send(
            "Unexpected Discord error – try again later.", ephemeral=True
        )
        log.exception("HTTPException adding role: %s", exc)
        return

    if table is not None:
        try:
            table.put_item(
                Item={
                    "discord_id": str(interaction.user.id),
                    "discord_name": interaction.user.name,
                    "player_tag": player.tag,
                    "player_name": player.name,
                }
            )
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to store verification: %s", exc)

    await interaction.followup.send("✅ Success! You now have access.", ephemeral=True)

    if log_chan := await resolve_log_channel(interaction.guild):
        try:
            await log_chan.send(
                f"{interaction.user.mention} verified with tag {player_tag}."
            )
        except discord.Forbidden:
            log.warning("No send permission in log channel %s", log_chan.id)
        except discord.HTTPException as exc:
            log.exception("Failed to log verification: %s", exc)


# ---------- /whois command ----------
@tree.command(name="whois", description="Get the clan player name for a Discord user")
@app_commands.describe(member="Member to look up")
async def whois(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)

    if table is None:
        await interaction.followup.send("Database not configured.", ephemeral=True)
        return

    try:
        resp = table.get_item(Key={"discord_id": str(member.id)})
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("DynamoDB get_item failed: %s", exc)
        await interaction.followup.send("Lookup failed.", ephemeral=True)
        return

    item = resp.get("Item")
    if not item:
        await interaction.followup.send("No record found.", ephemeral=True)
        return

    await interaction.followup.send(
        f"{member.display_name} is {item['player_name']}", ephemeral=True
    )


# ---------- Clan membership check ----------
@tasks.loop(minutes=5)
async def membership_check() -> None:
    log.info("Membership check started...")
    if table is None or not bot.guilds:
        return
    guild = bot.guilds[0]
    try:
        data = table.scan()
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to scan table: %s", exc)
        return

    for item in data.get("Items", []):
        discord_id = int(item["discord_id"])
        member = guild.get_member(discord_id)
        if member is None:
            continue
        player = await get_player(item["player_tag"])
        log.info(
            "Player %s (%s) in clan %s",
            player,
            item["player_tag"],
            player.clan.tag if player and player.clan else "None",
        )
        if (
            player is None
            or not player.clan
            or player.clan.tag.upper() != CLAN_TAG.upper()
        ):
            try:
                # await member.kick(reason="Left clan")
                log.warning("TEST MODE: Would kick %s for leaving clan", member)
            except discord.Forbidden:
                log.warning("Forbidden kicking %s", member)
            except discord.HTTPException as exc:
                log.exception("Failed to kick %s: %s", member, exc)
            try:
                table.delete_item(Key={"discord_id": item["discord_id"]})
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Failed to delete record for %s: %s", member, exc)


# ---------- Lifecycle ----------
@bot.event
async def on_ready():
    await tree.sync()
    await coc_client.login(COC_EMAIL, COC_PASSWORD)
    membership_check.start()
    log.info("Bot ready as %s (%s)", bot.user, bot.user.id)


async def main() -> None:
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    async with bot:
        await bot.start(DISCORD_TOKEN)  # type: ignore[arg-type]


if __name__ == "__main__":
    asyncio.run(main())
