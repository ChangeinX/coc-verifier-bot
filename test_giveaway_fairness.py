"""
Unit tests for the giveaway fairness system.

This test suite validates the fairness algorithm, edge case handling,
and database operations to ensure the system works correctly and maintains
fairness over time.
"""

import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from giveaway_fairness import (
    FairnessConfig,
    GiveawayFairness,
    UserStats,
    select_fair_winners,
    update_giveaway_stats,
)


class TestUserStats(unittest.TestCase):
    """Test UserStats data class functionality."""

    def setUp(self):
        self.user_id = "123456789"
        self.stats = UserStats(discord_id=self.user_id)

    def test_user_stats_creation(self):
        """Test creation of UserStats with defaults."""
        self.assertEqual(self.stats.discord_id, self.user_id)
        self.assertEqual(self.stats.total_entries, 0)
        self.assertEqual(self.stats.total_wins, 0)
        self.assertIsNone(self.stats.last_win_date)
        self.assertEqual(self.stats.current_pity, 1.0)
        self.assertEqual(self.stats.participation_streak, 0)

    def test_user_stats_to_dynamodb_item(self):
        """Test conversion to DynamoDB item format."""
        self.stats.total_entries = 5
        self.stats.current_pity = 2.5
        self.stats.last_win_date = datetime.datetime(2023, 1, 15, tzinfo=datetime.UTC)

        item = self.stats.to_dynamodb_item()

        self.assertEqual(item["discord_id"], self.user_id)
        self.assertEqual(item["total_entries"], 5)
        self.assertEqual(item["current_pity"], "2.5")
        self.assertEqual(item["last_win_date"], "2023-01-15T00:00:00+00:00")

    def test_user_stats_from_dynamodb_item(self):
        """Test creation from DynamoDB item."""
        item = {
            "discord_id": self.user_id,
            "total_entries": 10,
            "total_wins": 2,
            "current_pity": "3.0",
            "last_win_date": "2023-01-15T00:00:00+00:00",
            "goldpass_wins": 1,
            "giftcard_wins": 1
        }

        stats = UserStats.from_dynamodb_item(item)

        self.assertEqual(stats.discord_id, self.user_id)
        self.assertEqual(stats.total_entries, 10)
        self.assertEqual(stats.total_wins, 2)
        self.assertEqual(stats.current_pity, 3.0)
        self.assertEqual(stats.goldpass_wins, 1)
        self.assertEqual(stats.giftcard_wins, 1)
        self.assertIsNotNone(stats.last_win_date)


class TestFairnessConfig(unittest.TestCase):
    """Test FairnessConfig with various parameter combinations."""

    def test_default_config(self):
        """Test default configuration values."""
        config = FairnessConfig()

        self.assertEqual(config.base_pity_increment, 0.25)
        self.assertEqual(config.max_pity_multiplier, 4.0)
        self.assertEqual(config.major_win_cooldown_days, 14)
        self.assertEqual(config.new_user_pity_boost, 1.5)

    def test_custom_config(self):
        """Test configuration with custom values."""
        config = FairnessConfig(
            base_pity_increment=0.5,
            max_pity_multiplier=5.0,
            new_user_pity_boost=2.0
        )

        self.assertEqual(config.base_pity_increment, 0.5)
        self.assertEqual(config.max_pity_multiplier, 5.0)
        self.assertEqual(config.new_user_pity_boost, 2.0)


