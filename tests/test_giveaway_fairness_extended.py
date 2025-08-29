"""
Extended test coverage for giveaway fairness system.
This file targets the missing lines identified in the coverage report.
"""

import datetime
import unittest
from unittest.mock import MagicMock, patch

from giveaway_fairness import (
    FairnessConfig,
    GiveawayFairness,
    UserStats,
)


class TestExtendedUserStats(unittest.TestCase):
    """Extended tests for UserStats edge cases."""

    def test_from_dynamodb_item_with_all_optional_fields(self):
        """Test UserStats creation with all optional fields present."""
        item = {
            "discord_id": "123456789",
            "total_entries": "10",
            "total_wins": "2",
            "current_pity": "3.5",
            "participation_streak": "5",
            "goldpass_wins": "1",
            "giftcard_wins": "1",
            "last_win_date": "2023-01-15T12:30:00+00:00",
            "last_reset_date": "2023-01-10T10:00:00+00:00",
            "created_date": "2023-01-01T00:00:00+00:00",
            "last_entry_date": "2023-01-20T15:45:00+00:00",
        }

        stats = UserStats.from_dynamodb_item(item)

        self.assertEqual(stats.discord_id, "123456789")
        self.assertEqual(stats.total_entries, 10)
        self.assertEqual(stats.total_wins, 2)
        self.assertEqual(stats.current_pity, 3.5)
        self.assertEqual(stats.participation_streak, 5)
        self.assertEqual(stats.goldpass_wins, 1)
        self.assertEqual(stats.giftcard_wins, 1)
        self.assertIsNotNone(stats.last_win_date)
        self.assertIsNotNone(stats.last_reset_date)
        self.assertIsNotNone(stats.created_date)
        self.assertIsNotNone(stats.last_entry_date)

    def test_to_dynamodb_item_with_all_fields(self):
        """Test conversion to DynamoDB with all optional fields set."""
        now = datetime.datetime.now(tz=datetime.UTC)
        stats = UserStats(
            discord_id="123456789",
            total_entries=10,
            total_wins=2,
            last_win_date=now,
            current_pity=2.5,
            last_reset_date=now,
            participation_streak=3,
            goldpass_wins=1,
            giftcard_wins=1,
            created_date=now,
            last_entry_date=now,
        )

        item = stats.to_dynamodb_item()

        self.assertIn("last_win_date", item)
        self.assertIn("last_reset_date", item)
        self.assertIn("last_entry_date", item)
        self.assertEqual(item["discord_id"], "123456789")
        self.assertEqual(item["total_entries"], 10)
        self.assertEqual(item["current_pity"], "2.5")


