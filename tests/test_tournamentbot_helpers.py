from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import tournamentbot
from tournament_bot import (
    BracketRound,
    BracketState,
    PlayerEntry,
    RoundWindowDefinition,
    TeamRegistration,
    TournamentConfig,
    TournamentRoundWindows,
    TournamentSeries,
    utc_now_iso,
)
from tournament_bot.bracket import create_bracket_state


def test_build_setup_overview_embed_lists_divisions():
    series = TournamentSeries(
        guild_id=1,
        registration_opens_at="2024-05-01T18:00:00.000Z",
        registration_closes_at="2024-05-10T18:00:00.000Z",
        updated_by=7,
        updated_at="2024-01-01T00:00:00.000Z",
    )
    config = TournamentConfig(
        guild_id=1,
        division_id="th15",
        division_name="TH15",
        team_size=5,
        allowed_town_halls=[15, 16],
        max_teams=32,
        updated_by=7,
        updated_at="2024-01-01T00:00:00.000Z",
    )

    embed = tournamentbot.build_setup_overview_embed(series, [config])

    registration_field = next(
        field for field in embed.fields if field.name == "Registration Window"
    )
    assert "May" in registration_field.value
    division_field = next(
        field for field in embed.fields if field.name.startswith("TH15 (th15)")
    )
    assert "Team size: 5" in division_field.value
    assert "Allowed TH: 15, 16" in division_field.value


def test_parse_round_window_spec_supports_multiple_rounds():
    rounds = [
        BracketRound(name="Quarterfinals", matches=[]),
        BracketRound(name="Semifinals", matches=[]),
    ]
    spec = (
        "R1=2024-05-01T18:00..2024-05-02T18:00; "
        "Semifinals=2024-05-03T18:00..2024-05-04T18:00"
    )

    updates = tournamentbot.parse_round_window_spec(spec, rounds)

    assert set(updates.keys()) == {0, 1}
    first_open, first_close = updates[0]
    assert first_open == tournamentbot.isoformat_utc(
        datetime(2024, 5, 1, 18, tzinfo=UTC)
    )
    assert first_close == tournamentbot.isoformat_utc(
        datetime(2024, 5, 2, 18, tzinfo=UTC)
    )

    second_open, second_close = updates[1]
    assert second_open == tournamentbot.isoformat_utc(
        datetime(2024, 5, 3, 18, tzinfo=UTC)
    )
    assert second_close == tournamentbot.isoformat_utc(
        datetime(2024, 5, 4, 18, tzinfo=UTC)
    )


def test_parse_round_window_spec_rejects_unknown_round():
    rounds = [BracketRound(name="Final", matches=[])]
    spec = "Quarterfinals=2024-05-01T18:00..2024-05-02T18:00"

    with pytest.raises(tournamentbot.InvalidValueError):
        tournamentbot.parse_round_window_spec(spec, rounds)


def test_apply_round_windows_to_bracket_assigns_all_rounds():
    rounds = [
        BracketRound(name="Quarterfinals", matches=[]),
        BracketRound(name="Semifinals", matches=[]),
    ]
    bracket = BracketState(
        guild_id=1,
        division_id="th15",
        created_at="2024-01-01T00:00:00.000Z",
        rounds=rounds,
    )
    config = TournamentRoundWindows(
        guild_id=1,
        rounds=[
            RoundWindowDefinition(
                position=1,
                opens_at=tournamentbot.isoformat_utc(
                    datetime(2024, 5, 1, 18, tzinfo=UTC)
                ),
                closes_at=tournamentbot.isoformat_utc(
                    datetime(2024, 5, 3, 18, tzinfo=UTC)
                ),
            ),
            RoundWindowDefinition(
                position=2,
                opens_at=tournamentbot.isoformat_utc(
                    datetime(2024, 5, 4, 18, tzinfo=UTC)
                ),
                closes_at=tournamentbot.isoformat_utc(
                    datetime(2024, 5, 6, 18, tzinfo=UTC)
                ),
            ),
        ],
        updated_by=1,
        updated_at="2024-05-01T00:00:00.000Z",
    )

    changed, aligned, cleared = tournamentbot.apply_round_windows_to_bracket(
        bracket, config, clear_missing=True
    )

    assert changed is True
    assert aligned == 2
    assert cleared == 0
    assert bracket.rounds[0].window_opens_at == config.rounds[0].opens_at
    assert bracket.rounds[1].window_closes_at == config.rounds[1].closes_at


