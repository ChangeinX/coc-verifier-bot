"""
Extended test coverage for giveawaybot.py.
This file targets the missing lines identified in the coverage report.
"""

import datetime
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from giveawaybot import (
    GiveawayView,
    _table_is_empty,
    create_giveaway,
    eligible_for_giftcard,
    fairness_maintenance,
    finish_giveaway,
    giveaway_exists,
    main,
    seed_initial_giveaways,
)


class TestGiveawayViewExtended(unittest.IsolatedAsyncioTestCase):
    """Extended tests for GiveawayView missing coverage."""

    def setUp(self):
        """Set up test environment."""
        self.giveaway_id = "test-giveaway"
        self.run_id = "test-run"
        self.view = GiveawayView(self.giveaway_id, self.run_id)

    async def test_update_entry_count_exception_handling(self):
        """Test _update_entry_count exception handling."""
        with patch("giveawaybot.table") as mock_table:
            mock_table.query.side_effect = Exception("Database error")

            count = await self.view._update_entry_count()

            self.assertEqual(count, 0)

    async def test_enter_button_table_none(self):
        """Test enter button when table is None."""
        interaction_mock = AsyncMock()
        interaction_mock.user.id = 123456789
        button_mock = Mock()

        with patch("giveawaybot.table", None):
            await self.view.enter(interaction_mock, button_mock)

            interaction_mock.response.send_message.assert_called_once_with(
                "Database not configured", ephemeral=True
            )

    async def test_enter_button_botocore_client_error(self):
        """Test enter button with botocore ClientError."""
        interaction_mock = AsyncMock()
        interaction_mock.user.id = 123456789
        button_mock = Mock()

        # Mock a proper ClientError structure
        class MockClientError(Exception):
            def __init__(self):
                self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}

        mock_error = MockClientError()

        with (
            patch("giveawaybot.table") as mock_table,
            patch.object(self.view, "_update_entry_count", return_value=5),
        ):
            mock_table.put_item.side_effect = mock_error

            # Mock the botocore import
            with patch("builtins.__import__") as mock_import:

                def import_side_effect(name, *args, **kwargs):
                    if name == "botocore.exceptions":
                        mock_module = Mock()
                        mock_module.ClientError = MockClientError
                        return mock_module
                    return __import__(name, *args, **kwargs)

                mock_import.side_effect = import_side_effect

                await self.view.enter(interaction_mock, button_mock)

            interaction_mock.response.send_message.assert_called_once()

    async def test_enter_button_dynamodb_exception_handling(self):
        """Test enter button with DynamoDB exception handling."""
        interaction_mock = AsyncMock()
        interaction_mock.user.id = 123456789
        button_mock = Mock()

        # Mock DynamoDB exception
        class MockDynamoDBException(Exception):
            pass

        mock_exception = MockDynamoDBException("ConditionalCheckFailedException")

        with (
            patch("giveawaybot.table") as mock_table,
            patch("giveawaybot.dynamodb") as mock_dynamodb,
            patch.object(self.view, "_update_entry_count", return_value=3),
        ):
            mock_table.put_item.side_effect = mock_exception
            mock_dynamodb.meta.client.exceptions.ConditionalCheckFailedException = (
                MockDynamoDBException
            )

            await self.view.enter(interaction_mock, button_mock)

            interaction_mock.response.send_message.assert_called_once()

    async def test_enter_button_unhandled_exception(self):
        """Test enter button with unhandled exception."""
        interaction_mock = AsyncMock()
        interaction_mock.user.id = 123456789
        button_mock = Mock()

        with patch("giveawaybot.table") as mock_table:
            mock_table.put_item.side_effect = Exception("Unknown error")

            await self.view.enter(interaction_mock, button_mock)

            interaction_mock.response.send_message.assert_called_once_with(
                "Entry failed", ephemeral=True
            )


