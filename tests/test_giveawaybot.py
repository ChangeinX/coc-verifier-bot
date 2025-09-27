"""Tests for the giveaway bot (giveawaybot.py)."""

import asyncio
import datetime
import os
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord
import pytest

# Mock environment variables before importing
with patch.dict(
    os.environ,
    {
        "DISCORD_TOKEN": "fake_token",
        "GIVEAWAY_CHANNEL_ID": "123456",
        "GIVEAWAY_TABLE_NAME": "test_giveaway_table",
        "AWS_REGION": "us-east-1",
        "GIVEAWAY_TEST": "false",
        "COC_EMAIL": "fake_email",
        "COC_PASSWORD": "fake_password",
        "CLAN_TAG": "#TESTCLAN",
        "DDB_TABLE_NAME": "test_verification_table",
    },
):
    import giveawaybot


class TestEnvironmentValidation:
    """Test environment variable validation."""

    def test_required_vars_defined(self):
        """Test that required environment variables are properly defined."""
        assert giveawaybot.TOKEN is not None
        assert giveawaybot.GIVEAWAY_CHANNEL_ID == 123456
        assert giveawaybot.GIVEAWAY_TABLE_NAME is not None
        assert giveawaybot.COC_EMAIL is not None
        assert giveawaybot.COC_PASSWORD is not None
        assert giveawaybot.CLAN_TAG is not None
        assert giveawaybot.DDB_TABLE_NAME is not None
        assert giveawaybot.TEST_MODE is False


class TestGiveawayView:
    """Test the GiveawayView Discord UI component."""

    @pytest.mark.asyncio
    async def test_init(self):
        """Test GiveawayView initialization."""
        giveaway_id = "test-giveaway"
        run_id = "run123"
        view = giveawaybot.GiveawayView(giveaway_id, run_id)

        assert view.giveaway_id == giveaway_id
        assert view.run_id == run_id
        assert view.timeout is None

    @pytest.mark.asyncio
    async def test_update_entry_count_no_table(self):
        """Test _update_entry_count when table is None."""
        view = giveawaybot.GiveawayView("test", "run123")

        with patch.object(giveawaybot, "table", None):
            count = await view._update_entry_count()
            assert count == 0

    @pytest.mark.asyncio
    async def test_update_entry_count_with_entries(self):
        """Test _update_entry_count with existing entries."""
        view = giveawaybot.GiveawayView("test-giveaway", "run123")

        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                {"user_id": "run123#111"},
                {"user_id": "run123#222"},
                {"user_id": "run123#333"},
            ]
        }
        mock_table.get_item.return_value = {"Item": {"message_id": "999888777"}}

        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_message = MagicMock()
        mock_embed = MagicMock()
        mock_message.embeds = [mock_embed]
        mock_channel.fetch_message.return_value = mock_message

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(giveawaybot.bot, "get_channel", return_value=mock_channel),
            patch.object(giveawaybot, "GIVEAWAY_CHANNEL_ID", 123456),
        ):
            count = await view._update_entry_count()
            assert count == 3
            mock_embed.set_footer.assert_called_once_with(text="3 entries")

    @pytest.mark.asyncio
    async def test_enter_button_success(self):
        """Test successful entry into giveaway."""
        view = giveawaybot.GiveawayView("test-giveaway", "run123")

        # Mock interaction
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.user.id = 123456789
        # Ensure send_message is awaitable
        interaction.response.send_message = AsyncMock()

        # Mock table
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}
        mock_table.get_item.return_value = {"Item": {"message_id": "999888777"}}

        # Button not used in callback tests

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(
                view, "_update_entry_count", new_callable=AsyncMock, return_value=1
            ),
        ):
            await view.enter.callback(interaction)

            # Verify database insertion
            mock_table.put_item.assert_called_once_with(
                Item={"giveaway_id": "test-giveaway", "user_id": "run123#123456789"},
                ConditionExpression="attribute_not_exists(user_id)",
            )

            # Verify response
            interaction.response.send_message.assert_called_once_with(
                "You're entered! (1 entries)", ephemeral=True
            )

    @pytest.mark.asyncio
    async def test_enter_button_already_entered(self):
        """Test entering giveaway when already entered."""
        view = giveawaybot.GiveawayView("test-giveaway", "run123")

        interaction = AsyncMock(spec=discord.Interaction)
        interaction.user.id = 123456789
        interaction.response.send_message = AsyncMock()
        # Button not used in callback tests

        mock_table = MagicMock()
        # Simulate conditional check failure (already exists)
        from botocore.exceptions import ClientError

        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(
                view, "_update_entry_count", new_callable=AsyncMock, return_value=2
            ),
        ):
            await view.enter.callback(interaction)

            interaction.response.send_message.assert_called_once_with(
                "You're already entered! (2 entries)", ephemeral=True
            )

    @pytest.mark.asyncio
    async def test_enter_button_restricted_role_missing(self):
        """Users without allowed roles cannot enter restricted giveaways."""
        view = giveawaybot.GiveawayView("test-giveaway", "run123")

        interaction = AsyncMock(spec=discord.Interaction)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 987654321
        interaction.user.roles = []
        interaction.response.send_message = AsyncMock()

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {"allowed_role_ids": ["1392517649350791208"]}
        }

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(
                view, "_update_entry_count", new_callable=AsyncMock, return_value=1
            ),
        ):
            await view.enter.callback(interaction)

        mock_table.put_item.assert_not_called()
        interaction.response.send_message.assert_awaited_once_with(
            "You do not have the required role to enter this giveaway.",
            ephemeral=True,
        )

    @pytest.mark.asyncio
    async def test_enter_button_restricted_role_allowed(self):
        """Users with allowed roles can enter restricted giveaways."""
        view = giveawaybot.GiveawayView("test-giveaway", "run123")

        interaction = AsyncMock(spec=discord.Interaction)
        member = MagicMock(spec=discord.Member)
        role = MagicMock()
        role.id = 1392517649350791208
        member.roles = [role]
        member.id = 111222333
        interaction.user = member
        interaction.response.send_message = AsyncMock()

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {"allowed_role_ids": ["1392517649350791208"]}
        }

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(
                view, "_update_entry_count", new_callable=AsyncMock, return_value=5
            ),
        ):
            await view.enter.callback(interaction)

        mock_table.put_item.assert_called_once_with(
            Item={"giveaway_id": "test-giveaway", "user_id": "run123#111222333"},
            ConditionExpression="attribute_not_exists(user_id)",
        )
        interaction.response.send_message.assert_awaited_once_with(
            "You're entered! (5 entries)", ephemeral=True
        )


