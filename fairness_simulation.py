"""
Giveaway Fairness Simulation Framework

This module provides tools to simulate long-term giveaway behavior and validate
the fairness algorithm over extended periods. It helps identify potential issues
with the pity system, weight calculations, and distribution fairness.

The simulation can model:
- Different participant pool sizes and behaviors
- Various giveaway frequencies and types
- User activity patterns (consistent vs sporadic participation)
- Long-term fairness distribution
"""

import asyncio
import datetime
import json
import random
import statistics
from dataclasses import dataclass, field

from giveaway_fairness import FairnessConfig, GiveawayFairness, UserStats


@dataclass
class SimulationUser:
    """Represents a user in the simulation."""

    discord_id: str
    participation_rate: float  # Probability of entering each giveaway (0.0-1.0)
    activity_pattern: str = "consistent"  # consistent, sporadic, seasonal
    joined_date: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.UTC)
    )

    def should_participate(self, giveaway_number: int) -> bool:
        """Determine if user should participate in this giveaway."""
        if self.activity_pattern == "consistent":
            return random.random() < self.participation_rate
        elif self.activity_pattern == "sporadic":
            # More random participation
            return random.random() < (
                self.participation_rate * random.uniform(0.3, 1.7)
            )
        elif self.activity_pattern == "seasonal":
            # Participation varies with giveaway number (simulating seasonal activity)
            seasonal_modifier = 0.8 + 0.4 * abs(((giveaway_number % 30) - 15) / 15)
            return random.random() < (self.participation_rate * seasonal_modifier)
        return False


@dataclass
class GiveawayEvent:
    """Represents a giveaway event in the simulation."""

    giveaway_id: str
    giveaway_type: str  # goldpass or giftcard
    winners_needed: int
    date: datetime.datetime
    participants: list[str] = field(default_factory=list)
    winners: list[str] = field(default_factory=list)


@dataclass
class SimulationResults:
    """Results from a fairness simulation run."""

    total_giveaways: int
    total_participants: int
    win_distribution: dict[str, int]  # user_id -> win count
    participation_distribution: dict[str, int]  # user_id -> participation count
    average_pity_over_time: list[float]
    fairness_metrics: dict[str, float]
    population_resets: int
    final_user_stats: dict[str, UserStats]


class MockDynamoDBTable:
    """Mock DynamoDB table for simulation testing."""

    def __init__(self):
        self.data = {}

    def get_item(self, Key):
        """Mock get_item operation."""
        key = f"{Key['giveaway_id']}#{Key['user_id']}"
        if key in self.data:
            return {"Item": self.data[key]}
        return {}

    def put_item(self, Item):
        """Mock put_item operation."""
        key = f"{Item['giveaway_id']}#{Item['user_id']}"
        self.data[key] = Item.copy()

    def query(self, KeyConditionExpression=None, **kwargs):
        """Mock query operation."""
        # Simple implementation for testing
        items = []
        if "USER_STATS" in str(KeyConditionExpression):
            for key, item in self.data.items():
                if key.startswith("USER_STATS#STATS#"):
                    items.append(item)
        return {"Items": items}

    def clear(self):
        """Clear all data."""
        self.data.clear()