class TestCreateGiveawayExtended(unittest.IsolatedAsyncioTestCase):
    """Extended tests for create_giveaway missing coverage."""

    async def test_create_giveaway_table_none(self):
        """Test create_giveaway when table is None."""
        with patch("giveawaybot.table", None), patch("giveawaybot.bot") as mock_bot:
            mock_bot.guilds = [Mock()]

            await create_giveaway(
                "test-giveaway",
                "Test Title",
                "Test Description",
                datetime.datetime.now(tz=datetime.UTC),
            )
            # Should return early without error

    async def test_create_giveaway_no_guilds(self):
        """Test create_giveaway when bot has no guilds."""
        with (
            patch("giveawaybot.table"),
            patch("giveawaybot.bot") as mock_bot,
        ):
            mock_bot.guilds = []

            await create_giveaway(
                "test-giveaway",
                "Test Title",
                "Test Description",
                datetime.datetime.now(tz=datetime.UTC),
            )
            # Should return early without error

    async def test_create_giveaway_channel_not_text(self):
        """Test create_giveaway when channel is not TextChannel."""
        with (
            patch("giveawaybot.table"),
            patch("giveawaybot.bot") as mock_bot,
            patch("giveawaybot.TEST_MODE", False),
        ):
            mock_bot.guilds = [Mock()]
            mock_bot.get_channel.return_value = Mock(
                spec=discord.VoiceChannel
            )  # Not TextChannel

            await create_giveaway(
                "test-giveaway",
                "Test Title",
                "Test Description",
                datetime.datetime.now(tz=datetime.UTC),
            )
            # Should return early after warning

    async def test_create_giveaway_test_mode(self):
        """Test create_giveaway in test mode."""
        mock_channel = Mock(spec=discord.TextChannel)
        mock_message = Mock()
        mock_message.id = 123456789
        mock_channel.send = AsyncMock(return_value=mock_message)

        with (
            patch("giveawaybot.table") as mock_table,
            patch("giveawaybot.bot") as mock_bot,
            patch("giveawaybot.TEST_MODE", True),
        ):
            mock_bot.guilds = [Mock()]
            mock_bot.get_channel.return_value = mock_channel

            draw_time = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(
                days=1
            )

            await create_giveaway(
                "test-giveaway", "Test Title", "Test Description", draw_time
            )

            mock_channel.send.assert_called_once()
            mock_table.put_item.assert_called_once()

    async def test_create_giveaway_put_item_exception(self):
        """Test create_giveaway with put_item exception."""
        mock_channel = Mock(spec=discord.TextChannel)
        mock_message = Mock()
        mock_message.id = 123456789
        mock_channel.send = AsyncMock(return_value=mock_message)

        with (
            patch("giveawaybot.table") as mock_table,
            patch("giveawaybot.bot") as mock_bot,
            patch("giveawaybot.TEST_MODE", False),
        ):
            mock_bot.guilds = [Mock()]
            mock_bot.get_channel.return_value = mock_channel
            mock_table.put_item.side_effect = Exception("Database error")

            await create_giveaway(
                "test-giveaway",
                "Test Title",
                "Test Description",
                datetime.datetime.now(tz=datetime.UTC),
            )
            # Should handle exception gracefully


class TestGiveawayExistsExtended(unittest.IsolatedAsyncioTestCase):
    """Extended tests for giveaway_exists missing coverage."""

    async def test_giveaway_exists_exception_handling(self):
        """Test giveaway_exists exception handling."""
        with patch("giveawaybot.table") as mock_table:
            mock_table.get_item.side_effect = Exception("Database error")

            result = await giveaway_exists("test-giveaway")

            self.assertFalse(result)