class TestGiveawayCreation:
    """Test giveaway creation functionality."""

    @pytest.mark.asyncio
    async def test_create_giveaway_no_table(self):
        """Test create_giveaway when table is None."""
        with patch.object(giveawaybot, "table", None):
            await giveawaybot.create_giveaway(
                "test-id",
                "Test Title",
                "Test Description",
                datetime.datetime.now(tz=datetime.UTC),
            )
            # Should not raise an exception

    @pytest.mark.asyncio
    async def test_create_giveaway_no_guilds(self):
        """Test create_giveaway when bot has no guilds."""
        mock_table = MagicMock()

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(
                type(giveawaybot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[],
            ),
        ):
            await giveawaybot.create_giveaway(
                "test-id",
                "Test Title",
                "Test Description",
                datetime.datetime.now(tz=datetime.UTC),
            )
            # Should not raise an exception

    @pytest.mark.asyncio
    async def test_create_giveaway_success(self):
        """Test successful giveaway creation."""
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_message = MagicMock()
        mock_message.id = 999888777
        mock_channel.send.return_value = mock_message
        mock_channel.id = giveawaybot.GIVEAWAY_CHANNEL_ID
        mock_channel.id = giveawaybot.GIVEAWAY_CHANNEL_ID

        mock_table = MagicMock()

        draw_time = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(
                type(giveawaybot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[MagicMock()],
            ),
            patch.object(giveawaybot.bot, "get_channel", return_value=mock_channel),
            patch("uuid.uuid4") as uuid_mock,
        ):
            uuid_mock.return_value.hex = "mock-uuid-hex"

            await giveawaybot.create_giveaway(
                "test-giveaway", "Test Title", "Test Description", draw_time
            )

            # Verify message was sent
            mock_channel.send.assert_called_once()

            # Verify database storage
            mock_table.put_item.assert_called_once()
            call_args = mock_table.put_item.call_args[1]["Item"]
            assert call_args["giveaway_id"] == "test-giveaway"
            assert call_args["user_id"] == "META"
            assert call_args["message_id"] == "999888777"
            assert call_args["run_id"] == "mock-uuid-hex"
            assert call_args["winners"] == 1
            assert call_args["channel_id"] == str(mock_channel.id)
            assert call_args["allowed_role_ids"] == [
                str(role_id)
                for role_id in sorted(giveawaybot.RECURRING_GIVEAWAY_ALLOWED_ROLES)
            ]

    @pytest.mark.asyncio
    async def test_create_giveaway_test_mode(self):
        """Test giveaway creation in test mode."""
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_message = MagicMock()
        mock_message.id = 999888777
        mock_channel.send.return_value = mock_message

        mock_table = MagicMock()

        original_draw_time = datetime.datetime(
            2024, 12, 31, 23, 59, 59, tzinfo=datetime.UTC
        )

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(
                type(giveawaybot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[MagicMock()],
            ),
            patch.object(giveawaybot.bot, "get_channel", return_value=mock_channel),
            patch.object(giveawaybot, "TEST_MODE", True),
        ):
            await giveawaybot.create_giveaway(
                "test-giveaway", "Test Title", "Test Description", original_draw_time
            )

            # In test mode, draw time should be adjusted to 1 minute from now
            mock_channel.send.assert_called_once()


