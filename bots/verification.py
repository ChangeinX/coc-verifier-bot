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
from functools import partial
from typing import Final

import boto3
import coc
import discord
from discord import app_commands
from discord.ext import tasks

from bots.config import ShadowConfig, read_shadow_config
from bots.shadow import ShadowReporter
from verifier_bot import approvals, coc_api, logging_utils

# ---------- Constants ----------
MEMBERSHIP_CHECK_INTERVAL_MINUTES: Final[int] = 5
APPROVAL_TIMEOUT_HOURS: Final[int] = 24
APPROVAL_TIMEOUT_SECONDS: Final[int] = APPROVAL_TIMEOUT_HOURS * 3600

# ---------- Environment ----------
DISCORD_TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
COC_EMAIL: Final[str | None] = os.getenv("COC_EMAIL")
COC_PASSWORD: Final[str | None] = os.getenv("COC_PASSWORD")
CLAN_TAG: Final[str | None] = os.getenv("CLAN_TAG")
FEEDER_CLAN_TAG: Final[str | None] = os.getenv("FEEDER_CLAN_TAG")
VERIFIED_ROLE_ID: Final[int | None] = (
    int(os.getenv("VERIFIED_ROLE_ID")) if os.getenv("VERIFIED_ROLE_ID") else None
)
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

_shadow_config = read_shadow_config(default_enabled=False)
shadow_reporter = ShadowReporter(bot, _shadow_config)

# ---------- AWS / CoC clients ----------
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DDB_TABLE_NAME) if DDB_TABLE_NAME else None

coc_client = None

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("coc-gateway")