class TestEligibleForGiftcardExtended(unittest.IsolatedAsyncioTestCase):
    """Extended tests for eligible_for_giftcard missing coverage."""

    async def test_eligible_for_giftcard_test_mode(self):
        """Test eligible_for_giftcard in test mode."""
        with (
            patch("giveawaybot.TEST_MODE", True),
            patch("giveawaybot.ver_table") as mock_table,
            patch("giveawaybot.CLAN_TAG", "#TEST123"),
            patch("giveawaybot.coc_client") as mock_coc,
        ):
            # Mock verification table response
            mock_table.get_item.return_value = {"Item": {"player_tag": "#PLAYER123"}}

            # Mock raid log
            mock_member = Mock()
            mock_member.capital_resources_looted = 25000

            mock_entry = Mock()
            mock_entry.get_member.return_value = mock_member

            # Make get_raid_log an async mock that returns a list
            mock_coc.get_raid_log = AsyncMock(return_value=[mock_entry])

            result = await eligible_for_giftcard("123456789")

            self.assertTrue(result)

    async def test_eligible_for_giftcard_no_clan_tag(self):
        """Test eligible_for_giftcard when CLAN_TAG is None."""
        with (
            patch("giveawaybot.TEST_MODE", True),
            patch("giveawaybot.ver_table") as mock_table,
            patch("giveawaybot.CLAN_TAG", None),
            patch("giveawaybot.coc_client") as mock_coc,
        ):
            mock_table.get_item.return_value = {"Item": {"player_tag": "#PLAYER123"}}
            mock_coc.get_raid_log = AsyncMock(side_effect=Exception("No clan tag"))

            result = await eligible_for_giftcard("123456789")
            self.assertFalse(result)

    async def test_eligible_for_giftcard_verification_exception(self):
        """Test eligible_for_giftcard with verification exception."""
        with (
            patch("giveawaybot.TEST_MODE", False),
            patch("giveawaybot.ver_table") as mock_table,
        ):
            mock_table.get_item.side_effect = Exception("Database error")

            result = await eligible_for_giftcard("123456789")

            self.assertFalse(result)

    async def test_eligible_for_giftcard_no_verification_item(self):
        """Test eligible_for_giftcard with no verification item."""
        with (
            patch("giveawaybot.TEST_MODE", False),
            patch("giveawaybot.ver_table") as mock_table,
        ):
            mock_table.get_item.return_value = {}

            result = await eligible_for_giftcard("123456789")

            self.assertFalse(result)

    async def test_eligible_for_giftcard_no_player_tag(self):
        """Test eligible_for_giftcard with no player tag."""
        with (
            patch("giveawaybot.TEST_MODE", False),
            patch("giveawaybot.ver_table") as mock_table,
        ):
            mock_table.get_item.return_value = {"Item": {}}

            result = await eligible_for_giftcard("123456789")

            self.assertFalse(result)

    async def test_eligible_for_giftcard_no_raid_log(self):
        """Test eligible_for_giftcard with no raid log."""
        with (
            patch("giveawaybot.TEST_MODE", False),
            patch("giveawaybot.ver_table") as mock_table,
            patch("giveawaybot.coc_client") as mock_coc,
        ):
            mock_table.get_item.return_value = {"Item": {"player_tag": "#PLAYER123"}}
            mock_coc.get_raid_log = AsyncMock(return_value=[])

            result = await eligible_for_giftcard("123456789")

            self.assertFalse(result)

    async def test_eligible_for_giftcard_no_member_in_raid(self):
        """Test eligible_for_giftcard when member not in raid."""
        with (
            patch("giveawaybot.TEST_MODE", False),
            patch("giveawaybot.ver_table") as mock_table,
            patch("giveawaybot.coc_client") as mock_coc,
        ):
            mock_table.get_item.return_value = {"Item": {"player_tag": "#PLAYER123"}}

            mock_entry = Mock()
            mock_entry.get_member.return_value = None
            mock_coc.get_raid_log = AsyncMock(return_value=[mock_entry])

            result = await eligible_for_giftcard("123456789")

            self.assertFalse(result)

    async def test_eligible_for_giftcard_raid_log_exception(self):
        """Test eligible_for_giftcard with raid log exception."""
        with (
            patch("giveawaybot.TEST_MODE", False),
            patch("giveawaybot.ver_table") as mock_table,
            patch("giveawaybot.coc_client") as mock_coc,
        ):
            mock_table.get_item.return_value = {"Item": {"player_tag": "#PLAYER123"}}
            mock_coc.get_raid_log = AsyncMock(side_effect=Exception("API error"))

            result = await eligible_for_giftcard("123456789")

            self.assertFalse(result)