class TestManualCreateGiveaway:
    """Tests for the manual /create-giveaway command."""

    def _build_interaction(self, has_role: bool = True) -> discord.Interaction:
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        member = MagicMock(spec=discord.Member)
        member.id = 4242
        role = MagicMock()
        role.id = giveawaybot.CREATE_GIVEAWAY_ROLE_ID
        member.roles = [role] if has_role else []
        interaction.user = member
        return interaction

    @pytest.mark.asyncio
    async def test_manual_create_requires_role(self):
        interaction = self._build_interaction(has_role=False)

        with patch.object(giveawaybot, "table", MagicMock()):
            await giveawaybot.manual_create_giveaway.callback(interaction, 1, "6h")

        interaction.response.send_message.assert_called_once()
        interaction.response.defer.assert_not_called()

    @pytest.mark.asyncio
    async def test_manual_create_invalid_trigger(self):
        interaction = self._build_interaction()

        with patch.object(giveawaybot, "table", MagicMock()):
            await giveawaybot.manual_create_giveaway.callback(
                interaction, 1, "not-a-trigger"
            )

        interaction.response.send_message.assert_called_once()
        interaction.response.defer.assert_not_called()

    @pytest.mark.asyncio
    async def test_manual_create_success_time_and_entries(self):
        interaction = self._build_interaction()

        before_call = datetime.datetime.now(tz=datetime.UTC)

        with (
            patch.object(giveawaybot, "table", MagicMock()),
            patch.object(
                giveawaybot, "create_giveaway", new_callable=AsyncMock
            ) as create_mock,
        ):
            await giveawaybot.manual_create_giveaway.callback(
                interaction, 2, "12h", "200"
            )

        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()

        assert create_mock.await_count == 1
        awaited_call = create_mock.await_args
        args, kwargs = awaited_call.args, awaited_call.kwargs
        assert kwargs["entry_goal"] == 200
        assert kwargs["winners"] == 2
        assert kwargs["prize_label"] == "2 × Gold Passes"
        assert kwargs["channel_id"] == giveawaybot.CREATE_GIVEAWAY_CHANNEL_ID
        assert kwargs["allowed_role_ids"] == giveawaybot.MANUAL_GIVEAWAY_ALLOWED_ROLES
        assert kwargs["created_by"] == interaction.user.id

        draw_time = args[3]
        assert isinstance(draw_time, datetime.datetime)
        delta_seconds = (draw_time - before_call).total_seconds()
        assert pytest.approx(delta_seconds, abs=5) == 12 * 3600

    @pytest.mark.asyncio
    async def test_manual_create_entries_only(self):
        interaction = self._build_interaction()

        with (
            patch.object(giveawaybot, "table", MagicMock()),
            patch.object(
                giveawaybot, "create_giveaway", new_callable=AsyncMock
            ) as create_mock,
        ):
            await giveawaybot.manual_create_giveaway.callback(interaction, 1, "150")

        awaited_call = create_mock.await_args
        args, kwargs = awaited_call.args, awaited_call.kwargs
        assert args[3] is None
        assert kwargs["entry_goal"] == 150
        assert kwargs["winners"] == 1
        assert kwargs["allowed_role_ids"] == giveawaybot.MANUAL_GIVEAWAY_ALLOWED_ROLES


class TestGiveawayIDGeneration:
    """Test giveaway ID generation functions."""

    def test_month_end_giveaway_id(self):
        """Test month end giveaway ID generation."""
        test_date = datetime.date(2024, 5, 15)
        result = giveawaybot.month_end_giveaway_id(test_date)
        assert result == "goldpass-2024-05"

    def test_weekly_giveaway_id(self):
        """Test weekly giveaway ID generation."""
        test_date = datetime.date(2024, 5, 15)
        result = giveawaybot.weekly_giveaway_id(test_date)
        assert result == "giftcard-2024-05-15"


