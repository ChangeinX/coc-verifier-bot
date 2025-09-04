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
import uuid
from datetime import UTC, datetime
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
FEEDER_CLAN_TAG: Final[str | None] = os.getenv("FEEDER_CLAN_TAG")
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


# ---------- Member Removal Approval System ----------


class MemberRemovalView(discord.ui.View):
    def __init__(
        self,
        removal_id: str,
        discord_id: str,
        player_tag: str,
        player_name: str,
        reason: str,
    ):
        super().__init__(timeout=86400)  # 24 hours timeout
        self.removal_id = removal_id
        self.discord_id = discord_id
        self.player_tag = player_tag
        self.player_name = player_name
        self.reason = reason

    async def store_pending_removal(self) -> None:
        """Store the pending removal in DynamoDB."""
        if table is None:
            return
        try:
            table.put_item(
                Item={
                    "discord_id": f"PENDING_REMOVAL_{self.removal_id}",
                    "removal_id": self.removal_id,
                    "target_discord_id": self.discord_id,
                    "player_tag": self.player_tag,
                    "player_name": self.player_name,
                    "reason": self.reason,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "status": "PENDING",
                }
            )
            log.info(
                "Stored pending removal %s for user %s",
                self.removal_id,
                self.discord_id,
            )
        except Exception as exc:
            log.exception("Failed to store pending removal: %s", exc)

    async def remove_pending_removal(self) -> None:
        """Remove the pending removal from DynamoDB."""
        if table is None:
            return
        try:
            table.delete_item(Key={"discord_id": f"PENDING_REMOVAL_{self.removal_id}"})
            log.info("Removed pending removal %s", self.removal_id)
        except Exception as exc:
            log.exception("Failed to remove pending removal: %s", exc)

    @discord.ui.button(
        label="Approve Removal", style=discord.ButtonStyle.danger, emoji="✅"
    )
    async def approve_removal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Handle approval of member removal."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Error: Guild not found.", ephemeral=True)
            return

        # Get the member to be removed
        member = guild.get_member(int(self.discord_id))
        if member is None:
            await interaction.followup.send(
                f"❌ Member {self.discord_id} not found in server. They may have already left.",
                ephemeral=True,
            )
            await self.remove_pending_removal()
            # Disable buttons and update the embed
            for item in self.children:
                item.disabled = True
            if hasattr(interaction.message, "edit"):
                try:
                    embed = (
                        interaction.message.embeds[0]
                        if interaction.message.embeds
                        else None
                    )
                    if embed:
                        embed.color = discord.Color.red()
                        embed.add_field(
                            name="Result",
                            value=f"❌ Member not found (approved by {interaction.user.mention})",
                            inline=False,
                        )
                    await interaction.message.edit(embed=embed, view=self)
                except Exception as exc:
                    log.exception("Failed to update message after approval: %s", exc)
            return

        # Perform the removal actions
        kicked = False
        record_deleted = False

        # Try to kick the member
        try:
            await member.kick(
                reason=f"Left clan - approved by {interaction.user.name}: {self.reason}"
            )
            kicked = True
            log.info(
                "Kicked member %s (%s) - approved by %s",
                member,
                self.discord_id,
                interaction.user,
            )
        except discord.Forbidden:
            log.warning("Forbidden when trying to kick %s", member)
        except discord.HTTPException as exc:
            log.exception("Failed to kick %s: %s", member, exc)

        # Try to delete the verification record
        if table is not None:
            try:
                table.delete_item(Key={"discord_id": self.discord_id})
                record_deleted = True
                log.info(
                    "Deleted verification record for %s - approved by %s",
                    self.discord_id,
                    interaction.user,
                )
            except Exception as exc:
                log.exception(
                    "Failed to delete verification record for %s: %s",
                    self.discord_id,
                    exc,
                )

        # Remove the pending removal
        await self.remove_pending_removal()

        # Update the interaction
        result_parts = []
        if kicked:
            result_parts.append("✅ Member kicked")
        else:
            result_parts.append("⚠️ Could not kick member")

        if record_deleted:
            result_parts.append("✅ Record deleted")
        else:
            result_parts.append("⚠️ Could not delete record")

        result_text = " | ".join(result_parts)
        await interaction.followup.send(
            f"**Approved removal of {member.mention}**\n{result_text}", ephemeral=True
        )

        # Disable buttons and update the embed
        for item in self.children:
            item.disabled = True

        if hasattr(interaction.message, "edit"):
            try:
                embed = (
                    interaction.message.embeds[0]
                    if interaction.message.embeds
                    else None
                )
                if embed:
                    embed.color = discord.Color.green()
                    embed.add_field(
                        name="Result",
                        value=f"✅ Approved by {interaction.user.mention}\n{result_text}",
                        inline=False,
                    )
                await interaction.message.edit(embed=embed, view=self)
            except Exception as exc:
                log.exception("Failed to update message after approval: %s", exc)

    @discord.ui.button(
        label="Deny Removal", style=discord.ButtonStyle.secondary, emoji="❌"
    )
    async def deny_removal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Handle denial of member removal."""
        await interaction.response.defer(ephemeral=True)

        # Remove the pending removal
        await self.remove_pending_removal()

        log.info(
            "Denied removal of %s (%s) - denied by %s",
            self.player_name,
            self.discord_id,
            interaction.user,
        )
        await interaction.followup.send(
            f"**Denied removal of {self.player_name}**", ephemeral=True
        )

        # Disable buttons and update the embed
        for item in self.children:
            item.disabled = True

        if hasattr(interaction.message, "edit"):
            try:
                embed = (
                    interaction.message.embeds[0]
                    if interaction.message.embeds
                    else None
                )
                if embed:
                    embed.color = discord.Color.yellow()
                    embed.add_field(
                        name="Result",
                        value=f"❌ Denied by {interaction.user.mention}",
                        inline=False,
                    )
                await interaction.message.edit(embed=embed, view=self)
            except Exception as exc:
                log.exception("Failed to update message after denial: %s", exc)

    async def on_timeout(self) -> None:
        """Handle view timeout."""
        await self.remove_pending_removal()
        log.info("Removal request %s timed out", self.removal_id)


async def send_removal_approval_request(
    guild: discord.Guild,
    member: discord.Member,
    player_tag: str,
    player_name: str,
    reason: str,
) -> None:
    """Send a member removal approval request to the admin log channel."""
    log_chan = await resolve_log_channel(guild)
    if log_chan is None:
        log.warning(
            "No admin log channel configured - cannot send removal approval request"
        )
        return

    removal_id = uuid.uuid4().hex[:8]  # Short ID for easier reference
    view = MemberRemovalView(
        removal_id, str(member.id), player_tag, player_name, reason
    )

    # Store the pending removal
    await view.store_pending_removal()

    # Create the approval request embed
    embed = discord.Embed(
        title="🚨 Member Removal Request",
        description=f"Member **{member.display_name}** ({member.mention}) needs approval for removal.",
        color=discord.Color.orange(),
        timestamp=datetime.now(UTC),
    )

    embed.add_field(
        name="Discord User", value=f"{member.mention} ({member.id})", inline=True
    )
    embed.add_field(
        name="CoC Player", value=f"{player_name} ({player_tag})", inline=True
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Request ID", value=removal_id, inline=True)
    embed.add_field(
        name="Requested",
        value=f"<t:{int(datetime.now(UTC).timestamp())}:R>",
        inline=True,
    )

    embed.set_footer(text="This request will expire in 24 hours")

    try:
        await log_chan.send(embed=embed, view=view)
        log.info(
            "Sent removal approval request for %s (%s) - ID: %s",
            member,
            player_tag,
            removal_id,
        )
    except discord.Forbidden:
        log.warning("No send permission in log channel %s", log_chan.id)
    except discord.HTTPException as exc:
        log.exception("Failed to send removal approval request: %s", exc)


async def cleanup_expired_pending_removals() -> None:
    """Clean up expired pending removal requests (older than 24 hours)."""
    if table is None:
        return

    try:
        # Scan for pending removals
        response = table.scan()
        expired_count = 0
        cutoff_time = datetime.now(UTC).timestamp() - 86400  # 24 hours ago

        for item in response.get("Items", []):
            discord_id = item.get("discord_id", "")
            if discord_id.startswith("PENDING_REMOVAL_"):
                timestamp_str = item.get("timestamp")
                if timestamp_str:
                    try:
                        timestamp = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )
                        if timestamp.timestamp() < cutoff_time:
                            # This removal request has expired
                            table.delete_item(Key={"discord_id": discord_id})
                            expired_count += 1
                            log.info(
                                "Cleaned up expired pending removal: %s",
                                item.get("removal_id", "unknown"),
                            )
                    except (ValueError, AttributeError) as exc:
                        log.warning(
                            "Invalid timestamp in pending removal %s: %s",
                            discord_id,
                            exc,
                        )

        if expired_count > 0:
            log.info("Cleaned up %d expired pending removal requests", expired_count)

    except Exception as exc:
        log.exception("Failed to cleanup expired pending removals: %s", exc)


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
    player_clan_tag = player.clan.tag.upper()
    if CLAN_TAG and player_clan_tag == CLAN_TAG.upper():
        return True
    if FEEDER_CLAN_TAG and player_clan_tag == FEEDER_CLAN_TAG.upper():
        return True
    return False


async def get_player_clan_tag(player_tag: str) -> str | None:
    """Return the clan tag the player belongs to (main or feeder), or None if not a member."""
    player = await get_player(player_tag)
    if not player or not player.clan:
        return None
    player_clan_tag = player.clan.tag.upper()
    if CLAN_TAG and player_clan_tag == CLAN_TAG.upper():
        return CLAN_TAG.upper()
    if FEEDER_CLAN_TAG and player_clan_tag == FEEDER_CLAN_TAG.upper():
        return FEEDER_CLAN_TAG.upper()
    return None


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

    player_clan_tag = await get_player_clan_tag(player_tag)
    if player_clan_tag is None:
        await interaction.followup.send(
            "❌ Verification failed – you are not listed in any of our clans.",
            ephemeral=True,
        )
        return

    player = await get_player(player_tag)

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
                    "clan_tag": player_clan_tag,
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
        current_clan_tag = await get_player_clan_tag(item["player_tag"])
        stored_clan_tag = item.get("clan_tag")

        if current_clan_tag is None:
            # Send approval request for member removal
            reason = f"Player {item.get('player_name', 'Unknown')} ({item['player_tag']}) is no longer in any clan"
            try:
                await send_removal_approval_request(
                    guild,
                    member,
                    item["player_tag"],
                    item.get("player_name", "Unknown"),
                    reason,
                )
            except Exception as exc:  # pylint: disable=broad-except
                log.exception(
                    "Failed to send removal approval request for %s: %s", member, exc
                )
        elif stored_clan_tag and current_clan_tag != stored_clan_tag:
            try:
                table.update_item(
                    Key={"discord_id": item["discord_id"]},
                    UpdateExpression="SET clan_tag = :new_clan_tag",
                    ExpressionAttributeValues={":new_clan_tag": current_clan_tag},
                )
                log.info(
                    "Updated clan tag for %s from %s to %s",
                    member,
                    stored_clan_tag,
                    current_clan_tag,
                )
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Failed to update clan tag for %s: %s", member, exc)


# ---------- Lifecycle ----------
@bot.event
async def on_ready():
    await tree.sync()
    await coc_client.login(COC_EMAIL, COC_PASSWORD)

    # Clean up any expired pending removals on startup
    await cleanup_expired_pending_removals()

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
