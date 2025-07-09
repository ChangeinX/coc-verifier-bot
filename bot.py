#!/usr/bin/env python3
"""Discord–Clash-of-Clans gateway verification bot
-------------------------------------------------
Changes in this version (2025‑07‑09 b)
* Added *robust* logging‑channel handling:
  - Uses `bot.get_channel` (global cache) then falls back to `bot.fetch_channel`.
  - Warns if the bot lacks permission to send or the channel ID is wrong.
* Extra debug output when ADMIN_LOG_CHANNEL_ID is set but unavailable.

Required env‑vars: DISCORD_TOKEN, COC_API_TOKEN, CLAN_TAG, VERIFIED_ROLE_ID
Optional: ADMIN_LOG_CHANNEL_ID (numeric).
"""
import asyncio
import logging
import os
from typing import Final, Optional

import aiohttp
import discord
from discord import app_commands

# ---------- Environment ----------
DISCORD_TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
COC_API_TOKEN: Final[str | None] = os.getenv("COC_API_TOKEN")
CLAN_TAG: Final[str | None] = os.getenv("CLAN_TAG")
VERIFIED_ROLE_ID: Final[int] = int(os.getenv("VERIFIED_ROLE_ID", "0"))
ADMIN_LOG_CHANNEL_ID: Final[int] = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0"))

REQUIRED_VARS = (
    "DISCORD_TOKEN",
    "COC_API_TOKEN",
    "CLAN_TAG",
    "VERIFIED_ROLE_ID",
)

# ---------- Discord client ----------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("coc-gateway")


async def resolve_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Return a TextChannel object or None if unavailable."""
    if not ADMIN_LOG_CHANNEL_ID:
        return None

    # First try guild cache -> global cache -> REST fetch
    channel = guild.get_channel(ADMIN_LOG_CHANNEL_ID) or bot.get_channel(ADMIN_LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(ADMIN_LOG_CHANNEL_ID)
        except discord.HTTPException:
            log.warning("Cannot fetch channel %s – invalid ID or not accessible", ADMIN_LOG_CHANNEL_ID)
            return None

    if isinstance(channel, discord.TextChannel):
        return channel
    log.warning("Channel ID %s is not a text channel", ADMIN_LOG_CHANNEL_ID)
    return None


# ---------- Clash API ----------
COC_API_BASE = "https://api.clashofclans.com/v1"
HEADERS = {"Authorization": f"Bearer {COC_API_TOKEN}"}


async def is_member_of_clan(player_tag: str) -> bool:
    enc_tag = player_tag.upper().replace("#", "%23")
    url = f"{COC_API_BASE}/players/{enc_tag}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=HEADERS, timeout=10) as resp:
            if resp.status != 200:
                log.error("CoC API %s for %s", resp.status, player_tag)
                return False
            data = await resp.json()
    clan = data.get("clan")
    return bool(clan) and clan.get("tag", "").upper() == CLAN_TAG.upper()


# ---------- /verify command ----------
@tree.command(name="verify", description="Verify yourself as a clan member by providing your player tag.")
@app_commands.describe(player_tag="Your Clash of Clans player tag, e.g. #ABCD123")
async def verify(interaction: discord.Interaction, player_tag: str):
    await interaction.response.defer(ephemeral=True)

    player_tag = player_tag.strip().upper()
    if not player_tag.startswith("#"):
        player_tag = "#" + player_tag

    if not await is_member_of_clan(player_tag):
        await interaction.followup.send("❌ Verification failed – you are not listed in the clan.", ephemeral=True)
        return

    role = interaction.guild.get_role(VERIFIED_ROLE_ID)
    if role is None:
        await interaction.followup.send("Setup error: verified role not found – contact an admin.", ephemeral=True)
        log.error("Verified role ID %s not found in guild %s", VERIFIED_ROLE_ID, interaction.guild.id)
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
        await interaction.followup.send("Unexpected Discord error – try again later.", ephemeral=True)
        log.exception("HTTPException adding role: %s", exc)
        return

    await interaction.followup.send("✅ Success! You now have access.", ephemeral=True)

    if (log_chan := await resolve_log_channel(interaction.guild)):
        try:
            await log_chan.send(f"{interaction.user.mention} verified with tag {player_tag}.")
        except discord.Forbidden:
            log.warning("No send permission in log channel %s", log_chan.id)
        except discord.HTTPException as exc:
            log.exception("Failed to log verification: %s", exc)


# ---------- Lifecycle ----------
@bot.event
async def on_ready():
    await tree.sync()
    log.info("Bot ready as %s (%s)", bot.user, bot.user.id)


def main() -> None:
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    bot.run(DISCORD_TOKEN)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
