import logging

import discord
from discord import app_commands
import openai

from .clients import bot, tree, table
from .config import CLAN_TAG, VERIFIED_ROLE_ID
from .utils import resolve_log_channel, get_player, normalize_town_hall

log = logging.getLogger("coc-gateway")


async def fetch_strategy(town_hall: str) -> str:
    resp = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "user",
                "content": (
                    f"I'm a {town_hall} in clash of clans. "
                    "What attack strategies can I use in war that are OP "
                    "that are current with the latest update for clash of clans?"
                ),
            }
        ],
    )
    return resp.choices[0].message.content.strip()


@tree.command(name="strat", description="Get current war strategies")
@app_commands.describe(town_hall="Your town hall level, e.g. TH10")
async def strat(interaction: discord.Interaction, town_hall: str):
    await interaction.response.defer()
    th = normalize_town_hall(town_hall)
    if th is None:
        await interaction.followup.send("Invalid Town Hall.")
        return
    try:
        content = await fetch_strategy(th)
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("OpenAI request failed: %s", exc)
        await interaction.followup.send("Failed to fetch strategy.")
        return
    await interaction.followup.send(content)


@tree.command(name="verify", description="Verify yourself as a clan member by providing your player tag.")
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

    if (log_chan := await resolve_log_channel(interaction.guild)):
        try:
            await log_chan.send(f"{interaction.user.mention} verified with tag {player_tag}.")
        except discord.Forbidden:
            log.warning("No send permission in log channel %s", log_chan.id)
        except discord.HTTPException as exc:
            log.exception("Failed to log verification: %s", exc)


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

    await interaction.followup.send(f"{member.display_name} is {item['player_name']}", ephemeral=True)
