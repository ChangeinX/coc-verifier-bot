from datetime import datetime, timedelta

from tournament_bot import PlayerEntry, TeamRegistration
from tournament_bot.bracket import (
    create_bracket_state,
    render_bracket,
    set_match_winner,
    simulate_tournament,
)


def make_reg(user_id: int, offset_seconds: int) -> TeamRegistration:
    registered_at = (
        datetime.fromisoformat("2024-01-01T00:00:00+00:00")
        + timedelta(seconds=offset_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return TeamRegistration(
        guild_id=7,
        user_id=user_id,
        user_name=f"Team{user_id}",
        players=[
            PlayerEntry(
                name=f"Player{user_id}",
                tag=f"#P{user_id:03d}",
                town_hall=16,
            )
        ],
        registered_at=registered_at,
    )


def test_create_bracket_assigns_seeds_in_standard_order():
    registrations = [
        make_reg(1, 0),
        make_reg(2, 30),
        make_reg(3, 60),
        make_reg(4, 90),
    ]
    bracket = create_bracket_state(7, registrations)

    first_round = bracket.rounds[0]
    assert first_round.name == "Semifinals"
    match_ids = [match.match_id for match in first_round.matches]
    assert match_ids == ["R1M1", "R1M2"]

    top_match = first_round.matches[0]
    assert top_match.competitor_one.seed == 1
    assert top_match.competitor_one.team_id == registrations[0].user_id
    assert top_match.competitor_two.seed == 4
    assert top_match.competitor_two.team_id == registrations[3].user_id


def test_create_bracket_auto_assigns_byes():
    registrations = [make_reg(10, 0), make_reg(11, 30), make_reg(12, 60)]
    bracket = create_bracket_state(7, registrations)

    first_round = bracket.rounds[0]
    assert first_round.name == "Semifinals"
    bye_matches = [
        match
        for match in first_round.matches
        if match.competitor_one.team_id is None or match.competitor_two.team_id is None
    ]
    assert len(bye_matches) == 1
    assert bye_matches[0].winner_index is not None

    second_round = bracket.rounds[1]
    finalist_slots = {
        slot.team_id
        for match in second_round.matches
        for slot in (match.competitor_one, match.competitor_two)
        if slot.team_id is not None
    }
    assert len(finalist_slots) >= 1


def test_set_match_winner_advances_to_next_round():
    registrations = [make_reg(1, 0), make_reg(2, 30), make_reg(3, 60), make_reg(4, 90)]
    bracket = create_bracket_state(7, registrations)

    set_match_winner(bracket, "R1M1", 1)
    match = bracket.find_match("R1M1")
    assert match is not None
    assert match.winner_index == 0
    semifinal_winner = match.winner_slot()
    assert semifinal_winner is not None

    final_match = bracket.find_match("R2M1")
    assert final_match is not None
    finalist_ids = {
        final_match.competitor_one.team_id,
        final_match.competitor_two.team_id,
    }
    assert semifinal_winner.team_id in finalist_ids


def test_simulate_tournament_produces_snapshots_and_champion():
    registrations = [make_reg(1, 0), make_reg(2, 30), make_reg(3, 60), make_reg(4, 90)]
    bracket = create_bracket_state(7, registrations)

    final_state, snapshots = simulate_tournament(bracket)
    assert len(snapshots) == len(bracket.rounds) + 1
    assert snapshots[0][0] == "Initial Bracket"
    assert snapshots[-1][0] == "After Final"

    final_match = final_state.rounds[-1].matches[-1]
    assert final_match.winner_index is not None

    output = render_bracket(final_state)
    assert "Champion:" in output


def test_render_bracket_shrinks_completed_rounds():
    registrations = [make_reg(1, 0), make_reg(2, 30), make_reg(3, 60), make_reg(4, 90)]
    bracket = create_bracket_state(7, registrations)
    _, snapshots = simulate_tournament(bracket)

    after_semifinals = snapshots[1][1]

    full = render_bracket(after_semifinals)
    shrunk = render_bracket(after_semifinals, shrink_completed=True)

    assert full.splitlines()[0] == "Semifinals"
    assert shrunk.splitlines()[0] == "Final"
