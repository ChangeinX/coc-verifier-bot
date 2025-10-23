from datetime import datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from tournament_bot import (
    BracketState,
    PlayerEntry,
    TeamRegistration,
    TournamentConfig,
    TournamentSeries,
    TournamentStorage,
)
from tournament_bot.bracket import create_bracket_state


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}

    def get_item(self, *, Key):
        return {"Item": self.items.get((Key["pk"], Key["sk"]))}

    def put_item(self, *, Item):
        self.items[(Item["pk"], Item["sk"])] = Item

    def query(self, *, KeyConditionExpression, Select="COUNT", **_kwargs):
        pk_value = None
        sk_prefix = ""
        for condition in KeyConditionExpression._values:  # type: ignore[attr-defined]
            key, value = condition._values  # type: ignore[attr-defined]
            if key.name == "pk":  # pragma: no branch - helper
                pk_value = value
            elif key.name == "sk":
                sk_prefix = value
        matching_keys = [
            key
            for key in sorted(self.items)
            if key[0] == pk_value and key[1].startswith(sk_prefix)
        ]
        items = [self.items[key] for key in matching_keys]
        if Select == "COUNT":
            return {"Count": len(items)}
        return {"Items": [item.copy() for item in items], "Count": len(items)}

    def delete_item(self, *, Key, ConditionExpression):
        del ConditionExpression  # pragma: no cover - unused in fake implementation
        item_key = (Key["pk"], Key["sk"])
        if item_key not in self.items:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "Item not found",
                    }
                },
                "DeleteItem",
            )
        self.items.pop(item_key)


def build_storage() -> tuple[TournamentStorage, FakeTable]:
    table = FakeTable()
    return TournamentStorage(table), table


def sample_series() -> TournamentSeries:
    return TournamentSeries(
        guild_id=42,
        registration_opens_at="2024-01-01T00:00:00.000Z",
        registration_closes_at="2024-01-05T00:00:00.000Z",
        updated_by=99,
        updated_at="2024-01-01T00:00:00.000Z",
    )


def sample_config(
    division_id: str = "th12", *, role_id: int | None = None
) -> TournamentConfig:
    return TournamentConfig(
        guild_id=42,
        division_id=division_id,
        division_name=division_id.upper(),
        team_size=1,
        allowed_town_halls=[int(division_id[2:])],
        max_teams=16,
        updated_by=99,
        updated_at="2024-01-01T00:00:00.000Z",
        division_role_id=role_id,
    )


def sample_registration(division_id: str = "th12") -> TeamRegistration:
    return TeamRegistration(
        guild_id=42,
        division_id=division_id,
        user_id=7,
        user_name="User#1234",
        players=[
            PlayerEntry(
                name="One",
                tag="#AAA111",
                town_hall=int(division_id[2:]),
                clan_name="Alpha",
                clan_tag="#CLAN1",
            )
        ],
        registered_at="2024-01-01T00:00:00.000Z",
    )


def make_registration(
    user_id: int, offset_seconds: int, division_id: str
) -> TeamRegistration:
    registered_at = (
        datetime.fromisoformat("2024-01-01T00:00:00+00:00")
        + timedelta(seconds=offset_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return TeamRegistration(
        guild_id=42,
        division_id=division_id,
        user_id=user_id,
        user_name=f"User#{user_id}",
        players=[
            PlayerEntry(
                name=f"Player{user_id}",
                tag=f"#T{user_id:03d}",
                town_hall=int(division_id[2:]),
            )
        ],
        registered_at=registered_at,
    )


def test_ensure_table_raises_when_missing():
    storage = TournamentStorage(None)
    with pytest.raises(RuntimeError):
        storage.ensure_table()


def test_series_round_trip():
    storage, _ = build_storage()
    series = sample_series()
    storage.save_series(series)

    restored = storage.get_series(series.guild_id)
    assert restored == series
    opens_at, closes_at = restored.registration_window()
    assert opens_at.year == 2024
    assert closes_at.day == 5


def test_config_round_trip():
    storage, _ = build_storage()
    config = sample_config("th13", role_id=555555555)
    storage.save_config(config)

    restored = storage.get_config(config.guild_id, config.division_id)
    assert restored == config

    configs = storage.list_division_configs(config.guild_id)
    assert [cfg.division_id for cfg in configs] == ["th13"]


def test_config_missing_returns_none():
    storage, _ = build_storage()
    assert storage.get_config(42, "th12") is None


def test_registration_round_trip_and_count():
    storage, _table = build_storage()
    registration = sample_registration("th14")
    storage.save_registration(registration)

    restored = storage.get_registration(
        registration.guild_id, registration.division_id, registration.user_id
    )
    assert restored == registration

    assert (
        storage.registration_count(registration.guild_id, registration.division_id) == 1
    )
    assert storage.registration_count(registration.guild_id, "th13") == 0


def test_delete_registration_outcome():
    storage, _table = build_storage()
    registration = sample_registration("th13")
    storage.save_registration(registration)

    assert (
        storage.delete_registration(
            registration.guild_id, registration.division_id, registration.user_id
        )
        is True
    )
    assert (
        storage.delete_registration(
            registration.guild_id, registration.division_id, registration.user_id
        )
        is False
    )


def test_delete_registrations_for_division():
    storage, _ = build_storage()
    registrations = [
        sample_registration("th13"),
        sample_registration("th14"),
    ]
    for entry in registrations:
        storage.save_registration(entry)

    removed = storage.delete_registrations_for_division(42, "th13")
    assert removed == 1
    assert storage.list_registrations(42, "th13") == []
    assert len(storage.list_registrations(42, "th14")) == 1


def test_delete_registration_raises_for_other_errors():
    storage, table = build_storage()

    def broken_delete_item(**_kwargs):
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "DeleteItem")

    table.delete_item = broken_delete_item  # type: ignore[assignment]

    with pytest.raises(ClientError):
        storage.delete_registration(42, "th12", 99)


def test_list_registrations_returns_sorted_entries():
    storage, _table = build_storage()
    registrations = [
        make_registration(10, 60, "th12"),
        make_registration(11, 0, "th12"),
        make_registration(12, 120, "th12"),
        make_registration(99, 30, "th13"),
    ]
    for registration in registrations:
        storage.save_registration(registration)

    ordered = storage.list_registrations(42, "th12")
    assert [entry.user_id for entry in ordered] == [11, 10, 12]

    other_division = storage.list_registrations(42, "th13")
    assert [entry.user_id for entry in other_division] == [99]


def test_bracket_round_trip():
    storage, _table = build_storage()
    registrations = [
        make_registration(1, 0, "th12"),
        make_registration(2, 60, "th12"),
    ]
    for registration in registrations:
        storage.save_registration(registration)

    bracket = create_bracket_state(42, "th12", registrations)
    storage.save_bracket(bracket)

    restored = storage.get_bracket(42, "th12")
    assert isinstance(restored, BracketState)
    assert (
        restored.rounds[0].matches[0].competitor_one.team_id == registrations[0].user_id
    )
    storage.delete_bracket(42, "th12")
    assert storage.get_bracket(42, "th12") is None