def test_apply_round_windows_to_bracket_clears_missing_rounds():
    rounds = [
        BracketRound(name="Quarterfinals", matches=[]),
        BracketRound(name="Semifinals", matches=[]),
    ]
    rounds[1].window_opens_at = "2024-05-05T18:00:00.000Z"
    rounds[1].window_closes_at = "2024-05-07T18:00:00.000Z"
    bracket = BracketState(
        guild_id=1,
        division_id="th15",
        created_at="2024-01-01T00:00:00.000Z",
        rounds=rounds,
    )
    config = TournamentRoundWindows(
        guild_id=1,
        rounds=[
            RoundWindowDefinition(
                position=1,
                opens_at=tournamentbot.isoformat_utc(
                    datetime(2024, 5, 1, 18, tzinfo=UTC)
                ),
                closes_at=tournamentbot.isoformat_utc(
                    datetime(2024, 5, 3, 18, tzinfo=UTC)
                ),
            )
        ],
        updated_by=1,
        updated_at="2024-05-01T00:00:00.000Z",
    )

    changed, aligned, cleared = tournamentbot.apply_round_windows_to_bracket(
        bracket, config, clear_missing=True
    )

    assert changed is True
    assert aligned == 1
    assert cleared == 1
    assert bracket.rounds[0].window_opens_at == config.rounds[0].opens_at
    assert bracket.rounds[1].window_opens_at is None
    assert bracket.rounds[1].window_closes_at is None


def test_ensure_guild_validates_presence():
    interaction = SimpleNamespace(guild="guild")
    assert tournamentbot.ensure_guild(interaction) == "guild"

    interaction_missing = SimpleNamespace(guild=None)
    with pytest.raises(RuntimeError):
        tournamentbot.ensure_guild(interaction_missing)


@pytest.mark.asyncio
async def test_fetch_players_returns_entries(monkeypatch):
    async def fake_get_player(_client, _email, _password, tag):
        return SimpleNamespace(
            name=f"Player{tag[-1]}",
            town_hall_level=17,
            clan=SimpleNamespace(name="Clan", tag="#CLAN"),
        )

    monkeypatch.setattr(tournamentbot.coc_api, "get_player_with_retry", fake_get_player)
    tournamentbot.coc_client = SimpleNamespace()

    players = await tournamentbot.fetch_players(["#AAA111", "#BBB222"])

    assert [player.tag for player in players] == ["#AAA111", "#BBB222"]
    assert all(player.town_hall == 17 for player in players)
    assert players[0].clan_name == "Clan"


@pytest.mark.asyncio
async def test_fetch_players_raises_when_missing_data(monkeypatch):
    async def fake_get_player(_client, _email, _password, tag):
        if tag == "#AAA111":
            return SimpleNamespace(name="PlayerA", town_hall=None, town_hall_level=None)
        return None

    monkeypatch.setattr(tournamentbot.coc_api, "get_player_with_retry", fake_get_player)
    tournamentbot.coc_client = SimpleNamespace()

    with pytest.raises(tournamentbot.InvalidValueError):
        await tournamentbot.fetch_players(["#AAA111", "#BBB222"])


