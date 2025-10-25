from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Final

import discord

PENDING_REMOVAL_PREFIX: Final[str] = "PENDING_REMOVAL_"
DENIED_REMOVAL_PREFIX: Final[str] = "REMOVAL_DENIED_"
REMOVAL_REQUEST_PREFIX: Final[str] = "REMOVAL_REQUEST_"


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
                    "discord_id": f"{PENDING_REMOVAL_PREFIX}{self.removal_id}",
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
            table.delete_item(
                Key={"discord_id": f"{PENDING_REMOVAL_PREFIX}{self.removal_id}"}
            )
            log.info("Removed pending removal %s", self.removal_id)
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to remove pending removal: %s", exc)

    async def record_message_details(self, message: discord.Message) -> None:
        """Persist Discord message metadata for later cleanup."""
        table = self._get_table()
        if table is None:
            return
        try:
            table.update_item(
                Key={"discord_id": f"{PENDING_REMOVAL_PREFIX}{self.removal_id}"},
                UpdateExpression="SET message_id = :msg_id, channel_id = :chan_id, guild_id = :guild_id",
                ExpressionAttributeValues={
                    ":msg_id": str(message.id),
                    ":chan_id": str(message.channel.id),
                    ":guild_id": str(message.guild.id) if message.guild else "",
                },
            )
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to record removal message details: %s", exc)

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
            await clear_removal_request_record(self._get_table(), self.discord_id)
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
        role_removed = False

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

        role_removed = await self._remove_verified_role(
            member,
            reason=f"Removal approved by {interaction.user}",
        )

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
        await clear_removal_request_record(table, self.discord_id)

        result_parts: list[str] = []
        result_parts.append("✅ Member kicked" if kicked else "⚠️ Could not kick member")
        result_parts.append(
            "✅ Role removed" if role_removed else "⚠️ Could not remove role"
        )
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
                log.exception("Failed to update approval message: %s", exc)

    @discord.ui.button(
        label="Remove Role",
        style=discord.ButtonStyle.primary,
        emoji="🛡️",
    )
    async def remove_role(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
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
            await clear_removal_request_record(self._get_table(), self.discord_id)
            self._disable_all_items()
            await self._update_message_with_result(
                interaction,
                embed_color=discord.Color.red(),
                result_text=f"❌ Member not found (role removal by {interaction.user.mention})",
            )
            return

        role_removed = await self._remove_verified_role(
            member,
            reason=f"Verified role removed by {interaction.user}",
        )

        record_deleted = False
        table = self._get_table()
        if table is not None:
            try:
                table.delete_item(Key={"discord_id": self.discord_id})
                record_deleted = True
                log.info(
                    "Deleted verification record for %s via role removal by %s",
                    self.discord_id,
                    interaction.user,
                )
            except Exception as exc:  # pylint: disable=broad-except
                log.exception(
                    "Failed to delete verification record for %s during role removal: %s",
                    self.discord_id,
                    exc,
                )

        await self.remove_pending_removal()
        await clear_removal_request_record(table, self.discord_id)

        self._disable_all_items()

        status_parts = [
            "✅ Role removed" if role_removed else "⚠️ Could not remove role",
            "✅ Record deleted" if record_deleted else "⚠️ Could not delete record",
        ]
        status_text = " | ".join(status_parts)

        await interaction.followup.send(
            f"**Removed verified role for {member.mention}**\n{status_text}",
            ephemeral=True,
        )

        await self._update_message_with_result(
            interaction,
            embed_color=discord.Color.blurple(),
            result_text=f"🛡️ Role removed by {interaction.user.mention}\n{status_text}",
        )

    @discord.ui.button(
        label="Deny Removal", style=discord.ButtonStyle.secondary, emoji="❌"
    )
    async def deny_removal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):  # noqa: D401
        await interaction.response.defer(ephemeral=True)

        await self.remove_pending_removal()
        await clear_removal_request_record(self._get_table(), self.discord_id)
        await record_denied_removal(
            self._get_table(),
            removal_id=self.removal_id,
            target_discord_id=self.discord_id,
            denied_by_id=str(interaction.user.id),
            denied_by_name=str(interaction.user),
        )

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

    async def _update_message_with_result(
        self,
        interaction: discord.Interaction,
        *,
        embed_color: discord.Color,
        result_text: str,
    ) -> None:
        if hasattr(interaction.message, "edit") and interaction.message:
            try:
                embed = self._get_or_create_embed(interaction.message)
                if embed:
                    embed.color = embed_color
                    self._update_timestamp_field_to_static(embed)
                    embed.add_field(
                        name="Result",
                        value=result_text,
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

    def _disable_all_items(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _remove_verified_role(
        self, member: discord.Member, *, reason: str
    ) -> bool:
        try:
            from bot import VERIFIED_ROLE_ID
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Failed to import VERIFIED_ROLE_ID: %s", exc)
            return False

        if VERIFIED_ROLE_ID is None:
            return False

        role = member.guild.get_role(VERIFIED_ROLE_ID)
        if role is None:
            log.warning(
                "Verified role %s not found in guild %s", VERIFIED_ROLE_ID, member.guild
            )
            return False
        if role not in member.roles:
            log.debug(
                "Member %s (%s) no longer has verified role %s",
                member,
                self.discord_id,
                VERIFIED_ROLE_ID,
            )
            return True

        try:
            await member.remove_roles(role, reason=reason)
            log.info(
                "Removed verified role %s from %s (%s)",
                VERIFIED_ROLE_ID,
                member,
                self.discord_id,
            )
            return True
        except discord.Forbidden:
            log.warning("Forbidden when trying to remove verified role from %s", member)
        except discord.HTTPException as exc:  # pylint: disable=broad-except
            log.exception("Failed to remove verified role from %s: %s", member, exc)
        return False

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

    target_discord_id = str(member.id)
    if await has_recent_removal_request(table, target_discord_id):
        log.info(
            "Skipping removal approval request for %s - already notified within 24h",
            member,
        )
        return

    removal_id = uuid.uuid4().hex[:8]
    # use a callable to fetch the latest table to support dynamic patching
    view = MemberRemovalViewBase(
        (lambda: table), removal_id, target_discord_id, player_tag, player_name, reason
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
        message = await log_chan.send(embed=embed, view=view)
        await view.record_message_details(message)
        await record_removal_request(
            table, target_discord_id=target_discord_id, removal_id=removal_id
        )
        log.info(
            "Sent removal approval request for %s (%s) - ID: %s",
            member,
            player_tag,
            removal_id,
        )
    except discord.Forbidden:
        await view.remove_pending_removal()
        log.warning("No send permission in log channel %s", log_chan.id)
    except discord.HTTPException as exc:  # pylint: disable=broad-except
        await view.remove_pending_removal()
        log.exception("Failed to send removal approval request: %s", exc)


async def cleanup_expired_pending_removals(table) -> None:
    """Clean up expired removal requests (pending or denied) older than 24 hours."""
    if table is None:
        return

    try:
        from boto3.dynamodb.conditions import Attr

        from bot import APPROVAL_TIMEOUT_SECONDS

        def cleanup(prefix: str, label: str) -> int:
            response = table.scan(
                FilterExpression=Attr("discord_id").begins_with(prefix),
                ProjectionExpression="discord_id, removal_id, #ts",
                ExpressionAttributeNames={"#ts": "timestamp"},
            )

            expired = 0
            for item in response.get("Items", []):
                discord_id = item.get("discord_id", "")
                timestamp = _parse_timestamp(item.get("timestamp"))

                if timestamp is None:
                    table.delete_item(Key={"discord_id": discord_id})
                    continue

                age_seconds = (datetime.now(UTC) - timestamp).total_seconds()
                if age_seconds >= APPROVAL_TIMEOUT_SECONDS:
                    table.delete_item(Key={"discord_id": discord_id})
                    expired += 1
                    log.info(
                        "Cleaned up expired %s removal: %s",
                        label,
                        item.get("removal_id", "unknown"),
                    )

            return expired

        expired_pending = cleanup(PENDING_REMOVAL_PREFIX, "pending")
        expired_denied = cleanup(DENIED_REMOVAL_PREFIX, "denied")

        total_expired = expired_pending + expired_denied
        if total_expired:
            log.info("Cleaned up %d expired removal record(s)", total_expired)

    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to cleanup expired removals: %s", exc)


async def clear_pending_removals_for_target(
    table,
    target_discord_id: str,
    *,
    on_remove=None,
) -> int:
    """Remove all pending removals for a given Discord user."""
    if table is None:
        return 0

    try:
        from boto3.dynamodb.conditions import Attr

        response = table.scan(
            FilterExpression=Attr("discord_id").begins_with(PENDING_REMOVAL_PREFIX)
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
                if on_remove:
                    await on_remove(item)
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
                "Removed %d pending removal request(s) for %s",
                removed,
                target_discord_id,
            )
        await clear_removal_request_record(table, target_discord_id)
        return removed

    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            "Failed to clear pending removals for %s: %s", target_discord_id, exc
        )
        return 0


async def record_removal_request(
    table,
    *,
    target_discord_id: str,
    removal_id: str,
) -> None:
    if table is None:
        return

    try:
        table.put_item(
            Item={
                "discord_id": f"{REMOVAL_REQUEST_PREFIX}{target_discord_id}",
                "target_discord_id": target_discord_id,
                "removal_id": removal_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "status": "NOTIFIED",
            }
        )
    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            "Failed to record removal notification for %s: %s",
            target_discord_id,
            exc,
        )


async def clear_removal_request_record(table, target_discord_id: str) -> None:
    if table is None:
        return

    key = {"discord_id": f"{REMOVAL_REQUEST_PREFIX}{target_discord_id}"}

    try:
        table.delete_item(Key=key)
    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            "Failed to clear removal notification for %s: %s",
            target_discord_id,
            exc,
        )