class TestGiveawayFairness(unittest.IsolatedAsyncioTestCase):
    """Test main GiveawayFairness class functionality."""

    def setUp(self):
        """Set up test environment."""
        self.mock_table = MagicMock()
        self.config = FairnessConfig()
        self.fairness = GiveawayFairness(self.mock_table, self.config)
        self.user_id = "123456789"

    async def test_get_user_stats_new_user(self):
        """Test getting stats for a new user."""
        # Mock table returns no existing item
        self.mock_table.get_item.return_value = {}
        self.mock_table.put_item = MagicMock()

        stats = await self.fairness.get_user_stats(self.user_id)

        self.assertEqual(stats.discord_id, self.user_id)
        self.assertEqual(stats.current_pity, self.config.new_user_pity_boost)
        self.mock_table.put_item.assert_called_once()

    async def test_get_user_stats_existing_user(self):
        """Test getting stats for existing user."""
        mock_item = {
            "discord_id": self.user_id,
            "total_entries": 5,
            "current_pity": "2.5",
            "total_wins": 1
        }
        self.mock_table.get_item.return_value = {"Item": mock_item}

        stats = await self.fairness.get_user_stats(self.user_id)

        self.assertEqual(stats.discord_id, self.user_id)
        self.assertEqual(stats.total_entries, 5)
        self.assertEqual(stats.current_pity, 2.5)
        self.assertEqual(stats.total_wins, 1)

    async def test_get_user_stats_no_table(self):
        """Test fallback when table is None."""
        fairness = GiveawayFairness(None, self.config)

        stats = await fairness.get_user_stats(self.user_id)

        self.assertEqual(stats.discord_id, self.user_id)
        self.assertEqual(stats.current_pity, 1.0)  # Default, not new user boost

    def test_calculate_selection_weight_base_case(self):
        """Test weight calculation for basic user."""
        stats = UserStats(discord_id=self.user_id)

        weight = self.fairness.calculate_selection_weight(
            self.user_id, stats, "goldpass", 10
        )

        self.assertGreaterEqual(weight, 0.1)  # Minimum weight
        self.assertAlmostEqual(weight, 1.0, places=1)  # Should be close to base

    def test_calculate_selection_weight_high_pity(self):
        """Test weight calculation for user with high pity."""
        stats = UserStats(discord_id=self.user_id, current_pity=3.0)

        weight = self.fairness.calculate_selection_weight(
            self.user_id, stats, "goldpass", 10
        )

        self.assertGreater(weight, 2.5)  # Should be significantly higher

    def test_calculate_selection_weight_recent_winner(self):
        """Test weight calculation for recent winner."""
        recent_date = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=5)
        stats = UserStats(
            discord_id=self.user_id,
            last_win_date=recent_date,
            total_wins=1
        )

        weight = self.fairness.calculate_selection_weight(
            self.user_id, stats, "goldpass", 10
        )

        self.assertLess(weight, 1.0)  # Should be reduced due to cooldown

    def test_calculate_selection_weight_small_pool(self):
        """Test weight calculation adjustment for small participant pool."""
        stats = UserStats(discord_id=self.user_id, current_pity=4.0)

        weight = self.fairness.calculate_selection_weight(
            self.user_id, stats, "goldpass", 5  # Small pool
        )

        # Should be less than max pity due to small pool adjustment
        self.assertLess(weight, 4.0)
        self.assertGreater(weight, 1.0)

    def test_calculate_selection_weight_participation_bonus(self):
        """Test weight calculation with participation bonus."""
        stats = UserStats(
            discord_id=self.user_id,
            total_entries=10,  # Above threshold
            current_pity=1.0
        )

        weight = self.fairness.calculate_selection_weight(
            self.user_id, stats, "goldpass", 10
        )

        self.assertGreater(weight, 1.0)  # Should have participation bonus

    async def test_select_winners_fairly_basic(self):
        """Test basic winner selection functionality."""
        entries = ["user1", "user2", "user3", "user4"]

        # Mock get_user_stats to return default stats
        async def mock_get_stats(user_id):
            return UserStats(discord_id=user_id)

        self.fairness.get_user_stats = mock_get_stats

        winners = await self.fairness.select_winners_fairly(entries, "goldpass", 2)

        self.assertEqual(len(winners), 2)
        self.assertTrue(all(winner in entries for winner in winners))
        self.assertEqual(len(set(winners)), 2)  # No duplicates

    async def test_select_winners_fairly_empty_entries(self):
        """Test winner selection with empty entries."""
        winners = await self.fairness.select_winners_fairly([], "goldpass", 2)
        self.assertEqual(winners, [])

    async def test_select_winners_fairly_more_winners_than_entries(self):
        """Test winner selection when requesting more winners than entries."""
        entries = ["user1", "user2"]

        async def mock_get_stats(user_id):
            return UserStats(discord_id=user_id)

        self.fairness.get_user_stats = mock_get_stats

        winners = await self.fairness.select_winners_fairly(entries, "goldpass", 5)

        self.assertEqual(len(winners), 2)  # Should only return available entries

    async def test_select_winners_fairly_weighted_selection(self):
        """Test that higher pity users are more likely to be selected."""
        entries = ["low_pity", "high_pity"]

        async def mock_get_stats(user_id):
            if user_id == "high_pity":
                return UserStats(discord_id=user_id, current_pity=4.0)
            else:
                return UserStats(discord_id=user_id, current_pity=1.0)

        self.fairness.get_user_stats = mock_get_stats

        # Run selection multiple times to test probability
        high_pity_wins = 0
        trials = 100

        for _ in range(trials):
            winners = await self.fairness.select_winners_fairly(entries, "goldpass", 1)
            if "high_pity" in winners:
                high_pity_wins += 1

        # High pity user should win significantly more often (expect >70%)
        self.assertGreater(high_pity_wins, trials * 0.6)

    async def test_update_winner_stats_goldpass(self):
        """Test winner stats update for Gold Pass win."""
        winners = [self.user_id]

        # Mock existing stats
        mock_stats = UserStats(
            discord_id=self.user_id,
            current_pity=3.0,
            total_wins=0
        )

        async def mock_get_stats(user_id):
            return mock_stats

        saved_stats = []
        async def mock_save_stats(stats):
            saved_stats.append(stats)

        self.fairness.get_user_stats = mock_get_stats
        self.fairness._save_user_stats = mock_save_stats
        self.fairness._log_winner_selection = AsyncMock()

        await self.fairness.update_winner_stats(winners, "test_giveaway", "goldpass")

        self.assertEqual(len(saved_stats), 1)
        updated_stats = saved_stats[0]
        self.assertEqual(updated_stats.total_wins, 1)
        self.assertEqual(updated_stats.goldpass_wins, 1)
        self.assertEqual(updated_stats.current_pity, 1.0)  # Should be reset
        self.assertIsNotNone(updated_stats.last_win_date)

    async def test_update_winner_stats_giftcard(self):
        """Test winner stats update for Gift Card win."""
        winners = [self.user_id]

        mock_stats = UserStats(
            discord_id=self.user_id,
            current_pity=3.0,
            total_wins=0
        )

        async def mock_get_stats(user_id):
            return mock_stats

        saved_stats = []
        async def mock_save_stats(stats):
            saved_stats.append(stats)

        self.fairness.get_user_stats = mock_get_stats
        self.fairness._save_user_stats = mock_save_stats
        self.fairness._log_winner_selection = AsyncMock()

        await self.fairness.update_winner_stats(winners, "test_giveaway", "giftcard")

        updated_stats = saved_stats[0]
        self.assertEqual(updated_stats.total_wins, 1)
        self.assertEqual(updated_stats.giftcard_wins, 1)
        self.assertLess(updated_stats.current_pity, 3.0)  # Should be reduced
        self.assertGreaterEqual(updated_stats.current_pity, 1.0)  # But not below 1.0

    async def test_update_participation_stats(self):
        """Test participation stats update."""
        participants = ["user1", "user2", "user3"]

        UserStats(discord_id="", total_entries=5, current_pity=2.0)

        async def mock_get_stats(user_id):
            stats = UserStats(discord_id=user_id, total_entries=5, current_pity=2.0)
            return stats

        saved_stats = []
        async def mock_save_stats(stats):
            saved_stats.append(stats)

        self.fairness.get_user_stats = mock_get_stats
        self.fairness._save_user_stats = mock_save_stats

        await self.fairness.update_participation_stats(participants, "test_giveaway")

        self.assertEqual(len(saved_stats), 3)
        for stats in saved_stats:
            self.assertEqual(stats.total_entries, 6)  # Incremented
            self.assertGreater(stats.current_pity, 2.0)  # Pity increased
            self.assertIsNotNone(stats.last_entry_date)

    async def test_apply_time_based_decay(self):
        """Test time-based pity decay for inactive users."""
        old_date = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=60)

        mock_items = [
            {
                "discord_id": "active_user",
                "current_pity": "2.0",
                "last_entry_date": datetime.datetime.now(tz=datetime.UTC).isoformat()
            },
            {
                "discord_id": "inactive_user",
                "current_pity": "3.0",
                "last_entry_date": old_date.isoformat()
            }
        ]

        self.mock_table.query.return_value = {"Items": mock_items}

        saved_stats = []
        async def mock_save_stats(stats):
            saved_stats.append(stats)

        self.fairness._save_user_stats = mock_save_stats

        await self.fairness.apply_time_based_decay()

        # Should only save stats for inactive user (active user unchanged)
        self.assertEqual(len(saved_stats), 1)
        inactive_stats = saved_stats[0]
        self.assertEqual(inactive_stats.discord_id, "inactive_user")
        self.assertLess(inactive_stats.current_pity, 3.0)  # Pity decayed

    async def test_get_fairness_analytics(self):
        """Test fairness analytics generation."""
        mock_items = [
            {
                "discord_id": "user1",
                "total_entries": "10",
                "total_wins": "1",
                "current_pity": "2.0"
            },
            {
                "discord_id": "user2",
                "total_entries": "8",
                "total_wins": "0",
                "current_pity": "3.5"
            }
        ]

        self.mock_table.query.return_value = {"Items": mock_items}

        analytics = await self.fairness.get_fairness_analytics()

        self.assertEqual(analytics["total_users"], 2)
        self.assertEqual(analytics["average_pity"], 2.75)
        self.assertEqual(analytics["never_won_count"], 1)
        self.assertIn("system_health", analytics)

    def test_should_reset_population_pity(self):
        """Test population pity reset threshold detection."""
        self.assertFalse(self.fairness.should_reset_population_pity(2.0))
        self.assertTrue(self.fairness.should_reset_population_pity(3.5))

    async def test_apply_population_pity_reset(self):
        """Test population-wide pity reset."""
        mock_items = [
            {"discord_id": "user1", "current_pity": "4.0"},
            {"discord_id": "user2", "current_pity": "3.0"}
        ]

        self.mock_table.query.return_value = {"Items": mock_items}

        saved_stats = []
        async def mock_save_stats(stats):
            saved_stats.append(stats)

        self.fairness._save_user_stats = mock_save_stats

        await self.fairness.apply_population_pity_reset(0.5)

        self.assertEqual(len(saved_stats), 2)
        self.assertEqual(saved_stats[0].current_pity, 2.0)  # 4.0 * 0.5
        self.assertEqual(saved_stats[1].current_pity, 1.5)  # 3.0 * 0.5