class TestScheduleCheck:
    """Test the schedule check functionality."""

    @pytest.mark.asyncio
    async def test_schedule_check_gold_pass_day(self):
        """Test schedule check on gold pass giveaway day."""
        # Test date that's 5 days before month end (May 26 for May 31)
        test_date = datetime.date(2024, 5, 26)

        class RealDate(datetime.date):
            @classmethod
            def today(cls):
                return test_date

        with (
            patch("giveawaybot.datetime.date", RealDate),
            patch.object(
                giveawaybot, "giveaway_exists", return_value=False
            ) as exists_mock,
            patch.object(
                giveawaybot, "create_giveaway", new_callable=AsyncMock
            ) as create_mock,
        ):
            await giveawaybot.schedule_check()

            # Should check if gold pass giveaway exists
            exists_mock.assert_called_with("goldpass-2024-05")

            # Should create gold pass giveaway
            assert create_mock.call_count == 1
            args = create_mock.call_args[0]
            assert args[0] == "goldpass-2024-05"
            assert "🏆 Gold Pass Giveaway" in args[1]

    @pytest.mark.asyncio
    async def test_schedule_check_thursday_gift_card(self):
        """Test schedule check on Thursday for gift card giveaway."""
        # Thursday May 23, 2024
        test_date = datetime.date(2024, 5, 23)  # Thursday

        class RealDate(datetime.date):
            @classmethod
            def today(cls):
                return test_date

        with (
            patch("giveawaybot.datetime.date", RealDate),
            patch.object(
                giveawaybot, "giveaway_exists", return_value=False
            ) as exists_mock,
            patch.object(
                giveawaybot, "create_giveaway", new_callable=AsyncMock
            ) as create_mock,
        ):
            await giveawaybot.schedule_check()

            # Should check if gift card giveaway exists
            exists_mock.assert_called_with("giftcard-2024-05-23")

            # Should create gift card giveaway
            assert create_mock.call_count == 1
            args = create_mock.call_args[0]
            assert args[0] == "giftcard-2024-05-23"
            assert "🎁 $10 Gift Card Giveaway" in args[1]

    @pytest.mark.asyncio
    async def test_schedule_check_giveaway_exists(self):
        """Test schedule check when giveaway already exists."""
        test_date = datetime.date(2024, 5, 23)  # Thursday

        class RealDate(datetime.date):
            @classmethod
            def today(cls):
                return test_date

        with (
            patch("giveawaybot.datetime.date", RealDate),
            patch.object(giveawaybot, "giveaway_exists", return_value=True),
            patch.object(
                giveawaybot, "create_giveaway", new_callable=AsyncMock
            ) as create_mock,
        ):
            await giveawaybot.schedule_check()

            # Should not create giveaway if it already exists
            create_mock.assert_not_called()


