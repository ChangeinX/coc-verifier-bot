"""
Giveaway Fairness Module

This module implements an intelligent weighted selection system for Discord giveaways
to ensure fair distribution of wins over time while maintaining the excitement of
randomness. It includes a pity system for users who haven't won recently and handles
various edge cases for long-term fairness.

Key Features:
- Multi-factor weight calculation based on participation history
- Intelligent pity system with automatic resets
- Edge case handling for small participant pools
- Backward compatibility with existing systems
- Comprehensive logging for transparency
"""

import datetime
import logging
import random
import uuid
from dataclasses import dataclass, field

from boto3.dynamodb import conditions

log = logging.getLogger("giveaway-fairness")


@dataclass
class UserStats:
    """User statistics for giveaway fairness calculations."""

    discord_id: str
    total_entries: int = 0
    total_wins: int = 0
    last_win_date: datetime.datetime | None = None
    current_pity: float = 1.0
    last_reset_date: datetime.datetime | None = None
    participation_streak: int = 0
    goldpass_wins: int = 0
    giftcard_wins: int = 0
    created_date: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.UTC)
    )
    last_entry_date: datetime.datetime | None = None

    def to_dynamodb_item(self) -> dict:
        """Convert to DynamoDB item format."""
        item = {
            "discord_id": self.discord_id,
            "total_entries": self.total_entries,
            "total_wins": self.total_wins,
            "current_pity": str(self.current_pity),
            "participation_streak": self.participation_streak,
            "goldpass_wins": self.goldpass_wins,
            "giftcard_wins": self.giftcard_wins,
            "created_date": self.created_date.isoformat(),
        }

        if self.last_win_date:
            item["last_win_date"] = self.last_win_date.isoformat()
        if self.last_reset_date:
            item["last_reset_date"] = self.last_reset_date.isoformat()
        if self.last_entry_date:
            item["last_entry_date"] = self.last_entry_date.isoformat()

        return item

    @classmethod
    def from_dynamodb_item(cls, item: dict) -> "UserStats":
        """Create from DynamoDB item."""
        stats = cls(
            discord_id=item["discord_id"],
            total_entries=int(item.get("total_entries", 0)),
            total_wins=int(item.get("total_wins", 0)),
            current_pity=float(item.get("current_pity", 1.0)),
            participation_streak=int(item.get("participation_streak", 0)),
            goldpass_wins=int(item.get("goldpass_wins", 0)),
            giftcard_wins=int(item.get("giftcard_wins", 0)),
        )

        if "last_win_date" in item:
            stats.last_win_date = datetime.datetime.fromisoformat(item["last_win_date"])
        if "last_reset_date" in item:
            stats.last_reset_date = datetime.datetime.fromisoformat(
                item["last_reset_date"]
            )
        if "created_date" in item:
            stats.created_date = datetime.datetime.fromisoformat(item["created_date"])
        if "last_entry_date" in item:
            stats.last_entry_date = datetime.datetime.fromisoformat(
                item["last_entry_date"]
            )

        return stats


@dataclass
class FairnessConfig:
    """Configuration for fairness algorithm."""

    # Pity system parameters
    base_pity_increment: float = 0.25  # Increase per consecutive loss
    max_pity_multiplier: float = 4.0  # Maximum pity multiplier
    pity_decay_rate: float = 0.05  # Monthly pity decay when inactive

    # Participation bonuses
    participation_bonus_cap: float = 1.5  # Max bonus for consistent participation
    participation_threshold: int = 5  # Entries needed for participation bonus

    # Cooldown system
    major_win_cooldown_days: int = 14  # Cooldown after Gold Pass wins
    minor_win_cooldown_days: int = 7  # Cooldown after Gift Card wins
    cooldown_weight_reduction: float = 0.3  # Weight reduction during cooldown

    # Reset logic
    major_win_pity_reset: float = 1.0  # Reset pity to base after major win
    minor_win_pity_reduction: float = 0.5  # Reduce pity by this factor after minor win
    monthly_pity_decay: float = 0.1  # Monthly natural pity decay

    # Edge case handling
    small_pool_threshold: int = 8  # Pool size considered "small"
    new_user_pity_boost: float = 1.5  # Initial pity for new users
    inactive_user_days: int = 30  # Days before user considered inactive