async def resolve_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Thin wrapper delegating to logging_utils.resolve_log_channel."""
    return await logging_utils.resolve_log_channel(bot, ADMIN_LOG_CHANNEL_ID, guild)


# ---------- Member Removal Approval System ----------


class MemberRemovalView(approvals.MemberRemovalViewBase):
    """Thin wrapper to inject the module table into the generic view."""

    def __init__(
        self,
        removal_id: str,
        discord_id: str,
        player_tag: str,
        player_name: str,
        reason: str,
    ) -> None:
        super().__init__(
            lambda: table, removal_id, discord_id, player_tag, player_name, reason
        )

    # Expose methods on this class for tests that access MemberRemovalView.__dict__
    async def approve_removal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        return await super().approve_removal(interaction, button)

    async def deny_removal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        return await super().deny_removal(interaction, button)


async def send_removal_approval_request(
    guild: discord.Guild,
    member: discord.Member,
    player_tag: str,
    player_name: str,
    reason: str,
) -> None:
    if shadow_reporter.enabled:
        embed = discord.Embed(
            title="Shadow Removal Request",
            description=(
                f"{member.mention} ({member.id}) would be removed for {reason}."
            ),
            color=discord.Color.orange(),
        )
        await shadow_reporter.report(
            guild,
            f"[verify] simulated removal approval for {member.id}",
            embeds=[embed],
        )
        return

    return await approvals.send_removal_approval_request(
        guild,
        member,
        player_tag,
        player_name,
        reason,
        resolve_log_channel=resolve_log_channel,
        table=table,
    )


async def cleanup_expired_pending_removals() -> None:
    if shadow_reporter.enabled:
        return
    return await approvals.cleanup_expired_pending_removals(table)


async def has_pending_removal(target_discord_id: str) -> bool:
    if shadow_reporter.enabled:
        return False
    return await approvals.has_pending_removal(table, target_discord_id)


# ---------- Clash API ----------


async def get_player(player_tag: str) -> coc.Player | None:
    return await coc_api.get_player_with_retry(
        coc_client, COC_EMAIL, COC_PASSWORD, player_tag
    )


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
    player = await get_player(player_tag)
    if not player or not player.clan:
        return None
    player_clan_tag = player.clan.tag.upper()
    if CLAN_TAG and player_clan_tag == CLAN_TAG.upper():
        return CLAN_TAG.upper()
    if FEEDER_CLAN_TAG and player_clan_tag == FEEDER_CLAN_TAG.upper():
        return FEEDER_CLAN_TAG.upper()
    return None


# ---------- Helpers ----------
def normalize_player_tag(tag: str) -> str:
    """Normalize a user-provided tag to Clash format (uppercase, prefixed with #)."""
    tag = tag.strip().upper()
    if not tag.startswith("#"):
        tag = "#" + tag
    return tag


def player_deep_link(tag: str) -> str:
    """Build the official Clash deep link for a player profile."""
    return "https://link.clashofclans.com/?action=OpenPlayerProfile&tag=" + tag.lstrip(
        "#"
    )


async def cancel_pending_removal_message(
    guild: discord.Guild, removal_item: dict
) -> None:
    if shadow_reporter.enabled:
        return
    """Delete the Discord approval message associated with a removal request."""

    channel: discord.abc.GuildChannel | None = None
    channel_id = removal_item.get("channel_id")
    message_id = removal_item.get("message_id")
    removal_id = removal_item.get("removal_id")

    if channel_id:
        int_channel_id = int(channel_id)
        channel = guild.get_channel(int_channel_id) or bot.get_channel(int_channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(int_channel_id)
            except (discord.NotFound, discord.Forbidden):
                channel = None
            except discord.HTTPException as exc:  # pylint: disable=broad-except
                log.exception(
                    "Failed to fetch channel %s for pending removal cleanup: %s",
                    channel_id,
                    exc,
                )
                return

    if channel is None:
        channel = await resolve_log_channel(guild)

    if channel is None or not hasattr(channel, "fetch_message"):
        log.debug("No channel available to clean up pending removal %s", removal_id)
        return

    target_message: discord.Message | None = None

    if message_id:
        try:
            target_message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            target_message = None
        except discord.Forbidden:
            log.warning("No permission to fetch pending removal message %s", message_id)
            return
        except discord.HTTPException as exc:  # pylint: disable=broad-except
            log.exception(
                "Failed to fetch pending removal message %s: %s", message_id, exc
            )
            return

    if target_message is None and removal_id:
        try:
            async for candidate in channel.history(limit=100):
                for embed in candidate.embeds:
                    for field in embed.fields:
                        if field.name == "Request ID" and field.value == removal_id:
                            target_message = candidate
                            break
                    if target_message:
                        break
                if target_message:
                    break
        except AttributeError:
            target_message = None
        except discord.Forbidden:
            log.warning(
                "No permission to inspect history for pending removal %s", removal_id
            )
            return
        except discord.HTTPException as exc:  # pylint: disable=broad-except
            log.exception(
                "Failed to search log channel for pending removal %s: %s",
                removal_id,
                exc,
            )
            return

    if target_message is None:
        log.debug("Pending removal message not found for removal %s", removal_id)
        return

    try:
        await target_message.delete()
        log.info(
            "Deleted pending removal message for %s (removal %s)",
            removal_item.get("target_discord_id"),
            removal_id or target_message.id,
        )
    except discord.NotFound:
        log.debug("Pending removal message already deleted for removal %s", removal_id)
    except discord.Forbidden:
        log.warning(
            "No permission to delete pending removal message %s", target_message.id
        )
    except discord.HTTPException as exc:  # pylint: disable=broad-except
        log.exception(
            "Failed to delete pending removal message %s: %s",
            target_message.id,
            exc,
        )


# ---------- /verify command ----------
@tree.command(
    name="verify",
    description="Verify yourself as a clan member by providing your player tag.",
)
@app_commands.describe(player_tag="Your Clash of Clans player tag, e.g. #ABCD123")
async def verify(interaction: discord.Interaction, player_tag: str) -> None:
    await interaction.response.defer(ephemeral=True)

    player_tag = player_tag.strip().upper()
    if not player_tag.startswith("#"):
        player_tag = "#" + player_tag

    # Get player info once and determine clan membership
    player = await get_player(player_tag)
    if not player:
        await interaction.followup.send(
            "❌ Verification failed – player not found or CoC API unavailable.",
            ephemeral=True,
        )
        return

    # Check if player is in main or feeder clan
    player_clan_tag = None
    if player.clan:
        player_clan_tag_upper = player.clan.tag.upper()
        if CLAN_TAG and player_clan_tag_upper == CLAN_TAG.upper():
            player_clan_tag = CLAN_TAG.upper()
        elif FEEDER_CLAN_TAG and player_clan_tag_upper == FEEDER_CLAN_TAG.upper():
            player_clan_tag = FEEDER_CLAN_TAG.upper()

    if player_clan_tag is None:
        await interaction.followup.send(
            "❌ Verification failed – you are not listed in any of our clans.",
            ephemeral=True,
        )
        return

    if VERIFIED_ROLE_ID is None:
        await interaction.followup.send(
            "Setup error: verified role not configured – contact an admin.",
            ephemeral=True,
        )
        log.error("VERIFIED_ROLE_ID environment variable not set")
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

    if shadow_reporter.enabled:
        await interaction.followup.send(
            "✅ Verification recorded in shadow mode. No roles were changed.",
            ephemeral=True,
        )
        embed = discord.Embed(
            title="Shadow Verification",
            description=f"{interaction.user.mention} would be verified.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Player Tag", value=player_tag, inline=True)
        embed.add_field(name="Clan", value=player_clan_tag, inline=True)
        await shadow_reporter.report(
            interaction.guild,
            "[verify] simulated verification",
            embeds=[embed],
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
async def whois(interaction: discord.Interaction, member: discord.Member) -> None:
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


# ---------- /recruited command ----------
@tree.command(
    name="recruited",
    description="Announce a recruited player with tag and source.",
)
@app_commands.describe(
    player_tag="Player tag, e.g. #ABC123",
    source="Where the player was found",
)
@app_commands.choices(
    source=[
        app_commands.Choice(name="Discord", value="Discord"),
        app_commands.Choice(name="Reddit", value="Reddit"),
        app_commands.Choice(name="Data scrape", value="Data scrape"),
    ]
)
async def recruit(
    interaction: discord.Interaction,
    player_tag: str,
    source: app_commands.Choice[str],
) -> None:
    """Post a public, nicely formatted recruit announcement."""
    tag = normalize_player_tag(player_tag)
    link = player_deep_link(tag)

    embed = discord.Embed(
        title="🎯 New Recruit",
        description=f"{interaction.user.mention} reported a successful recruit!",
        color=discord.Color.green(),
    )
    embed.add_field(name="Player Tag", value=f"`{tag}`", inline=True)
    embed.add_field(name="Source", value=source.value, inline=True)
    embed.add_field(name="Deep Link", value=f"[Open Profile]({link})", inline=False)
    embed.set_footer(text="Reported via /recruited")

    await interaction.response.send_message(embed=embed)


# ---------- Clan membership check ----------
@tasks.loop(minutes=MEMBERSHIP_CHECK_INTERVAL_MINUTES)
async def membership_check() -> None:
    if shadow_reporter.enabled:
        await shadow_reporter.report(None, "[membership_check] skipped (shadow mode)")
        return

    log.info("Membership check started...")
    if table is None:
        log.warning("Membership check skipped: database table not configured")
        return

    if not bot.guilds:
        log.warning("Membership check skipped: bot not in any guilds")
        return

    # Process all guilds the bot is in
    for guild in bot.guilds:
        log.debug(f"Processing membership check for guild {guild.name} ({guild.id})")
        removal_cleanup = partial(cancel_pending_removal_message, guild)

        try:
            data = table.scan()
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to scan table: %s", exc)
            continue  # Continue to next guild instead of returning

        for item in data.get("Items", []):
            discord_id_str = item["discord_id"]

            # Skip non-user entries (pending removals, metadata, etc.)
            if not discord_id_str.isdigit():
                continue

            discord_id = int(discord_id_str)
            member = guild.get_member(discord_id)
            if member is None:
                continue

            fetch_result = await coc_api.fetch_player_with_status(
                coc_client,
                COC_EMAIL,
                COC_PASSWORD,
                item["player_tag"],
            )

            if fetch_result.status == "access_denied":
                log.error(
                    "Skipping membership check for %s due to CoC API access denial",
                    item["player_tag"],
                )
                await approvals.clear_pending_removals_for_target(
                    table,
                    discord_id_str,
                    on_remove=removal_cleanup,
                )
                continue

            if fetch_result.status == "error":
                log.error(
                    "Skipping membership check for %s due to CoC API error",
                    item["player_tag"],
                )
                continue

            player = fetch_result.player
            log.info(
                "Player %s (%s) in clan %s",
                player,
                item["player_tag"],
                player.clan.tag if player and player.clan else "None",
            )

            current_clan_tag = None
            if player and player.clan:
                player_clan_tag = player.clan.tag.upper()
                if CLAN_TAG and player_clan_tag == CLAN_TAG.upper():
                    current_clan_tag = CLAN_TAG.upper()
                elif FEEDER_CLAN_TAG and player_clan_tag == FEEDER_CLAN_TAG.upper():
                    current_clan_tag = FEEDER_CLAN_TAG.upper()

            stored_clan_tag = item.get("clan_tag")
            pending_exists = await has_pending_removal(item["discord_id"])

            if current_clan_tag is None:
                # Check if there's already a pending removal request for this member
                if pending_exists:
                    log.info(
                        "Skipping duplicate removal request for %s - already pending",
                        member,
                    )
                    continue

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
                        "Failed to send removal approval request for %s: %s",
                        member,
                        exc,
                    )
            else:
                if pending_exists:
                    await approvals.clear_pending_removals_for_target(
                        table,
                        item["discord_id"],
                        on_remove=removal_cleanup,
                    )
                    log.info(
                        "Cleared stale pending removal for %s (%s) - player still in clan",
                        member,
                        item["player_tag"],
                    )

                if stored_clan_tag and current_clan_tag != stored_clan_tag:
                    try:
                        table.update_item(
                            Key={"discord_id": item["discord_id"]},
                            UpdateExpression="SET clan_tag = :new_clan_tag",
                            ExpressionAttributeValues={
                                ":new_clan_tag": current_clan_tag
                            },
                        )
                        log.info(
                            "Updated clan tag for %s from %s to %s",
                            member,
                            stored_clan_tag,
                            current_clan_tag,
                        )
                    except Exception as exc:  # pylint: disable=broad-except
                        log.exception(
                            "Failed to update clan tag for %s: %s", member, exc
                        )


# ---------- Lifecycle ----------
@bot.event
async def on_ready() -> None:
    await tree.sync()

    # Log startup without credentials for security
    if shadow_reporter.enabled:
        await shadow_reporter.report(None, "[verify] on_ready shadow mode active")
    else:
        log.info("Signing in to CoC API...")
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


def configure_runtime(
    *,
    client: discord.Client | None = None,
    command_tree: app_commands.CommandTree | None = None,
    dynamodb_resource=None,
    table_name: str | None = None,
    coc_client_override: coc.Client | None = None,
    shadow_enabled: bool | None = None,
    shadow_channel_id: int | None = None,
) -> None:
    """Reconfigure module globals for the unified bot runtime."""

    global bot, tree, dynamodb, table, coc_client, shadow_reporter, _shadow_config

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

    if table_name is not None:
        table = dynamodb.Table(table_name)

    if coc_client_override is not None:
        coc_client = coc_client_override
    elif coc_client is None:
        coc_client = coc.Client()

    if shadow_enabled is not None or shadow_channel_id is not None:
        _shadow_config = ShadowConfig(
            enabled=shadow_enabled
            if shadow_enabled is not None
            else _shadow_config.enabled,
            channel_id=shadow_channel_id
            if shadow_channel_id is not None
            else _shadow_config.channel_id,
        )

    shadow_reporter = ShadowReporter(bot, _shadow_config)


if __name__ == "__main__":
    asyncio.run(main())