class FairnessSimulation:
    """Main simulation framework for testing fairness over time."""

    def __init__(self, config: FairnessConfig | None = None):
        """Initialize simulation with configuration."""
        self.config = config or FairnessConfig()
        self.mock_table = MockDynamoDBTable()
        self.fairness = GiveawayFairness(self.mock_table, self.config)
        self.users: list[SimulationUser] = []
        self.giveaways: list[GiveawayEvent] = []
        self.results: SimulationResults | None = None

    def create_user_population(self, population_configs: list[dict]) -> None:
        """
        Create simulated user population.

        Args:
            population_configs: List of config dicts with 'count', 'participation_rate', 'pattern'
        """
        self.users.clear()
        user_id = 1

        for config in population_configs:
            count = config.get("count", 10)
            participation_rate = config.get("participation_rate", 0.8)
            pattern = config.get("activity_pattern", "consistent")

            for _ in range(count):
                user = SimulationUser(
                    discord_id=str(user_id),
                    participation_rate=participation_rate,
                    activity_pattern=pattern,
                )
                self.users.append(user)
                user_id += 1

    def create_standard_population(self, size: int = 50) -> None:
        """Create a standard mixed population for testing."""
        configs = [
            {
                "count": int(size * 0.4),
                "participation_rate": 0.9,
                "activity_pattern": "consistent",
            },  # Regular participants
            {
                "count": int(size * 0.3),
                "participation_rate": 0.6,
                "activity_pattern": "sporadic",
            },  # Casual participants
            {
                "count": int(size * 0.2),
                "participation_rate": 0.8,
                "activity_pattern": "seasonal",
            },  # Seasonal participants
            {
                "count": int(size * 0.1),
                "participation_rate": 0.3,
                "activity_pattern": "sporadic",
            },  # Rare participants
        ]
        self.create_user_population(configs)

    async def run_simulation(
        self, weeks: int = 52, goldpass_per_month: int = 1, giftcards_per_week: int = 1
    ) -> SimulationResults:
        """
        Run complete fairness simulation.

        Args:
            weeks: Number of weeks to simulate
            goldpass_per_month: Gold pass giveaways per month
            giftcards_per_week: Gift card giveaways per week

        Returns:
            SimulationResults with detailed metrics
        """
        if not self.users:
            raise ValueError("No users created. Call create_user_population() first.")

        self.giveaways.clear()
        self.mock_table.clear()

        # Generate giveaway schedule
        start_date = datetime.datetime.now(tz=datetime.UTC)
        giveaway_id = 1

        for week in range(weeks):
            week_start = start_date + datetime.timedelta(weeks=week)

            # Add gift card giveaways for this week
            for gc in range(giftcards_per_week):
                giveaway = GiveawayEvent(
                    giveaway_id=f"giftcard-{giveaway_id}",
                    giveaway_type="giftcard",
                    winners_needed=3,
                    date=week_start
                    + datetime.timedelta(days=gc * 7 // giftcards_per_week),
                )
                self.giveaways.append(giveaway)
                giveaway_id += 1

            # Add gold pass giveaways (monthly)
            if week % 4 == 0:  # Roughly monthly
                for _gp in range(goldpass_per_month):
                    giveaway = GiveawayEvent(
                        giveaway_id=f"goldpass-{giveaway_id}",
                        giveaway_type="goldpass",
                        winners_needed=1,
                        date=week_start + datetime.timedelta(days=3),
                    )
                    self.giveaways.append(giveaway)
                    giveaway_id += 1

        # Run simulation
        print(
            f"Starting simulation: {len(self.giveaways)} giveaways over {weeks} weeks with {len(self.users)} users"
        )

        average_pity_over_time = []
        population_resets = 0

        for i, giveaway in enumerate(self.giveaways):
            # Determine participants for this giveaway
            participants = []
            for user in self.users:
                if user.should_participate(i):
                    participants.append(user.discord_id)

            giveaway.participants = participants

            if participants:
                # Select winners using fairness algorithm
                winners = await self.fairness.select_winners_fairly(
                    participants, giveaway.giveaway_type, giveaway.winners_needed
                )
                giveaway.winners = winners

                # Update statistics
                await self.fairness.update_participation_stats(
                    participants, giveaway.giveaway_id
                )
                await self.fairness.update_winner_stats(
                    winners, giveaway.giveaway_id, giveaway.giveaway_type
                )

                # Check for population pity reset
                if i % 10 == 0:  # Check every 10 giveaways
                    analytics = await self.fairness.get_fairness_analytics()
                    if analytics.get("average_pity", 0) > 3.0:
                        await self.fairness.apply_population_pity_reset(0.6)
                        population_resets += 1
                        print(f"Applied population pity reset at giveaway {i + 1}")

                # Track average pity over time
                if i % 5 == 0:  # Sample every 5 giveaways
                    analytics = await self.fairness.get_fairness_analytics()
                    average_pity_over_time.append(analytics.get("average_pity", 1.0))

            if (i + 1) % 20 == 0:
                print(f"Completed {i + 1}/{len(self.giveaways)} giveaways")

        # Calculate final results
        self.results = await self._calculate_results(
            average_pity_over_time, population_resets
        )
        return self.results

    async def _calculate_results(
        self, pity_history: list[float], population_resets: int
    ) -> SimulationResults:
        """Calculate comprehensive simulation results."""

        # Count wins and participation per user
        win_distribution = {}
        participation_distribution = {}

        for giveaway in self.giveaways:
            # Count participation
            for participant in giveaway.participants:
                participation_distribution[participant] = (
                    participation_distribution.get(participant, 0) + 1
                )

            # Count wins
            for winner in giveaway.winners:
                win_distribution[winner] = win_distribution.get(winner, 0) + 1

        # Get final user stats
        final_user_stats = {}
        for user in self.users:
            stats = await self.fairness.get_user_stats(user.discord_id)
            final_user_stats[user.discord_id] = stats

        # Calculate fairness metrics
        fairness_metrics = self._calculate_fairness_metrics(
            win_distribution, participation_distribution, final_user_stats
        )

        return SimulationResults(
            total_giveaways=len(self.giveaways),
            total_participants=len(self.users),
            win_distribution=win_distribution,
            participation_distribution=participation_distribution,
            average_pity_over_time=pity_history,
            fairness_metrics=fairness_metrics,
            population_resets=population_resets,
            final_user_stats=final_user_stats,
        )

    def _calculate_fairness_metrics(
        self,
        win_dist: dict[str, int],
        participation_dist: dict[str, int],
        user_stats: dict[str, UserStats],
    ) -> dict[str, float]:
        """Calculate various fairness metrics."""

        # Basic distributions
        wins = list(win_dist.values()) if win_dist else [0]
        list(participation_dist.values()) if participation_dist else [0]

        # Users who participated but never won
        participated_users = set(participation_dist.keys())
        winner_users = set(win_dist.keys())
        never_won_users = participated_users - winner_users

        # Win rate analysis (wins per participation)
        win_rates = []
        for user_id in participated_users:
            user_wins = win_dist.get(user_id, 0)
            user_participations = participation_dist.get(user_id, 1)
            win_rates.append(user_wins / user_participations)

        # Pity distribution
        pity_values = [stats.current_pity for stats in user_stats.values()]

        return {
            "average_wins": statistics.mean(wins),
            "win_std_dev": statistics.stdev(wins) if len(wins) > 1 else 0,
            "max_wins": max(wins),
            "min_wins": min(wins),
            "never_won_percentage": len(never_won_users) / len(participated_users) * 100
            if participated_users
            else 0,
            "average_win_rate": statistics.mean(win_rates) if win_rates else 0,
            "win_rate_std_dev": statistics.stdev(win_rates)
            if len(win_rates) > 1
            else 0,
            "average_final_pity": statistics.mean(pity_values) if pity_values else 1.0,
            "max_final_pity": max(pity_values) if pity_values else 1.0,
            "fairness_score": self._calculate_fairness_score(win_rates, pity_values),
        }

    def _calculate_fairness_score(
        self, win_rates: list[float], pity_values: list[float]
    ) -> float:
        """
        Calculate overall fairness score (0-100, higher is more fair).

        Based on:
        - Low standard deviation in win rates (more even distribution)
        - Reasonable pity distribution (not too high on average)
        - Win rate distribution close to expected
        """
        if not win_rates or not pity_values:
            return 0.0

        # Penalize high standard deviation in win rates
        win_rate_fairness = (
            max(0, 100 - (statistics.stdev(win_rates) * 500))
            if len(win_rates) > 1
            else 100
        )

        # Penalize excessively high pity values
        avg_pity = statistics.mean(pity_values)
        pity_fairness = max(0, 100 - max(0, (avg_pity - 2.0) * 25))

        # Overall score (weighted average)
        return (win_rate_fairness * 0.7) + (pity_fairness * 0.3)

    def print_results_summary(self) -> None:
        """Print a summary of simulation results."""
        if not self.results:
            print("No simulation results available. Run simulation first.")
            return

        print("\n" + "=" * 60)
        print("GIVEAWAY FAIRNESS SIMULATION RESULTS")
        print("=" * 60)

        print(f"Simulation Period: {self.results.total_giveaways} giveaways")
        print(f"Participant Pool: {self.results.total_participants} users")
        print(f"Population Resets: {self.results.population_resets}")

        print("\nFAIRNESS METRICS:")
        for metric, value in self.results.fairness_metrics.items():
            if isinstance(value, float):
                print(f"  {metric.replace('_', ' ').title()}: {value:.2f}")
            else:
                print(f"  {metric.replace('_', ' ').title()}: {value}")

        print("\nWIN DISTRIBUTION SUMMARY:")
        wins = list(self.results.win_distribution.values())
        if wins:
            print(f"  Total Winners: {len(self.results.win_distribution)}")
            print(f"  Average Wins per Winner: {statistics.mean(wins):.2f}")
            print(f"  Most Wins by Single User: {max(wins)}")
            print(f"  Users with 5+ Wins: {sum(1 for w in wins if w >= 5)}")

        print("\nPITY SYSTEM PERFORMANCE:")
        pity_values = [s.current_pity for s in self.results.final_user_stats.values()]
        if pity_values:
            print(f"  Average Final Pity: {statistics.mean(pity_values):.2f}")
            print(f"  Max Final Pity: {max(pity_values):.2f}")
            print(f"  Users with Pity > 3.0: {sum(1 for p in pity_values if p > 3.0)}")

        # System health assessment
        fairness_score = self.results.fairness_metrics.get("fairness_score", 0)
        if fairness_score > 80:
            health = "EXCELLENT"
        elif fairness_score > 60:
            health = "GOOD"
        elif fairness_score > 40:
            health = "FAIR"
        else:
            health = "NEEDS IMPROVEMENT"

        print(f"\nOVERALL SYSTEM HEALTH: {health} (Score: {fairness_score:.1f}/100)")
        print("=" * 60)

    def export_results(self, filename: str) -> None:
        """Export detailed results to JSON file."""
        if not self.results:
            print("No results to export.")
            return

        # Convert results to JSON-serializable format
        export_data = {
            "simulation_config": {
                "total_giveaways": self.results.total_giveaways,
                "total_participants": self.results.total_participants,
                "population_resets": self.results.population_resets,
            },
            "fairness_metrics": self.results.fairness_metrics,
            "win_distribution": self.results.win_distribution,
            "participation_distribution": self.results.participation_distribution,
            "pity_history": self.results.average_pity_over_time,
            "final_user_stats": {
                uid: {
                    "total_entries": stats.total_entries,
                    "total_wins": stats.total_wins,
                    "current_pity": stats.current_pity,
                    "goldpass_wins": stats.goldpass_wins,
                    "giftcard_wins": stats.giftcard_wins,
                }
                for uid, stats in self.results.final_user_stats.items()
            },
        }

        with open(filename, "w") as f:
            json.dump(export_data, f, indent=2)

        print(f"Results exported to {filename}")


# Convenience functions for common simulation scenarios
async def run_basic_fairness_test(
    population_size: int = 50, weeks: int = 26
) -> SimulationResults:
    """Run basic fairness test with standard population."""
    sim = FairnessSimulation()
    sim.create_standard_population(population_size)
    results = await sim.run_simulation(weeks=weeks)
    sim.print_results_summary()
    return results


async def run_stress_test(
    population_size: int = 100, weeks: int = 104
) -> SimulationResults:
    """Run extended stress test with large population."""
    sim = FairnessSimulation()
    sim.create_standard_population(population_size)
    results = await sim.run_simulation(weeks=weeks, giftcards_per_week=2)
    sim.print_results_summary()
    return results


async def run_small_population_test(
    population_size: int = 10, weeks: int = 52
) -> SimulationResults:
    """Test fairness with small participant pool."""
    sim = FairnessSimulation()
    sim.create_standard_population(population_size)
    results = await sim.run_simulation(weeks=weeks)
    sim.print_results_summary()
    return results


if __name__ == "__main__":
    # Example simulation run
    async def main():
        print("Running basic fairness simulation...")
        await run_basic_fairness_test(population_size=30, weeks=52)

        print("\n\nRunning small population test...")
        await run_small_population_test(population_size=8, weeks=26)

    asyncio.run(main())