class TestGiveawayExists:
    """Test giveaway existence checking."""

    @pytest.mark.asyncio
    async def test_giveaway_exists_no_table(self):
        """Test giveaway_exists when table is None."""
        with patch.object(giveawaybot, "table", None):
            result = await giveawaybot.giveaway_exists("test-id")
            assert result is False

    @pytest.mark.asyncio
    async def test_giveaway_exists_true(self):
        """Test giveaway exists and is not drawn."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {"giveaway_id": "test-id", "user_id": "META"}
        }

        with patch.object(giveawaybot, "table", mock_table):
            result = await giveawaybot.giveaway_exists("test-id")
            assert result is True

    @pytest.mark.asyncio
    async def test_giveaway_exists_already_drawn(self):
        """Test giveaway exists but already drawn."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {"giveaway_id": "test-id", "user_id": "META", "drawn": "1"}
        }

        with patch.object(giveawaybot, "table", mock_table):
            result = await giveawaybot.giveaway_exists("test-id")
            assert result is False

    @pytest.mark.asyncio
    async def test_giveaway_exists_no_item(self):
        """Test giveaway does not exist."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}

        with patch.object(giveawaybot, "table", mock_table):
            result = await giveawaybot.giveaway_exists("test-id")
            assert result is False


class TestEligibilityCheck:
    """Test gift card eligibility checking."""

    @pytest.mark.asyncio
    async def test_eligible_for_giftcard_test_mode(self):
        """Test eligibility check in test mode."""
        mock_ver_table = MagicMock()
        mock_ver_table.get_item.return_value = {"Item": {"player_tag": "#PLAYER1"}}

        mock_raid_log = [MagicMock()]
        mock_member = MagicMock()
        mock_member.capital_resources_looted = 25000
        mock_raid_log[0].get_member.return_value = mock_member

        with (
            patch.object(giveawaybot, "TEST_MODE", True),
            patch.object(giveawaybot, "ver_table", mock_ver_table),
            patch.object(giveawaybot, "coc_client") as mock_coc_client,
        ):
            mock_coc_client.get_raid_log = AsyncMock(return_value=mock_raid_log)
            result = await giveawaybot.eligible_for_giftcard("123456789")
            assert result is True

    @pytest.mark.asyncio
    async def test_eligible_for_giftcard_sufficient_loot(self):
        """Test eligibility with sufficient capital loot."""
        mock_ver_table = MagicMock()
        mock_ver_table.get_item.return_value = {"Item": {"player_tag": "#PLAYER1"}}

        mock_raid_log = [MagicMock()]
        mock_member = MagicMock()
        mock_member.capital_resources_looted = 25000
        mock_raid_log[0].get_member.return_value = mock_member

        with (
            patch.object(giveawaybot, "TEST_MODE", False),
            patch.object(giveawaybot, "ver_table", mock_ver_table),
            patch.object(giveawaybot, "coc_client") as mock_coc_client,
        ):
            mock_coc_client.get_raid_log = AsyncMock(return_value=mock_raid_log)
            result = await giveawaybot.eligible_for_giftcard("123456789")
            assert result is True

    @pytest.mark.asyncio
    async def test_eligible_for_giftcard_insufficient_loot(self):
        """Test eligibility with insufficient capital loot."""
        mock_ver_table = MagicMock()
        mock_ver_table.get_item.return_value = {"Item": {"player_tag": "#PLAYER1"}}

        mock_raid_log = [MagicMock()]
        mock_member = MagicMock()
        mock_member.capital_resources_looted = 15000  # Below 23,000 threshold
        mock_raid_log[0].get_member.return_value = mock_member

        with (
            patch.object(giveawaybot, "TEST_MODE", False),
            patch.object(giveawaybot, "ver_table", mock_ver_table),
            patch.object(giveawaybot, "coc_client") as mock_coc_client,
        ):
            mock_coc_client.get_raid_log = AsyncMock(return_value=mock_raid_log)
            result = await giveawaybot.eligible_for_giftcard("123456789")
            assert result is False

    @pytest.mark.asyncio
    async def test_eligible_for_giftcard_no_verification(self):
        """Test eligibility when user has no verification record."""
        mock_ver_table = MagicMock()
        mock_ver_table.get_item.return_value = {}

        with patch.object(giveawaybot, "ver_table", mock_ver_table):
            result = await giveawaybot.eligible_for_giftcard("123456789")
            assert result is False

    @pytest.mark.asyncio
    async def test_eligible_for_giftcard_no_member_in_raid(self):
        """Test eligibility when member not found in raid log."""
        mock_ver_table = MagicMock()
        mock_ver_table.get_item.return_value = {"Item": {"player_tag": "#PLAYER1"}}

        mock_raid_log = [MagicMock()]
        mock_raid_log[0].get_member.return_value = None  # Member not in raid

        with (
            patch.object(giveawaybot, "TEST_MODE", False),
            patch.object(giveawaybot, "ver_table", mock_ver_table),
            patch.object(giveawaybot, "coc_client") as mock_coc_client,
        ):
            mock_coc_client.get_raid_log = AsyncMock(return_value=mock_raid_log)
            result = await giveawaybot.eligible_for_giftcard("123456789")
            assert result is False


class TestFinishGiveaway:
    """Test giveaway finishing functionality."""

    @pytest.mark.asyncio
    async def test_finish_giveaway_no_table(self):
        """Test finish_giveaway when table is None."""
        with patch.object(giveawaybot, "table", None):
            await giveawaybot.finish_giveaway("test-id")
            # Should not raise an exception

    @pytest.mark.asyncio
    async def test_finish_giveaway_already_drawn(self):
        """Test finish_giveaway when already drawn."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": {"drawn": "1"}}

        with patch.object(giveawaybot, "table", mock_table):
            await giveawaybot.finish_giveaway("test-id")

            # Should not proceed with drawing
            mock_table.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_finish_giveaway_gold_pass(self):
        """Test finishing gold pass giveaway."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "run_id": "run123",
                "message_id": "999888777",
                "draw_time": "2024-01-01T12:00:00+00:00",
            }
        }
        mock_table.query.return_value = {
            "Items": [
                {"user_id": "run123#111"},
                {"user_id": "run123#222"},
                {"user_id": "run123#333"},
            ]
        }

        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_message = MagicMock()
        mock_embed = MagicMock()
        mock_embed.fields = []
        mock_message.embeds = [mock_embed]
        mock_channel.fetch_message.return_value = mock_message

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(giveawaybot.bot, "get_channel", return_value=mock_channel),
            patch("random.shuffle"),
        ):
            await giveawaybot.finish_giveaway("goldpass-2024-01")

            # Should announce winner
            mock_channel.send.assert_called_once()
            send_kwargs = mock_channel.send.call_args.kwargs
            assert "embed" in send_kwargs
            winner_embed = send_kwargs["embed"]
            assert isinstance(winner_embed, discord.Embed)
            assert any(field.name == "Total Entries" for field in winner_embed.fields)
            # Should mark as drawn
            mock_table.update_item.assert_called_once()

    @pytest.mark.asyncio
    async def test_finish_giveaway_gift_card_with_eligibility(self):
        """Test finishing gift card giveaway with eligibility filtering."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "run_id": "run123",
                "message_id": "999888777",
                "draw_time": "2024-01-01T12:00:00+00:00",
            }
        }
        mock_table.query.return_value = {
            "Items": [
                {"user_id": "run123#111"},
                {"user_id": "run123#222"},
                {"user_id": "run123#333"},
                {"user_id": "run123#444"},
            ]
        }

        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_message = MagicMock()
        mock_embed = MagicMock()
        mock_embed.fields = []
        mock_message.embeds = [mock_embed]
        mock_channel.fetch_message.return_value = mock_message

        # Mock eligibility - only users 111 and 333 are eligible
        async def mock_eligibility(user_id):
            return user_id in ["111", "333"]

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(giveawaybot.bot, "get_channel", return_value=mock_channel),
            patch.object(
                giveawaybot, "eligible_for_giftcard", side_effect=mock_eligibility
            ),
            patch("random.shuffle"),
        ):
            await giveawaybot.finish_giveaway("giftcard-2024-01-01")

            # Should announce winners (up to 3 eligible users)
            mock_channel.send.assert_called_once()
            send_kwargs = mock_channel.send.call_args.kwargs
            assert "embed" in send_kwargs
            winner_embed = send_kwargs["embed"]
            assert isinstance(winner_embed, discord.Embed)
            assert any(field.name == "Prize" for field in winner_embed.fields)


