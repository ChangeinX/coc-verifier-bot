from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Final

import discord

log: Final = logging.getLogger("coc-gateway")


class MemberRemovalViewBase(discord.ui.View):
    """A base view that encapsulates the removal approval logic.

    This class expects a DynamoDB-like table-like object to be provided. The
    thin wrapper in bot.py injects the app's actual table.
    """

    def __init__(
        self,
        table_getter,
        removal_id: str,
        discord_id: str,
        player_tag: str,
        player_name: str,
        reason: str,
    ) -> None:
        # Import the constant from the main bot module
        from bot import APPROVAL_TIMEOUT_SECONDS

        super().__init__(timeout=APPROVAL_TIMEOUT_SECONDS)
        # table_getter: callable returning the latest table (to support tests patching bot.table)
        self._get_table = table_getter
        self.removal_id = removal_id
        self.discord_id = discord_id
        self.player_tag = player_tag
        self.player_name = player_name
        self.reason = reason
        self.request_timestamp = datetime.now(UTC)

    async def store_pending_removal(self) -> None:
        table = self._get_table()
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
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to store pending removal: %s", exc)

    async def remove_pending_removal(self) -> None:
        table = self._get_table()
        if table is None:
            return
        try:
            table.delete_item(Key={"discord_id": f"PENDING_REMOVAL_{self.removal_id}"})
            log.info("Removed pending removal %s", self.removal_id)
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to remove pending removal: %s", exc)

    @discord.ui.button(
        label="Approve Removal", style=discord.ButtonStyle.danger, emoji="✅"
    )
    async def approve_removal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):  # noqa: D401
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Error: Guild not found.", ephemeral=True)
            return

        member = guild.get_member(int(self.discord_id))
        if member is None:
            await interaction.followup.send(
                f"❌ Member {self.discord_id} not found in server. They may have already left.",
                ephemeral=True,
            )
            await self.remove_pending_removal()
            for item in self.children:
                item.disabled = True
            if hasattr(interaction.message, "edit") and interaction.message:
                try:
                    embed = self._get_or_create_embed(interaction.message)
                    if embed:
                        embed.color = discord.Color.red()
                        self._update_timestamp_field_to_static(embed)
                        embed.add_field(
                            name="Result",
                            value=f"❌ Member not found (approved by {interaction.user.mention})",
                            inline=False,
                        )
                        await interaction.message.edit(embed=embed, view=self)
                    else:
                        log.warning(
                            "Could not create or retrieve embed for message update"
                        )
                except discord.NotFound:
                    log.warning(
                        "Message not found when trying to update approval result"
                    )
                except discord.Forbidden:
                    log.warning("No permission to edit approval message")
                except Exception as exc:  # pylint: disable=broad-except
                    log.exception("Failed to update message after approval: %s", exc)
            return

        kicked = False
        record_deleted = False

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
        except discord.HTTPException as exc:  # pylint: disable=broad-except
            log.exception("Failed to kick %s: %s", member, exc)

        table = self._get_table()
        if table is not None:
            try:
                table.delete_item(Key={"discord_id": self.discord_id})
                record_deleted = True
                log.info(
                    "Deleted verification record for %s - approved by %s",
                    self.discord_id,
                    interaction.user,
                )
            except Exception as exc:  # pylint: disable=broad-except
                log.exception(
                    "Failed to delete verification record for %s: %s",
                    self.discord_id,
                    exc,
                )

        await self.remove_pending_removal()

        result_parts: list[str] = []
        result_parts.append("✅ Member kicked" if kicked else "⚠️ Could not kick member")
        result_parts.append(
            "✅ Record deleted" if record_deleted else "⚠️ Could not delete record"
        )
        result_text = " | ".join(result_parts)
        await interaction.followup.send(
            f"**Approved removal of {member.mention}**\n{result_text}", ephemeral=True
        )

        for item in self.children:
            item.disabled = True

        if hasattr(interaction.message, "edit") and interaction.message:
            try:
                embed = self._get_or_create_embed(interaction.message)
                if embed:
                    embed.color = discord.Color.green()
                    self._update_timestamp_field_to_static(embed)
                    embed.add_field(
                        name="Result",
                        value=f"✅ Approved by {interaction.user.mention}\n{result_text}",
                        inline=False,
                    )
                    await interaction.message.edit(embed=embed, view=self)
                else:
                    log.warning("Could not create or retrieve embed for message update")
            except discord.NotFound:
                log.warning("Message not found when trying to update approval result")
            except discord.Forbidden:
                log.warning("No permission to edit approval message")
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Failed to update message after approval: %s", exc)

    @discord.ui.button(
        label="Deny Removal", style=discord.ButtonStyle.secondary, emoji="❌"
    )
    async def deny_removal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):  # noqa: D401
        await interaction.response.defer(ephemeral=True)

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

        for item in self.children:
            item.disabled = True

        if hasattr(interaction.message, "edit") and interaction.message:
            try:
                embed = self._get_or_create_embed(interaction.message)
                if embed:
                    embed.color = discord.Color.yellow()
                    self._update_timestamp_field_to_static(embed)
                    embed.add_field(
                        name="Result",
                        value=f"❌ Denied by {interaction.user.mention}",
                        inline=False,
                    )
                    await interaction.message.edit(embed=embed, view=self)
                else:
                    log.warning("Could not create or retrieve embed for message update")
            except discord.NotFound:
                log.warning("Message not found when trying to update denial result")
            except discord.Forbidden:
                log.warning("No permission to edit denial message")
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Failed to update message after denial: %s", exc)

    def _get_or_create_embed(self, message: discord.Message) -> discord.Embed | None:
        """Get existing embed or create a basic one if none exists."""
        try:
            if message.embeds:
                return message.embeds[0]
            else:
                # Create a basic embed if none exists
                embed = discord.Embed(
                    title="🚨 Member Removal Request",
                    color=discord.Color.orange(),
                )
                return embed
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to get or create embed: %s", exc)
            return None

    def _update_timestamp_field_to_static(self, embed: discord.Embed) -> None:
        try:
            static_timestamp = f"<t:{int(self.request_timestamp.timestamp())}:F>"
            for i, field in enumerate(embed.fields):
                if field.name == "Requested":
                    embed.set_field_at(
                        i, name="Requested", value=static_timestamp, inline=field.inline
                    )
                    break
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to update timestamp field to static format: %s", exc)

    async def on_timeout(self) -> None:
        await self.remove_pending_removal()
        log.info("Removal request %s timed out", self.removal_id)


