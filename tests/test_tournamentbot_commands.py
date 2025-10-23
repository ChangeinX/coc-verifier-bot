from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import tournamentbot
from tournament_bot import (
    BracketMatch,
    BracketRound,
    BracketSlot,
    BracketState,
    PlayerEntry,
    TeamRegistration,
    TournamentConfig,
    TournamentSeries,
)


class FakeMessage:
    def __init__(self) -> None:
        self.edits: list[dict[str, object]] = []

    async def edit(self, **kwargs) -> None:  # pragma: no cover - simple stub
        self.edits.append(kwargs)


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self._deferred = False

    async def send_message(
        self,
        message: str | None = None,
        *,
        embed=None,
        view=None,
        ephemeral: bool,
    ) -> None:
        self.messages.append(
            {
                "content": message,
                "embed": embed,
                "view": view,
                "ephemeral": ephemeral,
            }
        )
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


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.roles: dict[int, object] = {}
        self.members: dict[int, FakeUser] = {}

    def get_member(self, user_id: int):  # pragma: no cover - simple helper
        return self.members.get(user_id)

    async def fetch_member(self, user_id: int):  # pragma: no cover - simple helper
        return self.members.get(user_id)

    def register_member(self, member: FakeUser) -> None:
        self.members[member.id] = member


class FakeUser:
    def __init__(self, user_id: int, roles: list[object]):
        self.id = user_id
        self.roles = roles
        self.mention = f"<@{user_id}>"
        self.guild = None

    def __str__(self) -> str:
        return "User#0001"


class FakeInteraction:
    def __init__(self, user, guild) -> None:
        self.user = user
        self.guild = guild
        if getattr(user, "guild", None) is None:
            try:
                user.guild = guild
            except AttributeError:  # pragma: no cover - defensive
                pass
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.channel = None
        self._original_message = FakeMessage()

    async def original_response(self) -> FakeMessage:
        return self._original_message


class InMemoryStorage:
    def __init__(
        self,
        series: TournamentSeries,
        configs: dict[str, TournamentConfig],
    ) -> None:
        self._series = series
        self._configs = configs
        self._registrations: dict[tuple[str, int], TeamRegistration] = {}
        self._brackets: dict[tuple[int, str], BracketState] = {}
        self._round_windows = None

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

    def list_division_configs(self, guild_id: int) -> list[TournamentConfig]:
        if guild_id != self._series.guild_id:
            return []
        return sorted(
            self._configs.values(),
            key=lambda cfg: (cfg.division_name.lower(), cfg.division_id),
        )

    def list_division_ids(self, guild_id: int) -> list[str]:
        if guild_id != self._series.guild_id:
            return []
        return sorted(self._configs.keys())

    def get_round_windows(self, guild_id: int):  # pragma: no cover - simple helper
        return self._round_windows if guild_id == self._series.guild_id else None

    def save_round_windows(self, windows):  # pragma: no cover - simple helper
        self._round_windows = windows

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
        if guild_id != self._series.guild_id:
            return []
        return sorted(
            [
                reg
                for (div_id, _), reg in self._registrations.items()
                if div_id == division_id
            ],
            key=lambda reg: (reg.registered_at, reg.user_id),
        )

    def save_bracket(self, bracket: BracketState) -> None:
        key = (bracket.guild_id, bracket.division_id)
        self._brackets[key] = bracket

    def get_bracket(self, guild_id: int, division_id: str) -> BracketState | None:
        return self._brackets.get((guild_id, division_id))


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


def patch_division_role_helpers(monkeypatch, role_name: str = "TH15 Division") -> None:
    role = SimpleNamespace(id=987654321, name=role_name)

    async def fake_ensure(_guild, _config):  # pragma: no cover - simple stub
        return role, None

    async def fake_add(_member, _role, *, reason):  # pragma: no cover - stub
        del reason
        return None

    async def fake_remove(_member, _role, *, reason):  # pragma: no cover - stub
        del reason
        return None

    monkeypatch.setattr(tournamentbot, "ensure_division_role", fake_ensure)
    monkeypatch.setattr(tournamentbot, "add_division_role", fake_add)
    monkeypatch.setattr(tournamentbot, "remove_division_role", fake_remove)


@pytest.mark.asyncio
async def test_register_team_accepts_optional_sub_when_allowed(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})

    guild = FakeGuild(1)
    user = FakeUser(42, roles=[])
    user.guild = guild
    guild.register_member(user)
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
    patch_division_role_helpers(monkeypatch)

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

    guild = FakeGuild(1)
    user = FakeUser(42, roles=[])
    user.guild = guild
    guild.register_member(user)
    interaction = FakeInteraction(user=user, guild=guild)

    monkeypatch.setattr(tournamentbot, "storage", storage)
    monkeypatch.setattr(tournamentbot, "TOURNAMENT_REGISTRATION_CHANNEL_ID", None)
    patch_division_role_helpers(monkeypatch, role_name="TH12 Division")

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
    payload = interaction.response.messages[0]
    assert payload["ephemeral"] is True
    assert "Substitutes are not supported" in str(payload["content"])
    assert called is False