class TestDrawCheck:
    """Test the draw check background task."""

    @pytest.mark.asyncio
    async def test_draw_check_no_table(self):
        """Test draw_check when table is None."""
        with patch.object(giveawaybot, "table", None):
            await giveawaybot.draw_check()
            # Should not raise an exception

    @pytest.mark.asyncio
    async def test_draw_check_scan_error(self):
        """Test draw_check handling scan errors."""
        mock_table = MagicMock()
        mock_table.scan.side_effect = Exception("Scan error")

        with patch.object(giveawaybot, "table", mock_table):
            await giveawaybot.draw_check()
            # Should not raise an exception

    @pytest.mark.asyncio
    async def test_draw_check_ready_to_draw(self):
        """Test draw_check with giveaway ready to be drawn."""
        # Past draw time
        past_time = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1)

        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "giveaway_id": "ready-giveaway",
                    "user_id": "META",
                    "draw_time": past_time.isoformat(),
                }
            ]
        }

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(giveawaybot, "finish_giveaway") as finish_mock,
        ):
            await giveawaybot.draw_check()

            finish_mock.assert_called_once_with("ready-giveaway")

    @pytest.mark.asyncio
    async def test_draw_check_not_ready(self):
        """Test draw_check with giveaway not ready to be drawn."""
        # Future draw time
        future_time = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(
            hours=1
        )

        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "giveaway_id": "future-giveaway",
                    "user_id": "META",
                    "draw_time": future_time.isoformat(),
                }
            ]
        }

        with (
            patch.object(giveawaybot, "table", mock_table),
            patch.object(giveawaybot, "finish_giveaway") as finish_mock,
        ):
            await giveawaybot.draw_check()

            finish_mock.assert_not_called()