class GiveawayFairness:
    """Main fairness system for giveaway winner selection."""

    def __init__(self, table, config: FairnessConfig | None = None):
        """
        Initialize fairness system.

        Args:
            table: DynamoDB table for storing user statistics
            config: Configuration parameters (uses defaults if None)
        """
        self.table = table
        self.config = config or FairnessConfig()

    async def get_user_stats(self, discord_id: str) -> UserStats:
        """
        Get or create user statistics.

        Args:
            discord_id: Discord user ID

        Returns:
            UserStats object with current user data
        """
        if self.table is None:
            # Fallback for testing or when table unavailable
            return UserStats(discord_id=discord_id)

        try:
            # Look for existing stats
            resp = self.table.get_item(
                Key={"giveaway_id": "USER_STATS", "user_id": f"STATS#{discord_id}"}
            )

            if "Item" in resp:
                item = resp["Item"]
                return UserStats.from_dynamodb_item(item)
            else:
                # Create new user stats with new user boost
                stats = UserStats(
                    discord_id=discord_id, current_pity=self.config.new_user_pity_boost
                )
                await self._save_user_stats(stats)
                log.info(
                    f"Created new user stats for {discord_id} with pity boost {self.config.new_user_pity_boost}"
                )
                return stats

        except Exception as exc:
            log.exception(f"Failed to get user stats for {discord_id}: {exc}")
            # Fallback to default stats
            return UserStats(discord_id=discord_id)

    async def _save_user_stats(self, stats: UserStats) -> None:
        """Save user statistics to database."""
        if self.table is None:
            return

        try:
            item = {
                "giveaway_id": "USER_STATS",
                "user_id": f"STATS#{stats.discord_id}",
                **stats.to_dynamodb_item(),
            }

            self.table.put_item(Item=item)
            log.debug(f"Saved stats for user {stats.discord_id}")

        except Exception as exc:
            log.exception(f"Failed to save user stats for {stats.discord_id}: {exc}")

    def calculate_selection_weight(
        self, discord_id: str, stats: UserStats, giveaway_type: str, pool_size: int
    ) -> float:
        """
        Calculate selection weight for a user based on multiple factors.

        Args:
            discord_id: Discord user ID
            stats: User statistics
            giveaway_type: Type of giveaway (goldpass/giftcard)
            pool_size: Total number of participants

        Returns:
            Final weight for selection (higher = more likely to be selected)
        """
        now = datetime.datetime.now(tz=datetime.UTC)
        base_weight = 1.0

        # 1. Pity multiplier (most important factor)
        pity_weight = min(stats.current_pity, self.config.max_pity_multiplier)

        # 2. Participation bonus (reward consistent participants)
        participation_bonus = 1.0
        if stats.total_entries >= self.config.participation_threshold:
            participation_bonus = min(
                1.0 + (stats.total_entries * 0.02),  # 2% bonus per entry
                self.config.participation_bonus_cap,
            )

        # 3. Recency factor (recent participation gets slight bonus)
        recency_factor = 1.0
        if stats.last_entry_date:
            days_since_entry = (now - stats.last_entry_date).days
            if days_since_entry <= 7:  # Recent entry bonus
                recency_factor = 1.1
            elif days_since_entry > self.config.inactive_user_days:  # Inactive penalty
                recency_factor = 0.8

        # 4. Cooldown penalties (reduce weight for recent winners)
        cooldown_factor = 1.0
        if stats.last_win_date:
            days_since_win = (now - stats.last_win_date).days

            if (
                giveaway_type == "goldpass"
                and days_since_win < self.config.major_win_cooldown_days
            ):
                cooldown_factor = self.config.cooldown_weight_reduction
            elif (
                giveaway_type == "giftcard"
                and days_since_win < self.config.minor_win_cooldown_days
            ):
                cooldown_factor = self.config.cooldown_weight_reduction

        # 5. Small pool adjustments (reduce extreme weights for small pools)
        pool_adjustment = 1.0
        if pool_size <= self.config.small_pool_threshold:
            # Reduce pity impact for small pools to prevent domination
            pity_weight = 1.0 + ((pity_weight - 1.0) * 0.5)
            log.debug(f"Small pool detected ({pool_size}), reducing pity impact")

        # Calculate final weight
        final_weight = (
            base_weight
            * pity_weight
            * participation_bonus
            * recency_factor
            * cooldown_factor
            * pool_adjustment
        )

        log.debug(
            f"Weight calculation for {discord_id}: "
            f"base={base_weight:.2f}, pity={pity_weight:.2f}, "
            f"participation={participation_bonus:.2f}, recency={recency_factor:.2f}, "
            f"cooldown={cooldown_factor:.2f}, final={final_weight:.2f}"
        )

        return max(final_weight, 0.1)  # Ensure minimum weight

    async def select_winners_fairly(
        self, entries: list[str], giveaway_type: str, count: int
    ) -> list[str]:
        """
        Select winners using the fairness algorithm.

        Args:
            entries: List of Discord user IDs eligible for selection
            giveaway_type: Type of giveaway (goldpass/giftcard)
            count: Number of winners to select

        Returns:
            List of selected winner Discord IDs
        """
        if not entries or count <= 0:
            return []

        pool_size = len(entries)
        log.info(
            f"Selecting {count} winners from {pool_size} entries for {giveaway_type} giveaway"
        )

        # Get user stats and calculate weights
        user_weights = {}
        total_weight = 0.0

        for discord_id in entries:
            stats = await self.get_user_stats(discord_id)
            weight = self.calculate_selection_weight(
                discord_id, stats, giveaway_type, pool_size
            )
            user_weights[discord_id] = weight
            total_weight += weight

        # Handle edge case: more winners requested than participants
        actual_count = min(count, len(entries))
        winners = []
        remaining_entries = entries.copy()
        remaining_weights = user_weights.copy()

        # Select winners one by one using weighted random selection
        for i in range(actual_count):
            if not remaining_entries:
                break

            # Recalculate total weight for remaining participants
            current_total_weight = sum(remaining_weights.values())

            # Weighted random selection
            random_value = random.uniform(0, current_total_weight)
            cumulative_weight = 0.0

            selected_user = None
            for user_id in remaining_entries:
                cumulative_weight += remaining_weights[user_id]
                if random_value <= cumulative_weight:
                    selected_user = user_id
                    break

            # Fallback selection if something goes wrong
            if selected_user is None:
                selected_user = random.choice(remaining_entries)
                log.warning(f"Fallback random selection used for winner {i + 1}")

            winners.append(selected_user)
            remaining_entries.remove(selected_user)
            del remaining_weights[selected_user]

            log.info(
                f"Selected winner {i + 1}/{actual_count}: {selected_user} "
                f"(weight: {user_weights[selected_user]:.2f})"
            )

        return winners

    async def update_winner_stats(
        self, winners: list[str], giveaway_id: str, giveaway_type: str
    ) -> None:
        """
        Update statistics for winners and participants.

        Args:
            winners: List of winner Discord IDs
            giveaway_id: Giveaway identifier
            giveaway_type: Type of giveaway (goldpass/giftcard)
        """
        now = datetime.datetime.now(tz=datetime.UTC)

        # Update winner statistics
        for discord_id in winners:
            try:
                stats = await self.get_user_stats(discord_id)

                # Update win counts
                stats.total_wins += 1
                stats.last_win_date = now

                if giveaway_type == "goldpass":
                    stats.goldpass_wins += 1
                    # Major win: reset pity completely
                    stats.current_pity = self.config.major_win_pity_reset
                    stats.last_reset_date = now
                    log.info(
                        f"Major win for {discord_id}, pity reset to {stats.current_pity}"
                    )

                elif giveaway_type == "giftcard":
                    stats.giftcard_wins += 1
                    # Minor win: reduce pity but don't reset completely
                    stats.current_pity = max(
                        1.0, stats.current_pity * self.config.minor_win_pity_reduction
                    )
                    log.info(
                        f"Minor win for {discord_id}, pity reduced to {stats.current_pity:.2f}"
                    )

                await self._save_user_stats(stats)

                # Log winner selection for analytics
                await self._log_winner_selection(discord_id, giveaway_id, giveaway_type)

            except Exception as exc:
                log.exception(f"Failed to update winner stats for {discord_id}: {exc}")

    async def _log_winner_selection(
        self, discord_id: str, giveaway_id: str, giveaway_type: str
    ) -> None:
        """Log winner selection for analytics and transparency."""
        if self.table is None:
            return

        try:
            item = {
                "giveaway_id": "WINNER_HISTORY",
                "user_id": f"HISTORY#{datetime.datetime.now(tz=datetime.UTC).isoformat()}#{uuid.uuid4().hex[:8]}",
                "winner_discord_id": discord_id,
                "original_giveaway_id": giveaway_id,
                "giveaway_type": giveaway_type,
                "selection_timestamp": datetime.datetime.now(
                    tz=datetime.UTC
                ).isoformat(),
            }

            self.table.put_item(Item=item)
            log.debug(f"Logged winner selection: {discord_id} for {giveaway_id}")

        except Exception as exc:
            log.exception(f"Failed to log winner selection: {exc}")

    async def update_participation_stats(
        self, participants: list[str], giveaway_id: str
    ) -> None:
        """
        Update participation statistics for all entrants.

        Args:
            participants: List of all participant Discord IDs
            giveaway_id: Giveaway identifier
        """
        now = datetime.datetime.now(tz=datetime.UTC)

        for discord_id in participants:
            try:
                stats = await self.get_user_stats(discord_id)

                # Update participation counts
                stats.total_entries += 1
                stats.last_entry_date = now

                # Update participation streak (simplified - just increment for now)
                stats.participation_streak += 1

                # Increase pity for non-winners (will be reset if they win)
                if stats.current_pity < self.config.max_pity_multiplier:
                    stats.current_pity = min(
                        stats.current_pity + self.config.base_pity_increment,
                        self.config.max_pity_multiplier,
                    )

                await self._save_user_stats(stats)

            except Exception as exc:
                log.exception(
                    f"Failed to update participation stats for {discord_id}: {exc}"
                )

    async def apply_time_based_decay(self) -> None:
        """
        Apply time-based pity decay for inactive users.
        Should be called periodically (e.g., daily/weekly).
        """
        if self.table is None:
            return

        try:
            # Query all user stats
            resp = self.table.query(
                KeyConditionExpression=conditions.Key("giveaway_id").eq("USER_STATS")
                & conditions.Key("user_id").begins_with("STATS#")
            )

            now = datetime.datetime.now(tz=datetime.UTC)
            decay_count = 0

            for item in resp.get("Items", []):
                try:
                    stats = UserStats.from_dynamodb_item(item)

                    # Apply decay if user has been inactive
                    if stats.last_entry_date:
                        days_inactive = (now - stats.last_entry_date).days
                        if days_inactive > self.config.inactive_user_days:
                            months_inactive = days_inactive / 30.0
                            decay_factor = self.config.pity_decay_rate * months_inactive

                            old_pity = stats.current_pity
                            stats.current_pity = max(
                                1.0, stats.current_pity - decay_factor
                            )

                            if old_pity != stats.current_pity:
                                await self._save_user_stats(stats)
                                decay_count += 1
                                log.debug(
                                    f"Applied pity decay to {stats.discord_id}: "
                                    f"{old_pity:.2f} -> {stats.current_pity:.2f}"
                                )

                except Exception as exc:
                    log.exception(f"Failed to apply decay to user stats: {exc}")

            if decay_count > 0:
                log.info(f"Applied pity decay to {decay_count} inactive users")

        except Exception as exc:
            log.exception(f"Failed to apply time-based decay: {exc}")

    async def get_fairness_analytics(self) -> dict:
        """
        Get analytics data about fairness distribution.

        Returns:
            Dictionary containing fairness metrics
        """
        if self.table is None:
            return {"error": "Table not available"}

        try:
            # Get all user stats
            resp = self.table.query(
                KeyConditionExpression=conditions.Key("giveaway_id").eq("USER_STATS")
                & conditions.Key("user_id").begins_with("STATS#")
            )

            users = []
            total_pity = 0.0
            total_wins = 0
            total_entries = 0

            for item in resp.get("Items", []):
                stats = UserStats.from_dynamodb_item(item)
                users.append(stats)
                total_pity += stats.current_pity
                total_wins += stats.total_wins
                total_entries += stats.total_entries

            user_count = len(users)
            if user_count == 0:
                return {"message": "No user data available"}

            # Calculate metrics
            avg_pity = total_pity / user_count
            avg_wins = total_wins / user_count if user_count > 0 else 0
            avg_entries = total_entries / user_count if user_count > 0 else 0

            # Find users with extreme pity values
            high_pity_users = [u for u in users if u.current_pity > 3.0]
            never_won_users = [
                u for u in users if u.total_wins == 0 and u.total_entries > 5
            ]

            return {
                "total_users": user_count,
                "average_pity": round(avg_pity, 2),
                "average_wins": round(avg_wins, 2),
                "average_entries": round(avg_entries, 2),
                "high_pity_count": len(high_pity_users),
                "never_won_count": len(never_won_users),
                "system_health": "good" if avg_pity < 2.5 else "needs_attention",
            }

        except Exception as exc:
            log.exception(f"Failed to get fairness analytics: {exc}")
            return {"error": str(exc)}

    def should_reset_population_pity(self, avg_pity: float) -> bool:
        """
        Determine if population-wide pity reset is needed.

        Args:
            avg_pity: Average pity across all users

        Returns:
            True if population reset should be applied
        """
        # Reset if average pity gets too high (indicates system imbalance)
        return avg_pity > 3.0

    async def apply_population_pity_reset(self, reset_factor: float = 0.5) -> None:
        """
        Apply gentle pity reduction across all users.

        Args:
            reset_factor: Factor to multiply all pity values by (0.5 = 50% reduction)
        """
        if self.table is None:
            return

        try:
            resp = self.table.query(
                KeyConditionExpression=conditions.Key("giveaway_id").eq("USER_STATS")
                & conditions.Key("user_id").begins_with("STATS#")
            )

            reset_count = 0
            now = datetime.datetime.now(tz=datetime.UTC)

            for item in resp.get("Items", []):
                try:
                    stats = UserStats.from_dynamodb_item(item)

                    old_pity = stats.current_pity
                    stats.current_pity = max(1.0, stats.current_pity * reset_factor)
                    stats.last_reset_date = now

                    await self._save_user_stats(stats)
                    reset_count += 1

                    log.debug(
                        f"Population reset for {stats.discord_id}: "
                        f"{old_pity:.2f} -> {stats.current_pity:.2f}"
                    )

                except Exception as exc:
                    log.exception(f"Failed to reset user pity: {exc}")

            log.info(
                f"Applied population pity reset to {reset_count} users "
                f"with factor {reset_factor}"
            )

        except Exception as exc:
            log.exception(f"Failed to apply population pity reset: {exc}")


