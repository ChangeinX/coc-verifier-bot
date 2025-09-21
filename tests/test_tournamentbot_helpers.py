from types import SimpleNamespace

import pytest

import tournamentbot
from tournament_bot import PlayerEntry, TeamRegistration, TournamentConfig, utc_now_iso


def test_format_config_message_includes_details():
    config = TournamentConfig(
        guild_id=1,
        team_size=10,
        allowed_town_halls=[16, 17],
        max_teams=20,
        registration_opens_at="2024-05-01T18:00:00.000Z",
        registration_closes_at="2024-05-10T18:00:00.000Z",
        updated_by=0,
        updated_at="2024-01-01T00:00:00.000Z",
    )
    message = tournamentbot.format_config_message(config)
    assert "Team size: 10" in message
    assert "Allowed Town Halls: 16, 17" in message
    assert "Registration: May 01, 2024 18:00 UTC — May 10, 2024 18:00 UTC" in message


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

    async def fake_build(client, email, password, guild_id, **kwargs):
        assert client is tournamentbot.coc_client
        assert email == "email"
        assert password == "password"
        assert guild_id == 99
        assert kwargs.get("shuffle") is True

        return ["registration"]

    monkeypatch.setattr(tournamentbot, "build_seeded_registrations", fake_build)

    result = await tournamentbot.build_seeded_registrations_for_guild(99)

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
        user_id=2,
        user_name="Captain",
        players=players,
        registered_at=utc_now_iso(),
        team_name="Legends",
        substitute=substitute,
    )
    config = TournamentConfig(
        guild_id=1,
        team_size=5,
        allowed_town_halls=[15, 16],
        max_teams=8,
        registration_opens_at="2024-01-01T00:00:00.000Z",
        registration_closes_at="2024-01-05T00:00:00.000Z",
        updated_by=1,
        updated_at="2024-01-01T00:00:00.000Z",
    )

    closes_at = tournamentbot.parse_registration_datetime("2024-01-05T00:00")
    embed = tournamentbot.build_registration_embed(
        registration,
        config=config,
        closes_at=closes_at,
        is_update=False,
    )

    assert "Legends" in embed.title
    team_size_field = next(field for field in embed.fields if field.name == "Team Size")
    assert "5 starters" in team_size_field.value
    assert "+ 1 sub" in team_size_field.value


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


def test_resolve_registration_owner_self(monkeypatch):
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