class TestTableUtils:
    """Test table utility functions."""

    @pytest.mark.asyncio
    async def test_table_is_empty_no_table(self):
        """Test _table_is_empty when table is None."""
        with patch.object(giveawaybot, "table", None):
            result = await giveawaybot._table_is_empty()
            assert result is False

    @pytest.mark.asyncio
    async def test_table_is_empty_true(self):
        """Test _table_is_empty when table has no items."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": []}

        with patch.object(giveawaybot, "table", mock_table):
            result = await giveawaybot._table_is_empty()
            assert result is True

    @pytest.mark.asyncio
    async def test_table_is_empty_false(self):
        """Test _table_is_empty when table has items."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": [{"giveaway_id": "test"}]}

        with patch.object(giveawaybot, "table", mock_table):
            result = await giveawaybot._table_is_empty()
            assert result is False

    @pytest.mark.asyncio
    async def test_table_is_empty_scan_error(self):
        """Test _table_is_empty handling scan errors."""
        mock_table = MagicMock()
        mock_table.scan.side_effect = Exception("Scan error")

        with patch.object(giveawaybot, "table", mock_table):
            result = await giveawaybot._table_is_empty()
            assert result is False


class TestSeedInitialGiveaways:
    """Test initial giveaway seeding."""

    @pytest.mark.asyncio
    async def test_seed_initial_giveaways_table_not_empty(self):
        """Test seed_initial_giveaways when table is not empty."""
        with (
            patch.object(giveawaybot, "_table_is_empty", return_value=False),
            patch.object(giveawaybot, "create_giveaway") as create_mock,
        ):
            await giveawaybot.seed_initial_giveaways()

            create_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_initial_giveaways_empty_table(self):
        """Test seed_initial_giveaways when table is empty."""
        test_date = datetime.date(2024, 5, 15)

        class RealDate(datetime.date):
            @classmethod
            def today(cls):
                return test_date

        with (
            patch.object(giveawaybot, "_table_is_empty", return_value=True),
            patch.object(
                giveawaybot, "create_giveaway", new_callable=AsyncMock
            ) as create_mock,
            patch("giveawaybot.datetime.date", RealDate),
        ):
            await giveawaybot.seed_initial_giveaways()

            # Should create both initial giveaways
            assert create_mock.call_count == 2


class TestBotEvents:
    """Test bot event handlers."""

    @pytest.mark.asyncio
    async def test_on_ready(self):
        """Test the on_ready event handler."""
        test_date = datetime.date(2024, 5, 15)

        class RealDate(datetime.date):
            @classmethod
            def today(cls):
                return test_date

        with (
            patch.object(giveawaybot.tree, "sync", new_callable=AsyncMock) as sync_mock,
            patch.object(giveawaybot, "coc_client") as coc_client_mock,
            patch.object(giveawaybot.schedule_check, "start") as schedule_start,
            patch.object(giveawaybot.draw_check, "start") as draw_start,
            patch.object(
                giveawaybot, "seed_initial_giveaways", new_callable=AsyncMock
            ) as seed_mock,
            patch.object(
                giveawaybot.schedule_check, "__call__", new_callable=AsyncMock
            ),
            patch.object(giveawaybot.draw_check, "__call__", new_callable=AsyncMock),
            patch.object(
                giveawaybot, "create_giveaway", new_callable=AsyncMock
            ) as create_mock,
            patch.object(giveawaybot, "TEST_MODE", True),
            patch("giveawaybot.datetime.date", RealDate),
        ):
            coc_client_mock.login = AsyncMock()

            await giveawaybot.on_ready()

            sync_mock.assert_called_once()
            coc_client_mock.login.assert_called_once_with(
                giveawaybot.COC_EMAIL, giveawaybot.COC_PASSWORD
            )
            schedule_start.assert_called_once()
            draw_start.assert_called_once()
            seed_mock.assert_called_once()

            # In test mode, should create test giveaways
            assert create_mock.call_count == 2