@pytest.mark.asyncio
async def test_build_seeded_registrations_for_guild(monkeypatch):
    monkeypatch.setattr(tournamentbot, "COC_EMAIL", "email")
    monkeypatch.setattr(tournamentbot, "COC_PASSWORD", "password")
    monkeypatch.setattr(tournamentbot, "coc_client", SimpleNamespace(), raising=False)

    async def fake_build(client, email, password, guild_id, division_id, **kwargs):
        assert client is tournamentbot.coc_client
        assert email == "email"
        assert password == "password"
        assert guild_id == 99
        assert division_id == "th12"
        assert kwargs.get("shuffle") is True
        return ["registration"]

    monkeypatch.setattr(tournamentbot, "build_seeded_registrations", fake_build)

    result = await tournamentbot.build_seeded_registrations_for_guild(99, "th12")

    assert result == ["registration"]


def test_format_lineup_table_marks_substitute():
    players = [
        PlayerEntry(name=f"Player{idx}", tag=f"#TAG{idx}", town_hall=16)
        for idx in range(1, 6)
    ]
    substitute = PlayerEntry(name="Bench", tag="#SUB1", town_hall=15)

    table = tournamentbot.format_lineup_table(players, substitute=substitute)

    assert "Bench" in table
    assert table.splitlines()[-1].startswith("Bench")
    assert "(Sub)" in table.splitlines()[-1]


def test_build_registration_embed_includes_team_name_and_substitute():
    players = [
        PlayerEntry(name=f"Player{idx}", tag=f"#TAG{idx}", town_hall=16)
        for idx in range(1, 6)
    ]
    substitute = PlayerEntry(name="Bench", tag="#SUB1", town_hall=15)
    registration = TeamRegistration(
        guild_id=1,
        division_id="th15",
        user_id=2,
        user_name="Captain",
        players=players,
        registered_at=utc_now_iso(),
        team_name="Legends",
        substitute=substitute,
    )
    config = TournamentConfig(
        guild_id=1,
        division_id="th15",
        division_name="TH15",
        team_size=5,
        allowed_town_halls=[15, 16],
        max_teams=8,
        updated_by=1,
        updated_at="2024-01-01T00:00:00.000Z",
    )
    series = TournamentSeries(
        guild_id=1,
        registration_opens_at="2024-01-01T00:00:00.000Z",
        registration_closes_at="2024-01-05T00:00:00.000Z",
        updated_by=1,
        updated_at="2024-01-01T00:00:00.000Z",
    )

    embed = tournamentbot.build_registration_embed(
        registration,
        config=config,
        series=series,
        is_update=False,
    )

    assert "Legends" in embed.title
    required_field = next(
        field for field in embed.fields if field.name == "Team Size (Required)"
    )
    assert required_field.value == "5"
    team_size_field = next(field for field in embed.fields if field.name == "Team Size")
    assert "5 starters" in team_size_field.value
    assert "+ 1 sub" in team_size_field.value


def test_infer_division_defaults_single_level():
    name, allowed, team_size = tournamentbot.infer_division_defaults("th12-1v1")
    assert name == "TH12 1V1"
    assert allowed == [12]
    assert team_size == 1


def test_infer_division_defaults_range():
    name, allowed, team_size = tournamentbot.infer_division_defaults("th12-17")
    assert name == "TH12 17"
    assert allowed == list(range(12, 18))
    assert team_size == 5


def test_is_tournament_admin_checks_role_membership():
    admin_role = SimpleNamespace(id=tournamentbot.TOURNAMENT_ADMIN_ROLE_ID)
    member = SimpleNamespace(roles=[admin_role])
    assert tournamentbot.is_tournament_admin(member) is True

    non_admin = SimpleNamespace(roles=[SimpleNamespace(id=999)])
    assert tournamentbot.is_tournament_admin(non_admin) is False

    missing_roles = SimpleNamespace()
    assert tournamentbot.is_tournament_admin(missing_roles) is False