async def has_recent_removal_request(table, target_discord_id: str) -> bool:
    if table is None:
        return False

    key = {"discord_id": f"{REMOVAL_REQUEST_PREFIX}{target_discord_id}"}

    try:
        response = table.get_item(Key=key)
        item = response.get("Item")
        if not item:
            return False

        timestamp = _parse_timestamp(item.get("timestamp"))
        if timestamp is None:
            table.delete_item(Key=key)
            return False

        from bot import APPROVAL_TIMEOUT_SECONDS

        age_seconds = (datetime.now(UTC) - timestamp).total_seconds()
        if age_seconds < APPROVAL_TIMEOUT_SECONDS:
            return True

        table.delete_item(Key=key)
        return False
    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            "Failed to check removal notification for %s: %s",
            target_discord_id,
            exc,
        )
        return False


def _parse_timestamp(timestamp_str: str | None) -> datetime | None:
    """Parse an ISO formatted timestamp, returning ``None`` on failure."""
    if not timestamp_str:
        return None

    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:  # pylint: disable=broad-except
        log.warning("Invalid timestamp %s: %s", timestamp_str, exc)
        return None


async def record_denied_removal(
    table,
    *,
    removal_id: str,
    target_discord_id: str,
    denied_by_id: str | None = None,
    denied_by_name: str | None = None,
) -> None:
    """Persist a denial marker so future checks observe a cooldown."""
    if table is None:
        return

    try:
        now = datetime.now(UTC)
        item = {
            "discord_id": f"{DENIED_REMOVAL_PREFIX}{target_discord_id}",
            "removal_id": removal_id,
            "target_discord_id": target_discord_id,
            "timestamp": now.isoformat(),
            "status": "DENIED",
        }
        if denied_by_id:
            item["denied_by_id"] = denied_by_id
        if denied_by_name:
            item["denied_by_name"] = denied_by_name

        table.put_item(Item=item)
    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            "Failed to record denied removal for %s: %s", target_discord_id, exc
        )