# Convenience function for easy integration
async def select_fair_winners(
    table, entries: list[str], giveaway_type: str, count: int
) -> list[str]:
    """
    Convenience function for fair winner selection.

    Args:
        table: DynamoDB table for statistics
        entries: List of participant Discord IDs
        giveaway_type: Type of giveaway (goldpass/giftcard)
        count: Number of winners needed

    Returns:
        List of selected winner Discord IDs
    """
    fairness = GiveawayFairness(table)
    return await fairness.select_winners_fairly(entries, giveaway_type, count)


# Convenience function for updating stats after winner selection
async def update_giveaway_stats(
    table,
    winners: list[str],
    participants: list[str],
    giveaway_id: str,
    giveaway_type: str,
) -> None:
    """
    Convenience function for updating all giveaway statistics.

    Args:
        table: DynamoDB table for statistics
        winners: List of winner Discord IDs
        participants: List of all participant Discord IDs
        giveaway_id: Giveaway identifier
        giveaway_type: Type of giveaway (goldpass/giftcard)
    """
    fairness = GiveawayFairness(table)

    # Update participation stats for all participants
    await fairness.update_participation_stats(participants, giveaway_id)

    # Update winner-specific stats
    await fairness.update_winner_stats(winners, giveaway_id, giveaway_type)