def make_member(user_id: int, *, roles: list[object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, roles=roles or [])


def test_resolve_registration_owner_self():
    actor = make_member(10)
    interaction = SimpleNamespace(user=actor)

    owner, is_admin = tournamentbot.resolve_registration_owner(interaction, None)

    assert owner is actor
    assert is_admin is False


def test_resolve_registration_owner_admin_for_other():
    admin_role = SimpleNamespace(id=tournamentbot.TOURNAMENT_ADMIN_ROLE_ID)
    actor = make_member(10, roles=[admin_role])
    target = make_member(20)
    interaction = SimpleNamespace(user=actor)

    owner, is_admin = tournamentbot.resolve_registration_owner(interaction, target)

    assert owner is target
    assert is_admin is True


def test_resolve_registration_owner_rejects_non_admin():
    actor = make_member(10)
    target = make_member(20)
    interaction = SimpleNamespace(user=actor)

    with pytest.raises(PermissionError):
        tournamentbot.resolve_registration_owner(interaction, target)


def _simple_bracket() -> tuple[tournamentbot.BracketState, str, list[TeamRegistration]]:
    base_time = utc_now_iso()
    registrations = [
        TeamRegistration(
            guild_id=1,
            division_id="solo",
            user_id=101,
            user_name="Alpha",
            team_name="Alpha",
            players=[PlayerEntry(name="Alpha", tag="#AAA", town_hall=16)],
            registered_at=base_time,
        ),
        TeamRegistration(
            guild_id=1,
            division_id="solo",
            user_id=202,
            user_name="Bravo",
            team_name="Bravo",
            players=[PlayerEntry(name="Bravo", tag="#BBB", town_hall=16)],
            registered_at=base_time,
        ),
    ]
    bracket = create_bracket_state(1, "solo", registrations)
    match_id = bracket.rounds[0].matches[0].match_id
    return bracket, match_id, registrations


def test_collect_review_predictions_uses_low_confidence_fallback():
    bracket, match_id, _ = _simple_bracket()
    low_confidence = SimpleNamespace(
        match_id=match_id,
        winner_slot=0,
        winner_label="Alpha",
        confidence=0.45,
        method="mentions",
        evidence=["Alpha wins"],
        scores={"Alpha": None, "Bravo": None},
    )

    predictions, fallback = tournamentbot._collect_review_predictions(
        bracket, [low_confidence]
    )

    assert predictions == {match_id: low_confidence}
    assert fallback is low_confidence


def test_collect_review_predictions_prefers_high_confidence():
    bracket, match_id, _ = _simple_bracket()
    high_confidence = SimpleNamespace(
        match_id=match_id,
        winner_slot=0,
        winner_label="Alpha",
        confidence=0.82,
        method="score",
        evidence=["Alpha 82%"],
        scores={"Alpha": 82.0, "Bravo": 14.0},
    )

    predictions, fallback = tournamentbot._collect_review_predictions(
        bracket, [high_confidence]
    )

    assert predictions == {match_id: high_confidence}
    assert fallback is None


def test_collect_review_predictions_skips_completed_matches():
    bracket, match_id, _ = _simple_bracket()
    match = bracket.find_match(match_id)
    assert match is not None
    match.winner_index = 0
    result = SimpleNamespace(
        match_id=match_id,
        winner_slot=0,
        winner_label="Alpha",
        confidence=0.75,
        method="score",
        evidence=["Alpha 75%"],
        scores={"Alpha": 75.0, "Bravo": 10.0},
    )

    predictions, fallback = tournamentbot._collect_review_predictions(bracket, [result])

    assert predictions == {}
    assert fallback is None


def test_build_winner_update_lines_use_in_game_name():
    bracket, match_id, registrations = _simple_bracket()
    match = bracket.find_match(match_id)
    assert match is not None
    match.winner_index = 0
    registration_lookup = {
        registration.user_id: registration for registration in registrations
    }

    lines = tournamentbot.build_winner_update_lines(
        bracket,
        match,
        registration_lookup=registration_lookup,
        champion=None,
    )

    assert any("Winner (in-game): Alpha" in line for line in lines)
    assert all("Captain:" not in line for line in lines)