async def clear_denied_removal(table, target_discord_id: str) -> bool:
    """Remove any denial marker for the provided Discord user."""
    if table is None:
        return False

    key = {"discord_id": f"{DENIED_REMOVAL_PREFIX}{target_discord_id}"}

    try:
        response = table.delete_item(Key=key, ReturnValues="ALL_OLD")
        removed = "Attributes" in response
        if removed:
            log.info(
                "Removed denial marker for %s after clan membership update",
                target_discord_id,
            )
        return removed
    except Exception as exc:  # pylint: disable=broad-except
        log.exception(
            "Failed to clear denial marker for %s: %s", target_discord_id, exc
        )
        return False


async def has_recent_denied_removal(table, target_discord_id: str) -> bool:
    """Return ``True`` when a denial cooldown is still active."""
    if table is None:
        return False

    key = {"discord_id": f"{DENIED_REMOVAL_PREFIX}{target_discord_id}"}

    try:
        result = table.get_item(Key=key)
        item = result.get("Item")
        if not item:
            return False

        timestamp = _parse_timestamp(item.get("timestamp"))
        if timestamp is None:
            # Cannot evaluate; drop the marker so it doesn't block future checks.
            table.delete_item(Key=key)
            return False

        from bot import APPROVAL_TIMEOUT_SECONDS

        age_seconds = (datetime.now(UTC) - timestamp).total_seconds()
        if age_seconds < APPROVAL_TIMEOUT_SECONDS:
            return True

        # Cooldown expired – remove stale marker.
        table.delete_item(Key=key)
        return False

    except Exception as exc:  # pylint: disable=broad-except
        log.exception("Failed to load denial marker for %s: %s", target_discord_id, exc)
        return False


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
            & Attr("discord_id").begins_with(PENDING_REMOVAL_PREFIX),
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