@pytest.mark.asyncio
async def test_register_sub_updates_existing_registration(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})
    guild = FakeGuild(1)
    user = FakeUser(42, roles=[])
    user.guild = guild
    guild.register_member(user)
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


@pytest.mark.asyncio
async def test_set_round_windows_command_displays_view(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})

    guild = FakeGuild(1)
    admin_role = SimpleNamespace(id=tournamentbot.TOURNAMENT_ADMIN_ROLE_ID)
    admin = FakeUser(99, roles=[admin_role])
    admin.guild = guild
    interaction = FakeInteraction(user=admin, guild=guild)

    monkeypatch.setattr(tournamentbot, "storage", storage)

    await tournamentbot.set_round_windows_command.callback(interaction)

    messages = interaction.response.messages
    assert messages
    payload = messages[0]
    assert payload["ephemeral"] is True
    assert payload["embed"] is not None
    assert isinstance(payload["view"], tournamentbot.RoundWindowsView)


def _make_bracket_with_match(now: datetime) -> BracketState:
    return BracketState(
        guild_id=1,
        division_id="th15",
        created_at=tournamentbot.isoformat_utc(now),
        rounds=[
            BracketRound(
                name="Semifinals",
                matches=[
                    BracketMatch(
                        match_id="R1M1",
                        round_index=0,
                        competitor_one=BracketSlot(
                            seed=1,
                            team_id=101,
                            team_label="Alpha",
                        ),
                        competitor_two=BracketSlot(
                            seed=2,
                            team_id=102,
                            team_label="Bravo",
                        ),
                    )
                ],
            )
        ],
    )


def _register_team(
    storage: InMemoryStorage, user_id: int, team_name: str, now: datetime
) -> None:
    storage.save_registration(
        TeamRegistration(
            guild_id=1,
            division_id="th15",
            user_id=user_id,
            user_name=f"Captain{user_id}",
            players=make_players(5),
            registered_at=now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            team_name=team_name,
        )
    )


def _make_admin() -> FakeUser:
    admin_role = SimpleNamespace(id=tournamentbot.TOURNAMENT_ADMIN_ROLE_ID)
    return FakeUser(9001, roles=[admin_role])


@pytest.mark.asyncio
async def test_select_round_winner_requires_window(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})
    storage.save_bracket(_make_bracket_with_match(now))
    _register_team(storage, 101, "Alpha", now)
    _register_team(storage, 102, "Bravo", now)

    monkeypatch.setattr(tournamentbot, "storage", storage)
    patch_division_role_helpers(monkeypatch)

    guild = FakeGuild(1)
    admin = _make_admin()
    admin.guild = guild
    guild.register_member(admin)
    interaction = FakeInteraction(user=admin, guild=guild)

    winner = FakeUser(101, roles=[])
    winner.guild = guild
    guild.register_member(winner)
    loser_member = FakeUser(102, roles=[])
    loser_member.guild = guild
    guild.register_member(loser_member)

    await tournamentbot.select_round_winner_command.callback(
        interaction,
        "th15",
        winner,
    )

    messages = interaction.response.messages
    assert messages
    assert "does not have a match window configured" in str(messages[0]["content"])


@pytest.mark.asyncio
async def test_select_round_winner_respects_window_open(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})
    bracket = _make_bracket_with_match(now)
    bracket.rounds[0].window_opens_at = tournamentbot.isoformat_utc(
        now + timedelta(hours=2)
    )
    bracket.rounds[0].window_closes_at = tournamentbot.isoformat_utc(
        now + timedelta(hours=3)
    )
    storage.save_bracket(bracket)
    _register_team(storage, 101, "Alpha", now)
    _register_team(storage, 102, "Bravo", now)

    monkeypatch.setattr(tournamentbot, "storage", storage)
    patch_division_role_helpers(monkeypatch)

    guild = FakeGuild(1)
    admin = _make_admin()
    admin.guild = guild
    guild.register_member(admin)
    interaction = FakeInteraction(user=admin, guild=guild)
    winner = FakeUser(101, roles=[])
    winner.guild = guild
    guild.register_member(winner)
    loser_member = FakeUser(102, roles=[])
    loser_member.guild = guild
    guild.register_member(loser_member)

    await tournamentbot.select_round_winner_command.callback(
        interaction,
        "th15",
        winner,
    )

    messages = interaction.response.messages
    assert messages
    assert "opens" in str(messages[0]["content"])
    saved = storage.get_bracket(guild.id, "th15")
    assert saved is not None
    assert saved.rounds[0].matches[0].winner_index is None