class TestExtendedGiveawayFairness(unittest.IsolatedAsyncioTestCase):
    """Extended tests for GiveawayFairness missing coverage."""

    def setUp(self):
        """Set up test environment."""
        self.mock_table = MagicMock()
        self.config = FairnessConfig()
        self.fairness = GiveawayFairness(self.mock_table, self.config)

    async def test_get_user_stats_fallback_on_table_none(self):
        """Test get_user_stats fallback when table is None."""
        fairness = GiveawayFairness(None, self.config)

        stats = await fairness.get_user_stats("test_user")

        self.assertEqual(stats.discord_id, "test_user")
        self.assertEqual(stats.current_pity, 1.0)  # Default, not new user boost

    async def test_get_user_stats_exception_handling(self):
        """Test get_user_stats exception handling."""
        self.mock_table.get_item.side_effect = Exception("Database error")

        stats = await self.fairness.get_user_stats("test_user")

        self.assertEqual(stats.discord_id, "test_user")
        self.assertEqual(stats.current_pity, 1.0)  # Fallback

    async def test_save_user_stats_with_table_none(self):
        """Test _save_user_stats when table is None."""
        fairness = GiveawayFairness(None, self.config)
        stats = UserStats(discord_id="test_user")

        # Should not raise exception
        await fairness._save_user_stats(stats)

    async def test_save_user_stats_exception_handling(self):
        """Test _save_user_stats exception handling."""
        self.mock_table.put_item.side_effect = Exception("Database error")
        stats = UserStats(discord_id="test_user")

        # Should not raise exception
        await self.fairness._save_user_stats(stats)

    def test_calculate_selection_weight_participation_bonus(self):
        """Test participation bonus calculation."""
        stats = UserStats(
            discord_id="test_user",
            total_entries=10,  # Above threshold
            current_pity=1.0,
        )

        weight = self.fairness.calculate_selection_weight(
            "test_user", stats, "goldpass", 10
        )

        self.assertGreater(weight, 1.0)  # Should have participation bonus

    def test_calculate_selection_weight_recency_bonus(self):
        """Test recency bonus calculation."""
        recent_date = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(
            days=3
        )
        stats = UserStats(
            discord_id="test_user", last_entry_date=recent_date, current_pity=1.0
        )

        weight = self.fairness.calculate_selection_weight(
            "test_user", stats, "goldpass", 10
        )

        self.assertGreater(weight, 1.0)  # Should have recency bonus

    def test_calculate_selection_weight_inactive_penalty(self):
        """Test inactive user penalty."""
        old_date = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=60)
        stats = UserStats(
            discord_id="test_user", last_entry_date=old_date, current_pity=1.0
        )

        weight = self.fairness.calculate_selection_weight(
            "test_user", stats, "goldpass", 10
        )

        self.assertLess(weight, 1.0)  # Should have inactive penalty

    def test_calculate_selection_weight_goldpass_cooldown(self):
        """Test Gold Pass win cooldown penalty."""
        recent_win = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=7)
        stats = UserStats(
            discord_id="test_user", last_win_date=recent_win, current_pity=1.0
        )

        weight = self.fairness.calculate_selection_weight(
            "test_user", stats, "goldpass", 10
        )

        self.assertLess(weight, 1.0)  # Should have cooldown penalty

    def test_calculate_selection_weight_giftcard_cooldown(self):
        """Test Gift Card win cooldown penalty."""
        recent_win = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=3)
        stats = UserStats(
            discord_id="test_user", last_win_date=recent_win, current_pity=1.0
        )

        weight = self.fairness.calculate_selection_weight(
            "test_user", stats, "giftcard", 10
        )

        self.assertLess(weight, 1.0)  # Should have cooldown penalty

    async def test_select_winners_fairly_zero_count(self):
        """Test select_winners_fairly with zero count."""
        entries = ["user1", "user2"]

        winners = await self.fairness.select_winners_fairly(entries, "goldpass", 0)

        self.assertEqual(winners, [])

    async def test_select_winners_fairly_empty_remaining_entries(self):
        """Test select_winners_fairly when remaining entries becomes empty."""
        entries = ["user1"]

        async def mock_get_stats(user_id):
            return UserStats(discord_id=user_id)

        self.fairness.get_user_stats = mock_get_stats

        winners = await self.fairness.select_winners_fairly(entries, "goldpass", 1)

        self.assertEqual(len(winners), 1)
        self.assertIn("user1", winners)

    async def test_select_winners_fairly_fallback_selection(self):
        """Test fallback selection when weighted selection fails."""
        entries = ["user1", "user2"]

        async def mock_get_stats(user_id):
            return UserStats(discord_id=user_id, current_pity=0.0)  # Zero weight

        self.fairness.get_user_stats = mock_get_stats

        # Mock random to always return a value that triggers fallback
        with (
            patch("random.uniform") as mock_uniform,
            patch("random.choice") as mock_choice,
        ):
            mock_uniform.return_value = 999999  # Very high value
            mock_choice.return_value = "user1"

            winners = await self.fairness.select_winners_fairly(entries, "goldpass", 1)

            self.assertEqual(len(winners), 1)
            mock_choice.assert_called()

    async def test_update_winner_stats_exception_handling(self):
        """Test update_winner_stats exception handling."""
        winners = ["test_user"]

        async def mock_get_stats_error(user_id):
            raise Exception("Database error")

        self.fairness.get_user_stats = mock_get_stats_error

        # Should not raise exception
        await self.fairness.update_winner_stats(winners, "test_giveaway", "goldpass")

    async def test_log_winner_selection_with_table_none(self):
        """Test _log_winner_selection when table is None."""
        fairness = GiveawayFairness(None, self.config)

        # Should not raise exception
        await fairness._log_winner_selection("test_user", "test_giveaway", "goldpass")

    async def test_log_winner_selection_exception_handling(self):
        """Test _log_winner_selection exception handling."""
        self.mock_table.put_item.side_effect = Exception("Database error")

        # Should not raise exception
        await self.fairness._log_winner_selection(
            "test_user", "test_giveaway", "goldpass"
        )

    async def test_update_participation_stats_exception_handling(self):
        """Test update_participation_stats exception handling."""
        participants = ["test_user"]

        async def mock_get_stats_error(user_id):
            raise Exception("Database error")

        self.fairness.get_user_stats = mock_get_stats_error

        # Should not raise exception
        await self.fairness.update_participation_stats(participants, "test_giveaway")

    async def test_apply_time_based_decay_with_table_none(self):
        """Test apply_time_based_decay when table is None."""
        fairness = GiveawayFairness(None, self.config)

        # Should not raise exception
        await fairness.apply_time_based_decay()

    async def test_apply_time_based_decay_exception_handling(self):
        """Test apply_time_based_decay main exception handling."""
        self.mock_table.query.side_effect = Exception("Database error")

        # Should not raise exception
        await self.fairness.apply_time_based_decay()

    async def test_apply_time_based_decay_item_exception_handling(self):
        """Test apply_time_based_decay with item processing exception."""
        # Mock items that will cause exception in processing
        mock_items = [{"invalid": "item"}]  # Missing required fields
        self.mock_table.query.return_value = {"Items": mock_items}

        saved_stats = []

        async def mock_save_stats(stats):
            saved_stats.append(stats)

        self.fairness._save_user_stats = mock_save_stats

        # Should not raise exception, should handle item processing errors
        await self.fairness.apply_time_based_decay()

    async def test_apply_time_based_decay_with_decay(self):
        """Test apply_time_based_decay that actually applies decay."""
        old_date = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=60)

        mock_items = [
            {
                "discord_id": "inactive_user",
                "current_pity": "3.0",
                "last_entry_date": old_date.isoformat(),
            }
        ]

        self.mock_table.query.return_value = {"Items": mock_items}

        saved_stats = []

        async def mock_save_stats(stats):
            saved_stats.append(stats)

        self.fairness._save_user_stats = mock_save_stats

        await self.fairness.apply_time_based_decay()

        # Should save decayed stats
        self.assertEqual(len(saved_stats), 1)
        self.assertLess(saved_stats[0].current_pity, 3.0)

    async def test_get_fairness_analytics_with_table_none(self):
        """Test get_fairness_analytics when table is None."""
        fairness = GiveawayFairness(None, self.config)

        analytics = await fairness.get_fairness_analytics()

        self.assertIn("error", analytics)

    async def test_get_fairness_analytics_exception_handling(self):
        """Test get_fairness_analytics exception handling."""
        self.mock_table.query.side_effect = Exception("Database error")

        analytics = await self.fairness.get_fairness_analytics()

        self.assertIn("error", analytics)

    async def test_get_fairness_analytics_empty_data(self):
        """Test get_fairness_analytics with no user data."""
        self.mock_table.query.return_value = {"Items": []}

        analytics = await self.fairness.get_fairness_analytics()

        self.assertIn("message", analytics)
        self.assertEqual(analytics["message"], "No user data available")

    async def test_get_fairness_analytics_with_data(self):
        """Test get_fairness_analytics with actual data."""
        mock_items = [
            {
                "discord_id": "user1",
                "total_entries": "10",
                "total_wins": "2",
                "current_pity": "2.0",
            },
            {
                "discord_id": "user2",
                "total_entries": "6",
                "total_wins": "0",
                "current_pity": "3.5",
            },
        ]

        self.mock_table.query.return_value = {"Items": mock_items}

        analytics = await self.fairness.get_fairness_analytics()

        self.assertEqual(analytics["total_users"], 2)
        self.assertEqual(analytics["average_pity"], 2.75)
        self.assertEqual(analytics["average_wins"], 1.0)
        self.assertEqual(analytics["average_entries"], 8.0)
        self.assertEqual(analytics["high_pity_count"], 1)  # user2 has pity > 3.0
        self.assertEqual(
            analytics["never_won_count"], 1
        )  # user2 never won and has >5 entries
        self.assertIn("system_health", analytics)

    def test_should_reset_population_pity_true(self):
        """Test should_reset_population_pity returns True for high average."""
        result = self.fairness.should_reset_population_pity(3.5)
        self.assertTrue(result)

    def test_should_reset_population_pity_false(self):
        """Test should_reset_population_pity returns False for normal average."""
        result = self.fairness.should_reset_population_pity(2.0)
        self.assertFalse(result)

    async def test_apply_population_pity_reset_with_table_none(self):
        """Test apply_population_pity_reset when table is None."""
        fairness = GiveawayFairness(None, self.config)

        # Should not raise exception
        await fairness.apply_population_pity_reset()

    async def test_apply_population_pity_reset_exception_handling(self):
        """Test apply_population_pity_reset main exception handling."""
        self.mock_table.query.side_effect = Exception("Database error")

        # Should not raise exception
        await self.fairness.apply_population_pity_reset()

    async def test_apply_population_pity_reset_item_exception_handling(self):
        """Test apply_population_pity_reset with item processing exception."""
        mock_items = [{"invalid": "item"}]  # Will cause exception
        self.mock_table.query.return_value = {"Items": mock_items}

        saved_stats = []

        async def mock_save_stats(stats):
            saved_stats.append(stats)

        self.fairness._save_user_stats = mock_save_stats

        # Should not raise exception
        await self.fairness.apply_population_pity_reset()

    async def test_apply_population_pity_reset_with_data(self):
        """Test apply_population_pity_reset with actual data."""
        mock_items = [
            {"discord_id": "user1", "current_pity": "4.0"},
            {"discord_id": "user2", "current_pity": "2.0"},
        ]

        self.mock_table.query.return_value = {"Items": mock_items}

        saved_stats = []

        async def mock_save_stats(stats):
            saved_stats.append(stats)

        self.fairness._save_user_stats = mock_save_stats

        await self.fairness.apply_population_pity_reset(0.5)

        # Should reset both users
        self.assertEqual(len(saved_stats), 2)
        # 4.0 * 0.5 = 2.0, 2.0 * 0.5 = 1.0
        reset_values = [stats.current_pity for stats in saved_stats]
        self.assertIn(2.0, reset_values)
        self.assertIn(1.0, reset_values)


if __name__ == "__main__":
    unittest.main()
