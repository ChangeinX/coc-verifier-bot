from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import tournamentbot
from tournament_bot import PlayerEntry, TeamRegistration, TournamentConfig


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []
        self.deferred = False

    async def send_message(self, message: str, *, ephemeral: bool) -> None:
        self.messages.append((message, ephemeral))
        self.deferred = True

    async def defer(self, *, ephemeral: bool, thinking: bool) -> None:  # noqa: FBT001,F841 - test stub
        self.deferred = True

    def is_done(self) -> bool:
        return self.deferred


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
    def __init__(self, config: TournamentConfig) -> None:
        self._config = config
        self._registrations: dict[int, TeamRegistration] = {}

    def ensure_table(self) -> None:  # pragma: no cover - simple stub
        return None

    def get_config(self, guild_id: int) -> TournamentConfig | None:
        return self._config if guild_id == self._config.guild_id else None

    def get_registration(self, guild_id: int, user_id: int) -> TeamRegistration | None:
        return self._registrations.get(user_id)

    def registration_count(self, guild_id: int) -> int:
        return len(self._registrations)

    def save_registration(self, registration: TeamRegistration) -> None:
        self._registrations[registration.user_id] = registration

    def list_registrations(
        self, guild_id: int
    ) -> list[TeamRegistration]:  # pragma: no cover
        return list(self._registrations.values())


def make_config(now: datetime) -> TournamentConfig:
    opens = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    closes = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return TournamentConfig(
        guild_id=1,
        team_size=5,
        allowed_town_halls=[15, 16],
        max_teams=16,
        registration_opens_at=opens,
        registration_closes_at=closes,
        updated_by=1,
        updated_at=opens,
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
async def test_register_team_accepts_optional_sub(monkeypatch):
    now = datetime.now(UTC)
    config = make_config(now)
    storage = InMemoryStorage(config)
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

    await tournamentbot.register_team_command.callback(interaction, "My Team", tags)

    saved = storage.get_registration(guild.id, user.id)
    assert saved is not None
    assert saved.team_name == "My Team"
    assert len(saved.players) == 5
    assert saved.substitute is not None
    assert saved.substitute.tag == "#TAG5"

    messages = interaction.followup.sent
    assert messages
    assert messages[-1]["content"].startswith("Team registered!")


@pytest.mark.asyncio
async def test_register_sub_updates_existing_registration(monkeypatch):
    now = datetime.now(UTC)
    config = make_config(now)
    storage = InMemoryStorage(config)
    guild = SimpleNamespace(id=1)
    user = FakeUser(42, roles=[])
    interaction = FakeInteraction(user=user, guild=guild)

    existing_players = make_players(5)
    storage.save_registration(
        TeamRegistration(
            guild_id=1,
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

    await tournamentbot.register_sub_command.callback(interaction, "#NEWSUB")

    saved = storage.get_registration(guild.id, user.id)
    assert saved is not None and saved.substitute is not None
    assert saved.substitute.tag == "#NEWSUB"

    messages = interaction.followup.sent
    assert messages
    assert messages[-1]["content"] == "Substitute registered!"
    assert messages[-1]["ephemeral"] is True


@pytest.mark.asyncio
async def test_show_registered_lists_summary(monkeypatch):
    now = datetime.now(UTC)
    config = make_config(now)
    storage = InMemoryStorage(config)
    guild = SimpleNamespace(id=1)
    user = FakeUser(99, roles=[])
    interaction = FakeInteraction(user=user, guild=guild)

    storage.save_registration(
        TeamRegistration(
            guild_id=1,
            user_id=1,
            user_name="CaptainOne",
            players=make_players(5),
            registered_at=now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            team_name="Alpha",
        )
    )
    storage.save_registration(
        TeamRegistration(
            guild_id=1,
            user_id=2,
            user_name="CaptainTwo",
            players=make_players(5),
            registered_at=(now + timedelta(seconds=1)).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            ),
            team_name="Bravo",
        )
    )

    monkeypatch.setattr(tournamentbot, "storage", storage)

    await tournamentbot.show_registered_command.callback(interaction)

    assert interaction.response.messages
    message, is_ephemeral = interaction.response.messages[0]
    assert is_ephemeral is True
    assert "Alpha" in message and "Bravo" in message
    assert "Registered teams:" in message


@pytest.mark.asyncio
async def test_show_registered_handles_empty_list(monkeypatch):
    now = datetime.now(UTC)
    config = make_config(now)
    storage = InMemoryStorage(config)
    guild = SimpleNamespace(id=1)
    user = FakeUser(99, roles=[])
    interaction = FakeInteraction(user=user, guild=guild)

    monkeypatch.setattr(tournamentbot, "storage", storage)

    await tournamentbot.show_registered_command.callback(interaction)

    assert interaction.response.messages
    message, is_ephemeral = interaction.response.messages[0]
    assert is_ephemeral is True
    assert message == "No teams have registered yet."