class TestConvenienceFunctions(unittest.IsolatedAsyncioTestCase):
    """Test convenience functions for integration."""

    def setUp(self):
        self.mock_table = MagicMock()

    @patch('giveaway_fairness.GiveawayFairness')
    async def test_select_fair_winners(self, mock_fairness_class):
        """Test convenience function for winner selection."""
        mock_fairness = AsyncMock()
        mock_fairness.select_winners_fairly.return_value = ["winner1", "winner2"]
        mock_fairness_class.return_value = mock_fairness

        winners = await select_fair_winners(
            self.mock_table, ["user1", "user2", "user3"], "goldpass", 2
        )

        self.assertEqual(winners, ["winner1", "winner2"])
        mock_fairness.select_winners_fairly.assert_called_once_with(
            ["user1", "user2", "user3"], "goldpass", 2
        )

    @patch('giveaway_fairness.GiveawayFairness')
    async def test_update_giveaway_stats(self, mock_fairness_class):
        """Test convenience function for stats update."""
        mock_fairness = AsyncMock()
        mock_fairness_class.return_value = mock_fairness

        winners = ["winner1"]
        participants = ["user1", "user2", "winner1"]

        await update_giveaway_stats(
            self.mock_table, winners, participants, "test_giveaway", "goldpass"
        )

        mock_fairness.update_participation_stats.assert_called_once_with(
            participants, "test_giveaway"
        )
        mock_fairness.update_winner_stats.assert_called_once_with(
            winners, "test_giveaway", "goldpass"
        )