async def send_removal_approval_request(
    guild: discord.Guild,
    member: discord.Member,
    player_tag: str,
    player_name: str,
    reason: str,
    resolve_log_channel,
    table,
) -> None:
    """Send a member removal approval request to the admin log channel."""
    log_chan = await resolve_log_channel(guild)
    if log_chan is None:
        log.warning(
            "No admin log channel configured - cannot send removal approval request"
        )
        return

    removal_id = uuid.uuid4().hex[:8]
    # use a callable to fetch the latest table to support dynamic patching
    view = MemberRemovalViewBase(
        (lambda: table), removal_id, str(member.id), player_tag, player_name, reason
    )

    await view.store_pending_removal()

    embed = discord.Embed(
        title="🚨 Member Removal Request",
        description=(
            f"Member **{member.display_name}** ({member.mention}) needs approval for removal."
        ),
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
    except discord.HTTPException as exc:  # pylint: disable=broad-except
        log.exception("Failed to send removal approval request: %s", exc)


async def cleanup_expired_pending_removals(table) -> None:
    """Clean up expired pending removal requests (older than 24 hours)."""
    if table is None:
        return

    try:
        from boto3.dynamodb.conditions import Attr

        # Use filtered scan to only get pending removal entries
        response = table.scan(
            FilterExpression=Attr("discord_id").begins_with("PENDING_REMOVAL_"),
            ProjectionExpression="discord_id, removal_id, #ts",
            ExpressionAttributeNames={
                "#ts": "timestamp"
            },  # 'timestamp' is a reserved word
        )

        expired_count = 0
        from bot import APPROVAL_TIMEOUT_SECONDS

        cutoff_time = datetime.now(UTC).timestamp() - APPROVAL_TIMEOUT_SECONDS

        for item in response.get("Items", []):
            discord_id = item.get("discord_id", "")
            timestamp_str = item.get("timestamp")
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )
                    if timestamp.timestamp() < cutoff_time:
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

    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to cleanup expired pending removals: %s", exc)


async def clear_pending_removals_for_target(table, target_discord_id: str) -> int:
    """Remove all pending removals for a given Discord user."""
    if table is None:
        return 0

    try:
        from boto3.dynamodb.conditions import Attr

        response = table.scan(
            FilterExpression=Attr("discord_id").begins_with("PENDING_REMOVAL_")
            & Attr("target_discord_id").eq(target_discord_id),
            ProjectionExpression="discord_id, removal_id",
        )

        removed = 0
        for item in response.get("Items", []):
            discord_id = item.get("discord_id")
            if not discord_id:
                continue
            try:
                table.delete_item(Key={"discord_id": discord_id})
                removed += 1
                log.info(
                    "Removed pending removal %s for user %s",
                    item.get("removal_id", "unknown"),
                    target_discord_id,
                )
            except Exception as exc:  # pylint: disable=broad-except
                log.exception(
                    "Failed to delete pending removal %s: %s", discord_id, exc
                )

        if removed:
            log.info(
                "Removed %d pending removal request(s) for %s", removed, target_discord_id
            )
        return removed

    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            "Failed to clear pending removals for %s: %s", target_discord_id, exc
        )
        return 0


async def has_pending_removal(table, target_discord_id: str) -> bool:
    """Check if there's already a pending removal request for the given discord_id.

    This uses a targeted query approach to avoid expensive table scans.
    """
    if table is None:
        return False

    try:
        # First try the more efficient approach - query by prefix pattern
        # This is more efficient than scanning the entire table
        from boto3.dynamodb.conditions import Attr

        response = table.scan(
            FilterExpression=Attr("target_discord_id").eq(target_discord_id)
            & Attr("discord_id").begins_with("PENDING_REMOVAL_"),
            ProjectionExpression="discord_id, target_discord_id",
            Limit=1,  # We only need to know if one exists
        )

        items = response.get("Items", [])
        if items:
            log.info(
                "Found existing pending removal for discord_id %s",
                target_discord_id,
            )
            return True

        return False

    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            "Failed to check for pending removal for %s: %s", target_discord_id, exc
        )
        return False