class TestFinishGiveawayExtended(unittest.IsolatedAsyncioTestCase):
    """Extended tests for finish_giveaway missing coverage."""

    async def test_finish_giveaway_table_none(self):
        """Test finish_giveaway when table is None."""
        with patch("giveawaybot.table", None):
            await finish_giveaway("test-giveaway")
            # Should return early without error

    async def test_finish_giveaway_query_exception(self):
        """Test finish_giveaway with query exception."""
        with patch("giveawaybot.table") as mock_table:
            mock_table.get_item.side_effect = Exception("Database error")

            await finish_giveaway("test-giveaway")
            # Should handle exception gracefully

    async def test_finish_giveaway_already_drawn(self):
        """Test finish_giveaway when already drawn."""
        with patch("giveawaybot.table") as mock_table:
            mock_table.get_item.return_value = {
                "Item": {"drawn": "1", "run_id": "test-run"}
            }

            await finish_giveaway("test-giveaway")
            # Should return early

    async def test_finish_giveaway_no_entries(self):
        """Test finish_giveaway with no entries."""
        with (
            patch("giveawaybot.table") as mock_table,
            patch("giveawaybot.bot") as mock_bot,
        ):
            mock_table.get_item.return_value = {
                "Item": {"run_id": "test-run", "message_id": "123"}
            }
            mock_table.query.return_value = {"Items": []}

            mock_channel = Mock(spec=discord.TextChannel)
            mock_message = Mock()
            mock_message.embeds = [Mock()]
            mock_channel.fetch_message = AsyncMock(return_value=mock_message)
            mock_channel.send = AsyncMock()

            mock_bot.get_channel.return_value = mock_channel

            await finish_giveaway("goldpass-test")

            mock_channel.send.assert_called_once()

    async def test_finish_giveaway_fairness_system_exception(self):
        """Test finish_giveaway with fairness system exception."""
        with (
            patch("giveawaybot.table") as mock_table,
            patch("giveawaybot.bot") as mock_bot,
            patch("giveawaybot.USE_FAIRNESS_SYSTEM", True),
            patch("giveaway_fairness.select_fair_winners") as mock_fair_winners,
        ):
            mock_table.get_item.return_value = {
                "Item": {"run_id": "test-run", "message_id": "123"}
            }
            mock_table.query.return_value = {
                "Items": [{"user_id": "test-run#123456789"}]
            }

            mock_fair_winners.side_effect = Exception("Fairness error")

            mock_channel = Mock(spec=discord.TextChannel)
            mock_message = Mock()
            mock_message.embeds = [Mock()]
            mock_channel.fetch_message = AsyncMock(return_value=mock_message)
            mock_channel.send = AsyncMock()

            mock_bot.get_channel.return_value = mock_channel

            with patch("random.shuffle"), patch("random.choices"):
                await finish_giveaway("goldpass-test")

    async def test_finish_giveaway_message_update_exception(self):
        """Test finish_giveaway with message update exception."""
        with (
            patch("giveawaybot.table") as mock_table,
            patch("giveawaybot.bot") as mock_bot,
            patch("giveawaybot.USE_FAIRNESS_SYSTEM", False),
        ):
            mock_table.get_item.return_value = {
                "Item": {
                    "run_id": "test-run",
                    "message_id": "123",
                    "draw_time": "2023-01-01T12:00:00+00:00",
                }
            }
            mock_table.query.return_value = {
                "Items": [{"user_id": "test-run#123456789"}]
            }

            mock_channel = Mock(spec=discord.TextChannel)
            mock_channel.fetch_message = AsyncMock(
                side_effect=Exception("Message not found")
            )
            mock_channel.send = AsyncMock()

            mock_bot.get_channel.return_value = mock_channel

            with patch("random.shuffle"), patch("random.choices"):
                await finish_giveaway("goldpass-test")

    async def test_finish_giveaway_player_name_exception(self):
        """Test finish_giveaway with player name fetch exception."""
        with (
            patch("giveawaybot.table") as mock_table,
            patch("giveawaybot.ver_table") as mock_ver_table,
            patch("giveawaybot.bot") as mock_bot,
            patch("giveawaybot.USE_FAIRNESS_SYSTEM", False),
        ):
            mock_table.get_item.return_value = {
                "Item": {"run_id": "test-run", "message_id": "123"}
            }
            mock_table.query.return_value = {
                "Items": [{"user_id": "test-run#123456789"}]
            }
            mock_ver_table.get_item.side_effect = Exception("Database error")

            mock_channel = Mock(spec=discord.TextChannel)
            mock_message = Mock()
            mock_message.embeds = [Mock()]
            mock_channel.fetch_message = AsyncMock(return_value=mock_message)
            mock_channel.send = AsyncMock()

            mock_bot.get_channel.return_value = mock_channel

            with patch("random.shuffle"), patch("random.choices"):
                await finish_giveaway("goldpass-test")

    async def test_finish_giveaway_update_item_exception(self):
        """Test finish_giveaway with update_item exception."""
        with (
            patch("giveawaybot.table") as mock_table,
            patch("giveawaybot.bot") as mock_bot,
            patch("giveawaybot.USE_FAIRNESS_SYSTEM", False),
        ):
            mock_table.get_item.return_value = {
                "Item": {"run_id": "test-run", "message_id": "123"}
            }
            mock_table.query.return_value = {"Items": []}
            mock_table.update_item.side_effect = Exception("Update failed")

            mock_channel = Mock(spec=discord.TextChannel)
            mock_message = Mock()
            mock_message.embeds = [Mock()]
            mock_channel.fetch_message = AsyncMock(return_value=mock_message)
            mock_channel.send = AsyncMock()

            mock_bot.get_channel.return_value = mock_channel

            await finish_giveaway("goldpass-test")


