import asyncio
import logging
import os
from typing import Final

import aiohttp
import discord
from discord import app_commands

# ---------- Configuration from environment ----------
DISCORD_TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
COC_API_TOKEN: Final[str | None] = os.getenv("COC_API_TOKEN")
CLAN_TAG: Final[str | None] = os.getenv("CLAN_TAG")  # must start with "#"
VERIFIED_ROLE_ID: Final[int] = int(os.getenv("VERIFIED_ROLE_ID", "0"))
ADMIN_LOG_CHANNEL_ID: Final[int] = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0"))

REQUIRED_VARS = ("DISCORD_TOKEN", "COC_API_TOKEN", "CLAN_TAG", "VERIFIED_ROLE_ID")

# ---------- Discord setup ----------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # we need this to add roles

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("coc-gateway")

# ---------- Clash of Clans API helper ----------
COC_API_BASE = "https://api.clashofclans.com/v1"
HEADERS = {"Authorization": f"Bearer {COC_API_TOKEN}"}


async def is_member_of_clan(player_tag: str) -> bool:
    """Return True iff the player belongs to CLAN_TAG."""
    enc_tag = player_tag.upper().replace("#", "%23")
    url = f"{COC_API_BASE}/players/{enc_tag}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=HEADERS, timeout=10) as resp:
            if resp.status != 200:
                log.error("CoC API %s for %s", resp.status, player_tag)
                return False
            data = await resp.json()

    clan = data.get("clan")
    if not clan:
        return False
    # clan.tag from API does *include* the leading '#'
    return clan.get("tag", "").upper() == CLAN_TAG.upper()


# ---------- Slash command ----------
@tree.command(name="verify", description="Verify yourself as a clan member by providing your player tag.")
@app_commands.describe(player_tag="Your Clash of Clans player tag, e.g. #ABCD123")
async def verify(interaction: discord.Interaction, player_tag: str):
    await interaction.response.defer(ephemeral=True)

    player_tag = player_tag.strip().upper()
    if not player_tag.startswith("#"):
        player_tag = "#" + player_tag

    if not await is_member_of_clan(player_tag):
        await interaction.followup.send("❌ Verification failed. You are not listed in the clan.", ephemeral=True)
        return

    role = interaction.guild.get_role(VERIFIED_ROLE_ID)
    if role is None:
        await interaction.followup.send("Role not found – please tell an admin.", ephemeral=True)
        return

    await interaction.user.add_roles(role, reason="Passed CoC clan verification")
    await interaction.followup.send("✅ Success! You now have access.", ephemeral=True)

    if ADMIN_LOG_CHANNEL_ID:
        channel = interaction.guild.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            await channel.send(f"{interaction.user.mention} verified with tag {player_tag}.")


# ---------- Bot lifecycle ----------
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
