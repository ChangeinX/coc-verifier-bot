import pytest

from tournament_bot import (
    InvalidTownHallError,
    InvalidValueError,
    PlayerEntry,
    TeamRegistration,
    TournamentConfig,
    normalize_player_tag,
    parse_player_tags,
    parse_town_hall_levels,
    utc_now_iso,
    validate_max_teams,
    validate_team_size,
)


def test_parse_player_tags_accepts_hashless_input():
    tags = parse_player_tags("abc123 #DEF456")
    assert tags == ["#ABC123", "#DEF456"]


def test_parse_player_tags_rejects_duplicates():
    with pytest.raises(InvalidValueError):
        parse_player_tags("#AAA111 #AAA111")


def test_parse_player_tags_rejects_empty_input():
    with pytest.raises(InvalidValueError):
        parse_player_tags("   ")


def test_parse_player_tags_rejects_invalid_format():
    with pytest.raises(InvalidValueError):
        parse_player_tags("invalid!tag")


def test_parse_town_hall_levels_normalizes_and_sorts():
    levels = parse_town_hall_levels("17, 16, 16")
    assert levels == [16, 17]


def test_parse_town_hall_levels_rejects_out_of_range():
    with pytest.raises(InvalidTownHallError):
        parse_town_hall_levels("0, 16")


def test_parse_town_hall_levels_rejects_empty_input():
    with pytest.raises(InvalidTownHallError):
        parse_town_hall_levels("   ")


def test_parse_town_hall_levels_rejects_non_numeric():
    with pytest.raises(InvalidTownHallError):
        parse_town_hall_levels("16,abc")


def test_validate_team_size_enforces_increment():
    assert validate_team_size(10) == 10
    with pytest.raises(InvalidValueError):
        validate_team_size(9)


def test_validate_team_size_rejects_too_small_and_large():
    with pytest.raises(InvalidValueError):
        validate_team_size(4)
    with pytest.raises(InvalidValueError):
        validate_team_size(55)


def test_validate_max_teams_enforces_even():
    assert validate_max_teams(6) == 6
    with pytest.raises(InvalidValueError):
        validate_max_teams(5)


def test_validate_max_teams_rejects_out_of_bounds():
    with pytest.raises(InvalidValueError):
        validate_max_teams(1)
    with pytest.raises(InvalidValueError):
        validate_max_teams(400)


def test_normalize_player_tag_variants():
    assert normalize_player_tag(" #abc123 ") == "#ABC123"
    with pytest.raises(InvalidValueError):
        normalize_player_tag("")


def test_models_round_trip():
    config = TournamentConfig(
        guild_id=123,
        team_size=5,
        allowed_town_halls=[16, 17],
        max_teams=10,
        updated_by=42,
        updated_at=utc_now_iso(),
    )
    config_item = config.to_item()
    assert TournamentConfig.from_item(config_item) == config

    registration = TeamRegistration(
        guild_id=123,
        user_id=456,
        user_name="User#1234",
        players=[
            PlayerEntry(name="PlayerOne", tag="#AAA111", town_hall=16),
            PlayerEntry(name="PlayerTwo", tag="#BBB222", town_hall=17),
        ],
        registered_at=utc_now_iso(),
    )
    item = registration.to_item()
    restored = TeamRegistration.from_item(item)
    assert restored.guild_id == registration.guild_id
    assert restored.user_id == registration.user_id
    assert [p.tag for p in restored.players] == ["#AAA111", "#BBB222"]
    assert registration.lines_for_channel[0].startswith("User#1234 | PlayerOne")