class TestFairnessMaintenanceExtended(unittest.IsolatedAsyncioTestCase):
    """Extended tests for fairness_maintenance missing coverage."""

    async def test_fairness_maintenance_disabled(self):
        """Test fairness_maintenance when fairness system is disabled."""
        with patch("giveawaybot.USE_FAIRNESS_SYSTEM", False):
            await fairness_maintenance()
            # Should return early without error

    async def test_fairness_maintenance_table_none(self):
        """Test fairness_maintenance when table is None."""
        with (
            patch("giveawaybot.USE_FAIRNESS_SYSTEM", True),
            patch("giveawaybot.table", None),
        ):
            await fairness_maintenance()
            # Should return early without error

    async def test_fairness_maintenance_exception(self):
        """Test fairness_maintenance with exception."""
        with (
            patch("giveawaybot.USE_FAIRNESS_SYSTEM", True),
            patch("giveawaybot.table"),
        ):
            # Mock import to raise exception
            with patch("builtins.__import__", side_effect=Exception("Import error")):
                await fairness_maintenance()
                # Should handle exception gracefully


class TestUtilityFunctionsExtended(unittest.IsolatedAsyncioTestCase):
    """Extended tests for utility functions missing coverage."""

    async def test_table_is_empty_table_none(self):
        """Test _table_is_empty when table is None."""
        with patch("giveawaybot.table", None):
            result = await _table_is_empty()
            self.assertFalse(result)

    async def test_table_is_empty_scan_exception(self):
        """Test _table_is_empty with scan exception."""
        with patch("giveawaybot.table") as mock_table:
            mock_table.scan.side_effect = Exception("Scan failed")

            result = await _table_is_empty()
            self.assertFalse(result)

    async def test_seed_initial_giveaways_table_not_empty(self):
        """Test seed_initial_giveaways when table is not empty."""
        with patch("giveawaybot._table_is_empty", return_value=False):
            await seed_initial_giveaways()
            # Should return early without creating giveaways


class TestMainFunction(unittest.IsolatedAsyncioTestCase):
    """Test main function missing coverage."""

    async def test_main_missing_required_vars(self):
        """Test main function with missing required environment variables."""
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError) as context:
                await main()
            self.assertIn("Missing env vars", str(context.exception))


if __name__ == "__main__":
    unittest.main()
