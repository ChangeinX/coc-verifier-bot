from datetime import UTC, datetime

from tournament_bot.models import (
    BracketMatch,
    BracketRound,
    BracketSlot,
    BracketState,
    TeamRegistration,
)
from tournamentbot import categorize_captains_for_division


def make_registration(user_id: int, *, team_name: str) -> TeamRegistration:
    return TeamRegistration(
        guild_id=1,
        division_id="th15",
        user_id=user_id,
        user_name=team_name,
        players=[],
        registered_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        team_name=team_name,
    )


def test_categorize_without_bracket_marks_all_active() -> None:
    registrations = [
        make_registration(1, team_name="Alpha"),
        make_registration(2, team_name="Beta"),
    ]

    active, eliminated = categorize_captains_for_division(None, registrations)

    assert active == {1, 2}
    assert eliminated == set()


def test_categorize_with_bracket_marks_loser_eliminated() -> None:
    registrations = [
        make_registration(1, team_name="Alpha"),
        make_registration(2, team_name="Beta"),
        make_registration(3, team_name="Gamma"),
    ]

    match = BracketMatch(
        match_id="R1M1",
        round_index=0,
        competitor_one=BracketSlot(seed=1, team_id=1, team_label="Alpha"),
        competitor_two=BracketSlot(seed=2, team_id=2, team_label="Beta"),
        winner_index=0,
    )
    bracket = BracketState(
        guild_id=1,
        division_id="th15",
        created_at="2024-01-01T00:00:00.000Z",
        rounds=[BracketRound(name="Final", matches=[match])],
    )

    active, eliminated = categorize_captains_for_division(bracket, registrations)

    assert active == {1, 3}
    assert eliminated == {2}
