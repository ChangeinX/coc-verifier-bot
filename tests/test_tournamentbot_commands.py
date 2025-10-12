from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import tournamentbot
from tournament_bot import (
    PlayerEntry,
    TeamRegistration,
    TournamentConfig,
    TournamentSeries,
)


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []
        self._deferred = False

    async def send_message(self, message: str, *, ephemeral: bool) -> None:
        self.messages.append((message, ephemeral))
        self._deferred = True

    async def defer(self, *, ephemeral: bool, thinking: bool) -> None:  # noqa: FBT001,F841 - test stub
        self._deferred = True

    def is_done(self) -> bool:
        return self._deferred


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send(
        self,
        content: str | None = None,
        *,
        embed=None,
        ephemeral: bool = False,
    ) -> None:
        self.sent.append({"content": content, "embed": embed, "ephemeral": ephemeral})


class FakeUser:
    def __init__(self, user_id: int, roles: list[object]):
        self.id = user_id
        self.roles = roles

    def __str__(self) -> str:
        return "User#0001"


class FakeInteraction:
    def __init__(self, user, guild) -> None:
        self.user = user
        self.guild = guild
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.channel = None


class InMemoryStorage:
    def __init__(
        self,
        series: TournamentSeries,
        configs: dict[str, TournamentConfig],
    ) -> None:
        self._series = series
        self._configs = configs
        self._registrations: dict[tuple[str, int], TeamRegistration] = {}

    def ensure_table(self) -> None:  # pragma: no cover - simple stub
        return None

    def get_series(self, guild_id: int) -> TournamentSeries | None:
        return self._series if guild_id == self._series.guild_id else None

    def save_series(self, series: TournamentSeries) -> None:  # pragma: no cover
        self._series = series

    def get_config(self, guild_id: int, division_id: str) -> TournamentConfig | None:
        if guild_id != self._series.guild_id:
            return None
        return self._configs.get(division_id)

    def list_division_ids(self, guild_id: int) -> list[str]:
        if guild_id != self._series.guild_id:
            return []
        return sorted(self._configs.keys())

    def get_registration(
        self, guild_id: int, division_id: str, user_id: int
    ) -> TeamRegistration | None:
        return self._registrations.get((division_id, user_id))

    def registration_count(self, guild_id: int, division_id: str) -> int:
        return sum(
            1
            for (div_id, _), _value in self._registrations.items()
            if div_id == division_id
        )

    def save_registration(self, registration: TeamRegistration) -> None:
        key = (registration.division_id, registration.user_id)
        self._registrations[key] = registration

    def list_registrations(
        self, guild_id: int, division_id: str
    ) -> list[TeamRegistration]:
        return sorted(
            [
                reg
                for (div_id, _), reg in self._registrations.items()
                if div_id == division_id
            ],
            key=lambda reg: (reg.registered_at, reg.user_id),
        )


def make_series(now: datetime) -> TournamentSeries:
    opens = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    closes = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return TournamentSeries(
        guild_id=1,
        registration_opens_at=opens,
        registration_closes_at=closes,
        updated_by=1,
        updated_at=opens,
    )


def make_config(team_size: int, division_id: str) -> TournamentConfig:
    return TournamentConfig(
        guild_id=1,
        division_id=division_id,
        division_name=division_id.upper(),
        team_size=team_size,
        allowed_town_halls=[15, 16],
        max_teams=16,
        updated_by=1,
        updated_at="2024-01-01T00:00:00.000Z",
    )


def make_players(count: int) -> list[PlayerEntry]:
    players: list[PlayerEntry] = []
    for idx in range(count):
        players.append(
            PlayerEntry(
                name=f"Player{idx}",
                tag=f"#TAG{idx}",
                town_hall=15 if idx % 2 == 0 else 16,
            )
        )
    return players


@pytest.mark.asyncio
async def test_register_team_accepts_optional_sub_when_allowed(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})

    guild = SimpleNamespace(id=1)
    user = FakeUser(42, roles=[])
    interaction = FakeInteraction(user=user, guild=guild)

    players = make_players(6)

    async def fake_fetch(tags):
        assert len(tags) == 6
        return players

    async def fake_post(_guild, _embed):
        return None

    monkeypatch.setattr(tournamentbot, "storage", storage)
    monkeypatch.setattr(tournamentbot, "fetch_players", fake_fetch)
    monkeypatch.setattr(tournamentbot, "post_registration_announcement", fake_post)
    monkeypatch.setattr(tournamentbot, "TOURNAMENT_REGISTRATION_CHANNEL_ID", None)

    tags = " ".join(player.tag for player in players)

    await tournamentbot.register_player_command.callback(
        interaction,
        "th15",
        "My Team",
        tags,
    )

    saved = storage.get_registration(guild.id, "th15", user.id)
    assert saved is not None
    assert saved.team_name == "My Team"
    assert len(saved.players) == 5
    assert saved.substitute is not None
    assert saved.substitute.tag == "#TAG5"

    messages = interaction.followup.sent
    assert messages
    assert messages[-1]["content"].startswith("Team registered!")


@pytest.mark.asyncio
async def test_register_team_blocks_sub_in_1v1(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=1, division_id="th12")
    storage = InMemoryStorage(series, {"th12": config})

    guild = SimpleNamespace(id=1)
    user = FakeUser(42, roles=[])
    interaction = FakeInteraction(user=user, guild=guild)

    monkeypatch.setattr(tournamentbot, "storage", storage)
    monkeypatch.setattr(tournamentbot, "TOURNAMENT_REGISTRATION_CHANNEL_ID", None)

    players = make_players(2)
    tags = " ".join(player.tag for player in players)

    called = False

    async def fake_fetch(_tags):
        nonlocal called
        called = True
        return players

    monkeypatch.setattr(tournamentbot, "fetch_players", fake_fetch)

    await tournamentbot.register_player_command.callback(
        interaction,
        "th12",
        "Solo Warrior",
        tags,
    )

    assert interaction.response.messages
    message, ephemeral = interaction.response.messages[0]
    assert ephemeral is True
    assert "Substitutes are not supported" in message
    assert called is False


@pytest.mark.asyncio
async def test_register_sub_updates_existing_registration(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})
    guild = SimpleNamespace(id=1)
    user = FakeUser(42, roles=[])
    interaction = FakeInteraction(user=user, guild=guild)

    existing_players = make_players(5)
    storage.save_registration(
        TeamRegistration(
            guild_id=1,
            division_id="th15",
            user_id=user.id,
            user_name="User#0001",
            players=existing_players,
            registered_at=now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            team_name="My Team",
        )
    )

    async def fake_fetch(tags):
        assert tags == ["#NEWSUB"]
        return [PlayerEntry(name="NewSub", tag="#NEWSUB", town_hall=15)]

    async def fake_post(_guild, _embed):
        return None

    monkeypatch.setattr(tournamentbot, "storage", storage)
    monkeypatch.setattr(tournamentbot, "fetch_players", fake_fetch)
    monkeypatch.setattr(tournamentbot, "post_registration_announcement", fake_post)
    monkeypatch.setattr(tournamentbot, "TOURNAMENT_REGISTRATION_CHANNEL_ID", None)

    await tournamentbot.register_sub_command.callback(interaction, "th15", "#NEWSUB")

    saved = storage.get_registration(guild.id, "th15", user.id)
    assert saved is not None and saved.substitute is not None
    assert saved.substitute.tag == "#NEWSUB"

    messages = interaction.followup.sent
    assert messages
    assert messages[-1]["content"] == "Substitute registered!"
    assert messages[-1]["ephemeral"] is True