class TestEdgeCases(unittest.IsolatedAsyncioTestCase):
    """Test edge cases and error handling."""

    def setUp(self):
        self.fairness = GiveawayFairness(None)  # No table for testing

    async def test_exception_handling_in_get_user_stats(self):
        """Test graceful fallback when database operations fail."""
        mock_table = MagicMock()
        mock_table.get_item.side_effect = Exception("Database error")

        fairness = GiveawayFairness(mock_table)
        stats = await fairness.get_user_stats("test_user")

        # Should return default stats as fallback
        self.assertEqual(stats.discord_id, "test_user")
        self.assertEqual(stats.current_pity, 1.0)

    def test_weight_calculation_extreme_values(self):
        """Test weight calculation with extreme values."""
        # Very high pity
        stats = UserStats(discord_id="test", current_pity=100.0)
        weight = self.fairness.calculate_selection_weight("test", stats, "goldpass", 10)
        self.assertEqual(weight, self.fairness.config.max_pity_multiplier)  # Should be capped

        # Very old win date (should not affect calculation much)
        old_date = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        stats = UserStats(discord_id="test", last_win_date=old_date)
        weight = self.fairness.calculate_selection_weight("test", stats, "goldpass", 10)
        self.assertGreaterEqual(weight, 0.1)  # Should still have reasonable weight

    async def test_select_winners_single_participant(self):
        """Test winner selection with only one participant."""
        async def mock_get_stats(user_id):
            return UserStats(discord_id=user_id)

        self.fairness.get_user_stats = mock_get_stats

        winners = await self.fairness.select_winners_fairly(["only_user"], "goldpass", 1)
        self.assertEqual(winners, ["only_user"])

        # Request more winners than participants
        winners = await self.fairness.select_winners_fairly(["only_user"], "goldpass", 3)
        self.assertEqual(winners, ["only_user"])


if __name__ == "__main__":
    # Run tests with asyncio support
    unittest.main()
