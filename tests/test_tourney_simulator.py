from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import tournament_bot.simulator as sim
import tournament_bot.tourney_simulator as cli
from tournament_bot.bracket import create_bracket_state


def test_load_seed_tags_success(tmp_path):
    seed_file = tmp_path / "seed.txt"
    seed_file.write_text("#aaa\n\n#bbb\n")

    tags = sim.load_seed_tags(seed_file)

    assert tags == ["#AAA", "#BBB"]


def test_load_seed_tags_empty(tmp_path):
    seed_file = tmp_path / "empty.txt"
    seed_file.write_text("\n\n")

    with pytest.raises(ValueError):
        sim.load_seed_tags(seed_file)


def test_ensure_town_hall_range_validation():
    players = [
        sim.SeededPlayer(
            name="Alpha",
            tag="#A",
            town_hall=15,
            trophies=5000,
            exp_level=200,
            clan_name=None,
            clan_tag=None,
        )
    ]

    # Should not raise inside allowed range
    sim.ensure_town_hall_range(players, minimum=15, maximum=17)

    players.append(
        sim.SeededPlayer(
            name="Bravo",
            tag="#B",
            town_hall=18,
            trophies=5100,
            exp_level=205,
            clan_name=None,
            clan_tag=None,
        )
    )

    with pytest.raises(ValueError):
        sim.ensure_town_hall_range(players, minimum=15, maximum=17)


def test_sorted_for_seeding_orders_by_priority():
    players = [
        sim.SeededPlayer("Charlie", "#C", 16, 5200, 210, None, None),
        sim.SeededPlayer("Delta", "#D", 17, 5100, 205, None, None),
        sim.SeededPlayer("Echo", "#E", 16, 5400, 215, None, None),
    ]

    ordered = sim.sorted_for_seeding(players)

    assert [player.tag for player in ordered] == ["#D", "#E", "#C"]


def test_build_registrations_produces_unique_entries():
    base_time = datetime(2025, 1, 1, tzinfo=UTC)
    players = [
        sim.SeededPlayer("Foxtrot", "#F", 16, 5000, 200, "Foo", "#FOO"),
        sim.SeededPlayer("Golf", "#G", 15, 4800, 190, None, None),
    ]

    registrations = sim.build_registrations(players, guild_id=42, base_time=base_time)

    assert len(registrations) == 2
    assert registrations[0].user_id == 1
    assert registrations[1].user_id == 2
    assert registrations[0].registered_at != registrations[1].registered_at
    assert registrations[0].players[0].clan_name == "Foo"


@pytest.mark.asyncio
async def test_fetch_seeded_players(monkeypatch):
    async def fake_fetch(client, email, password, tag, **kwargs):
        return SimpleNamespace(
            status="ok",
            player=SimpleNamespace(
                name=f"Player {tag}",
                tag=tag,
                town_hall=16,
                trophies=5000,
                exp_level=200,
                clan=SimpleNamespace(name="Clan", tag="#CLAN"),
            ),
        )

    monkeypatch.setattr(sim.coc_api, "fetch_player_with_status", fake_fetch)

    players = await sim.fetch_seeded_players(
        client=object(),
        email="email",
        password="password",
        tags=["#AAA", "#BBB"],
    )

    assert len(players) == 2
    assert players[0].clan_name == "Clan"


@pytest.mark.asyncio
async def test_fetch_seeded_players_failure(monkeypatch):
    async def fake_fetch(client, email, password, tag, **kwargs):
        return SimpleNamespace(status="error", player=None)

    monkeypatch.setattr(sim.coc_api, "fetch_player_with_status", fake_fetch)

    with pytest.raises(RuntimeError):
        await sim.fetch_seeded_players(
            client=object(),
            email="email",
            password="password",
            tags=["#AAA"],
        )


def test_cli_helpers(capsys):
    cli.print_snapshots([("Initial", object()), ("After Final", object())])
    output = capsys.readouterr().out
    assert "Snapshot 1: Initial" in output

    registrations = sim.build_registrations(
        [
            sim.SeededPlayer("Hotel", "#H", 16, 5100, 205, None, None),
            sim.SeededPlayer("India", "#I", 16, 5200, 210, None, None),
        ],
        guild_id=1,
        base_time=datetime(2025, 1, 1, tzinfo=UTC),
    )
    bracket = create_bracket_state(1, registrations)
    cli.render_final_bracket(bracket)
    rendered = capsys.readouterr().out
    assert "Final Bracket" in rendered


@pytest.mark.asyncio
async def test_cli_main_async(monkeypatch, tmp_path, capsys):
    seed_file = tmp_path / "seed.txt"
    seed_file.write_text("#AAA\n#BBB\n")

    args = SimpleNamespace(
        seed_file=seed_file,
        guild_id=7,
        base_time="2025-01-01T00:00:00.000Z",
        no_bracket=True,
    )

    monkeypatch.setenv("COC_EMAIL", "email@example.com")
    monkeypatch.setenv("COC_PASSWORD", "secret")
    monkeypatch.setattr(cli, "parse_args", lambda: args)

    fake_players = {
        "#AAA": SimpleNamespace(
            name="Juliet",
            tag="#AAA",
            town_hall=17,
            trophies=6000,
            exp_level=220,
            clan=SimpleNamespace(name="Clan A", tag="#CA"),
        ),
        "#BBB": SimpleNamespace(
            name="Kilo",
            tag="#BBB",
            town_hall=16,
            trophies=5800,
            exp_level=210,
            clan=None,
        ),
    }

    async def fake_build_seeded(
        client, email, password, guild_id, seed_file=None, base_time=None
    ):
        seeded = [
            sim.SeededPlayer(
                name=player.name,
                tag=player.tag,
                town_hall=player.town_hall,
                trophies=player.trophies,
                exp_level=player.exp_level,
                clan_name=player.clan.name if player.clan else None,
                clan_tag=player.clan.tag if player.clan else None,
            )
            for player in fake_players.values()
        ]
        return sim.build_registrations(seeded, guild_id, base_time=base_time)

    class FakeClient:
        async def login(self, email, password):
            assert email == "email@example.com"
            assert password == "secret"

        async def close(self):
            pass

    monkeypatch.setattr(cli.coc, "Client", lambda: FakeClient())
    monkeypatch.setattr(cli, "build_seeded_registrations", fake_build_seeded)

    await cli.main_async()

    output = capsys.readouterr().out
    assert "Snapshot" in output