@pytest.mark.asyncio
async def test_select_round_winner_within_window_records_winner(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})
    bracket = _make_bracket_with_match(now)
    bracket.rounds[0].window_opens_at = tournamentbot.isoformat_utc(
        now - timedelta(hours=1)
    )
    bracket.rounds[0].window_closes_at = tournamentbot.isoformat_utc(
        now + timedelta(hours=1)
    )
    storage.save_bracket(bracket)
    _register_team(storage, 101, "Alpha", now)
    _register_team(storage, 102, "Bravo", now)

    monkeypatch.setattr(tournamentbot, "storage", storage)
    patch_division_role_helpers(monkeypatch)

    guild = FakeGuild(1)
    admin = _make_admin()
    admin.guild = guild
    guild.register_member(admin)
    interaction = FakeInteraction(user=admin, guild=guild)
    winner = FakeUser(101, roles=[])
    winner.guild = guild
    guild.register_member(winner)
    loser_member = FakeUser(102, roles=[])
    loser_member.guild = guild
    guild.register_member(loser_member)

    await tournamentbot.select_round_winner_command.callback(
        interaction,
        "th15",
        winner,
    )

    saved = storage.get_bracket(guild.id, "th15")
    assert saved is not None
    assert saved.rounds[0].matches[0].winner_index == 0

    messages = interaction.response.messages
    assert messages
    assert "Recorded" in str(messages[0]["content"])


@pytest.mark.asyncio
async def test_assign_role_self_success(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})
    monkeypatch.setattr(tournamentbot, "storage", storage)

    guild = FakeGuild(1)
    user = FakeUser(42, roles=[])
    user.guild = guild
    guild.register_member(user)
    interaction = FakeInteraction(user=user, guild=guild)

    role = SimpleNamespace(id=222, name="TH15 Division")

    async def fake_ensure(guild_obj, cfg):
        assert guild_obj is guild
        assert cfg is config
        return role, None

    add_calls: dict[str, object] = {}

    async def fake_add(member, role_obj, *, reason):
        add_calls["member"] = member
        add_calls["role"] = role_obj
        add_calls["reason"] = reason
        return None

    monkeypatch.setattr(tournamentbot, "ensure_division_role", fake_ensure)
    monkeypatch.setattr(tournamentbot, "add_division_role", fake_add)

    await tournamentbot.assign_role_command.callback(interaction, "th15", None)

    assert add_calls["member"] is user
    assert add_calls["role"] == role
    assert "TH15" in str(add_calls["reason"])

    messages = interaction.followup.sent
    assert messages
    assert "Assigned" in str(messages[0]["content"])


@pytest.mark.asyncio
async def test_assign_role_requires_admin_for_others(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})
    monkeypatch.setattr(tournamentbot, "storage", storage)

    guild = FakeGuild(1)
    user = FakeUser(42, roles=[])
    user.guild = guild
    guild.register_member(user)
    interaction = FakeInteraction(user=user, guild=guild)

    other_member = FakeUser(84, roles=[])
    other_member.guild = guild
    guild.register_member(other_member)

    await tournamentbot.assign_role_command.callback(interaction, "th15", other_member)

    messages = interaction.response.messages
    assert messages
    assert "administrator" in str(messages[0]["content"]).lower()


@pytest.mark.asyncio
async def test_assign_role_reports_existing_role(monkeypatch):
    now = datetime.now(UTC)
    series = make_series(now)
    config = make_config(team_size=5, division_id="th15")
    storage = InMemoryStorage(series, {"th15": config})
    monkeypatch.setattr(tournamentbot, "storage", storage)

    role = SimpleNamespace(id=333, name="TH15 Division")

    user = FakeUser(42, roles=[role])
    guild = FakeGuild(1)
    user.guild = guild
    guild.register_member(user)
    interaction = FakeInteraction(user=user, guild=guild)

    async def fake_ensure(_guild, _cfg):
        return role, None

    async def fake_add(member, role_obj, *, reason):
        del member, role_obj, reason
        return None

    monkeypatch.setattr(tournamentbot, "ensure_division_role", fake_ensure)
    monkeypatch.setattr(tournamentbot, "add_division_role", fake_add)

    await tournamentbot.assign_role_command.callback(interaction, "th15", None)

    messages = interaction.followup.sent
    assert messages
    assert "already has" in str(messages[0]["content"]).lower()