class TestMainFunction:
    """Test the main function and environment validation."""

    def test_main_missing_env_vars(self):
        """Test main function raises error for missing environment variables."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="Missing env vars"):
                asyncio.run(giveawaybot.main())

    @pytest.mark.asyncio
    async def test_main_with_all_env_vars(self):
        """Test main function with all required environment variables."""
        env_vars = {
            "DISCORD_TOKEN": "fake_token",
            "GIVEAWAY_CHANNEL_ID": "123456",
            "GIVEAWAY_TABLE_NAME": "test_table",
            "COC_EMAIL": "fake_email",
            "COC_PASSWORD": "fake_password",
            "CLAN_TAG": "#TESTCLAN",
            "DDB_TABLE_NAME": "test_verification_table",
        }

        with (
            patch.dict(os.environ, env_vars),
            patch.object(giveawaybot, "coc_client") as coc_client_mock,
            patch.object(
                giveawaybot.bot, "start", new_callable=AsyncMock
            ) as start_mock,
        ):
            # Mock the start method to avoid actually starting the bot
            coc_client_mock.login = AsyncMock()
            start_mock.side_effect = KeyboardInterrupt()

            try:
                await giveawaybot.main()
            except KeyboardInterrupt:
                pass  # Expected for this test

            coc_client_mock.login.assert_called_once_with("fake_email", "fake_password")
            start_mock.assert_called_once_with("fake_token")


class TestGiveawayStatsCommand:
    """Tests for the /stats giveaway command."""

    @pytest.mark.asyncio
    async def test_stats_command_without_table(self):
        """Command should short-circuit when the table is not configured."""
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.response.send_message = AsyncMock()

        with patch.object(giveawaybot, "table", None):
            await giveawaybot.giveaway_stats.callback(interaction)

        interaction.response.send_message.assert_called_once_with(
            "Giveaway database is not configured.", ephemeral=True
        )

    @pytest.mark.asyncio
    async def test_stats_command_success(self):
        """Command should render an embed with aggregated statistics."""

        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_stats = giveawaybot.GiveawayStatistics(
            total_giveaways=10,
            completed_giveaways=8,
            active_giveaways=2,
            ready_to_draw=1,
            scheduled_giveaways=1,
            total_entries=120,
            average_entries=12.0,
            total_winners_recorded=15,
            giveaways_with_winners=8,
            successful_payouts=5,
        )

        with (
            patch.object(giveawaybot, "table", object()),
            patch.object(
                giveawaybot,
                "_collect_giveaway_statistics",
                new=AsyncMock(return_value=mock_stats),
            ),
        ):
            await giveawaybot.giveaway_stats.callback(interaction)

        interaction.response.defer.assert_called_once_with(ephemeral=True)
        interaction.response.send_message.assert_not_called()

        interaction.followup.send.assert_called_once()
        kwargs = interaction.followup.send.call_args.kwargs
        assert kwargs["ephemeral"] is True
        embed = kwargs["embed"]

        fields = {field.name: field.value for field in embed.fields}
        assert fields["Total Giveaways"] == "10"
        assert fields["Successful Payouts"] == "5"
        assert fields["Pending Payouts"] == "3"
        assert fields["Entries Recorded"] == "120 (avg 12.0)"

        assert not embed.description

    @pytest.mark.asyncio
    async def test_collect_stats_without_table(self):
        """Helper should return zeroed statistics when table is missing."""

        with patch.object(giveawaybot, "table", None):
            stats = await giveawaybot._collect_giveaway_statistics()

        assert stats.total_giveaways == 0
        assert stats.completed_giveaways == 0

    @pytest.mark.asyncio
    async def test_collect_stats_with_sample_data(self):
        """Helper should aggregate statistics from stored giveaways."""

        now = datetime.datetime.now(tz=datetime.UTC)
        past = (now - datetime.timedelta(hours=2)).isoformat()
        future = (now + datetime.timedelta(hours=3)).isoformat()

        scan_items = [
            {
                "giveaway_id": "giveaway-complete",
                "run_id": "run1",
                "drawn": "1",
                "payout_status": "COMPLETED",
            },
            {
                "giveaway_id": "giveaway-ready",
                "run_id": "run2",
                "draw_time": past,
            },
            {
                "giveaway_id": "giveaway-scheduled",
                "run_id": "run3",
                "draw_time": future,
            },
        ]

        class FakeTable:
            def __init__(self) -> None:
                self.responses = [
                    {"Items": scan_items},
                    {
                        "Items": [
                            {"user_id": "run1#111"},
                            {"user_id": "run1#222"},
                        ]
                    },
                    {"Items": [{"user_id": "run2#333"}]},
                    {"Items": []},
                    {
                        "Items": [
                            {
                                "user_id": "HISTORY#2024-01-01T00:00:00Z#abcd",
                                "original_giveaway_id": "giveaway-complete",
                            }
                        ]
                    },
                ]

            def scan(self, **_kwargs):
                return self.responses.pop(0)

            def query(self, **_kwargs):
                return self.responses.pop(0)

        with patch.object(giveawaybot, "table", FakeTable()):
            stats = await giveawaybot._collect_giveaway_statistics()

        assert stats.total_giveaways == 3
        assert stats.completed_giveaways == 1
        assert stats.active_giveaways == 2
        assert stats.ready_to_draw == 1
        assert stats.scheduled_giveaways == 1
        assert stats.total_entries == 3
        assert stats.average_entries == pytest.approx(1.0)
        assert stats.successful_payouts == 1
        assert stats.total_winners_recorded == 1
        assert stats.giveaways_with_winners == 1
