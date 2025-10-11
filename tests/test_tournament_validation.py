import pytest

from tournament_bot import (
    InvalidTownHallError,
    InvalidValueError,
    PlayerEntry,
    TeamRegistration,
    TournamentConfig,
    TournamentSeries,
    normalize_player_tag,
    parse_player_tags,
    parse_registration_datetime,
    parse_town_hall_levels,
    utc_now_iso,
    validate_max_teams,
    validate_registration_window,
    validate_team_name,
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
    assert validate_team_size(1) == 1
    assert validate_team_size(10) == 10
    with pytest.raises(InvalidValueError):
        validate_team_size(9)


def test_validate_team_size_rejects_too_small_and_large():
    with pytest.raises(InvalidValueError):
        validate_team_size(0)
    with pytest.raises(InvalidValueError):
        validate_team_size(2)
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
    series = TournamentSeries(
        guild_id=123,
        registration_opens_at="2024-02-01T12:00:00.000Z",
        registration_closes_at="2024-02-05T18:00:00.000Z",
        updated_by=42,
        updated_at=utc_now_iso(),
    )
    series_item = series.to_item()
    restored_series = TournamentSeries.from_item(series_item)
    assert restored_series == series
    opens_at, closes_at = restored_series.registration_window()
    assert opens_at.day == 1
    assert closes_at.day == 5

    config = TournamentConfig(
        guild_id=123,
        division_id="th16",
        division_name="TH16",
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
        division_id="th16",
        user_id=456,
        user_name="User#1234",
        players=[
            PlayerEntry(name="PlayerOne", tag="#AAA111", town_hall=16),
            PlayerEntry(name="PlayerTwo", tag="#BBB222", town_hall=17),
        ],
        registered_at=utc_now_iso(),
        team_name="Legends",
        substitute=PlayerEntry(name="PlayerSub", tag="#SUB999", town_hall=16),
    )
    item = registration.to_item()
    restored = TeamRegistration.from_item(item)
    assert restored.guild_id == registration.guild_id
    assert restored.division_id == registration.division_id
    assert restored.user_id == registration.user_id
    assert [p.tag for p in restored.players] == ["#AAA111", "#BBB222"]
    assert restored.team_name == "Legends"
    assert restored.substitute and restored.substitute.tag == "#SUB999"
    assert registration.lines_for_channel[0].startswith("PlayerOne (TH16)")


def test_parse_registration_datetime_accepts_iso_variants():
    dt = parse_registration_datetime("2024-05-01 18:00")
    assert dt.isoformat().startswith("2024-05-01T18:00")
    with pytest.raises(InvalidValueError):
        parse_registration_datetime("not-a-date")


def test_validate_registration_window_enforces_order():
    opens = parse_registration_datetime("2024-05-01T18:00")
    closes = parse_registration_datetime("2024-05-02T18:00")
    validated_opens, validated_closes = validate_registration_window(opens, closes)
    assert validated_opens < validated_closes
    with pytest.raises(InvalidValueError):
        validate_registration_window(closes, opens)


def test_validate_team_name_enforces_length_and_trims():
    assert validate_team_name("  The Mighty Heroes  ") == "The Mighty Heroes"

    with pytest.raises(InvalidValueError):
        validate_team_name("hi")

    with pytest.raises(InvalidValueError):
        validate_team_name("x" * 101)
