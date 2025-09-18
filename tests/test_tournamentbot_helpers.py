from types import SimpleNamespace

import pytest

import tournamentbot
from tournament_bot import TournamentConfig


def test_format_config_message_includes_details():
    config = TournamentConfig(
        guild_id=1,
        team_size=10,
        allowed_town_halls=[16, 17],
        max_teams=20,
        updated_by=0,
        updated_at="2024-01-01T00:00:00.000Z",
    )
    message = tournamentbot.format_config_message(config)
    assert "Team size: 10" in message
    assert "Allowed Town Halls: 16, 17" in message


def test_ensure_guild_validates_presence():
    interaction = SimpleNamespace(guild="guild")
    assert tournamentbot.ensure_guild(interaction) == "guild"

    interaction_missing = SimpleNamespace(guild=None)
    with pytest.raises(RuntimeError):
        tournamentbot.ensure_guild(interaction_missing)


@pytest.mark.asyncio
async def test_fetch_players_returns_entries(monkeypatch):
    async def fake_get_player(_client, _email, _password, tag):
        return SimpleNamespace(name=f"Player{tag[-1]}", town_hall_level=17)

    monkeypatch.setattr(tournamentbot.coc_api, "get_player_with_retry", fake_get_player)

    players = await tournamentbot.fetch_players(["#AAA111", "#BBB222"])

    assert [player.tag for player in players] == ["#AAA111", "#BBB222"]
    assert all(player.town_hall == 17 for player in players)


@pytest.mark.asyncio
async def test_fetch_players_raises_when_missing_data(monkeypatch):
    async def fake_get_player(_client, _email, _password, tag):
        if tag == "#AAA111":
            return SimpleNamespace(name="PlayerA", town_hall=None, town_hall_level=None)
        return None

    monkeypatch.setattr(tournamentbot.coc_api, "get_player_with_retry", fake_get_player)

    with pytest.raises(tournamentbot.InvalidValueError):
        await tournamentbot.fetch_players(["#AAA111", "#BBB222"])
