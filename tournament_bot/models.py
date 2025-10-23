from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format (ms precision)."""
    return datetime.now(UTC).strftime(ISO_FORMAT)


@dataclass(slots=True)
class TournamentSeries:
    guild_id: int
    registration_opens_at: str
    registration_closes_at: str
    updated_by: int
    updated_at: str

    PK_TEMPLATE: ClassVar[str] = "GUILD#%s"
    SK_VALUE: ClassVar[str] = "SERIES"

    @classmethod
    def key(cls, guild_id: int) -> dict[str, str]:
        return {"pk": cls.PK_TEMPLATE % guild_id, "sk": cls.SK_VALUE}

    def to_item(self) -> dict[str, object]:
        item = self.key(self.guild_id)
        item.update(
            {
                "registration_opens_at": self.registration_opens_at,
                "registration_closes_at": self.registration_closes_at,
                "updated_by": str(self.updated_by),
                "updated_at": self.updated_at,
            }
        )
        return item

    @classmethod
    def from_item(cls, item: dict[str, object]) -> TournamentSeries:
        guild_id = int(str(item["pk"]).split("#", 1)[1])
        return cls(
            guild_id=guild_id,
            registration_opens_at=str(item.get("registration_opens_at", "")),
            registration_closes_at=str(item.get("registration_closes_at", "")),
            updated_by=int(item.get("updated_by", 0)),
            updated_at=str(item.get("updated_at", "")),
        )

    def registration_window(self) -> tuple[datetime, datetime]:
        opens_at = datetime.strptime(self.registration_opens_at, ISO_FORMAT).replace(
            tzinfo=UTC
        )
        closes_at = datetime.strptime(self.registration_closes_at, ISO_FORMAT).replace(
            tzinfo=UTC
        )
        return opens_at, closes_at


@dataclass(slots=True)
class RoundWindowDefinition:
    position: int
    opens_at: str
    closes_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "position": self.position,
            "opens_at": self.opens_at,
            "closes_at": self.closes_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RoundWindowDefinition:
        position_raw = data.get("position")
        try:
            position = int(position_raw) if position_raw is not None else 0
        except (TypeError, ValueError):  # pragma: no cover - defensive
            position = 0
        if position <= 0:
            position = 1
        opens_at = str(data.get("opens_at", ""))
        closes_at = str(data.get("closes_at", ""))
        return cls(position=position, opens_at=opens_at, closes_at=closes_at)


@dataclass(slots=True)
class TournamentRoundWindows:
    guild_id: int
    rounds: list[RoundWindowDefinition]
    updated_by: int
    updated_at: str

    PK_TEMPLATE: ClassVar[str] = "GUILD#%s"
    SK_VALUE: ClassVar[str] = "ROUND_WINDOWS"

    @classmethod
    def key(cls, guild_id: int) -> dict[str, str]:
        return {"pk": cls.PK_TEMPLATE % guild_id, "sk": cls.SK_VALUE}

    def to_item(self) -> dict[str, object]:
        self.ensure_sequential_positions()
        item = self.key(self.guild_id)
        item.update(
            {
                "rounds": [round_.to_dict() for round_ in self.rounds],
                "updated_by": str(self.updated_by),
                "updated_at": self.updated_at,
            }
        )
        return item

    @classmethod
    def from_item(cls, item: dict[str, object]) -> TournamentRoundWindows:
        pk_value = str(item["pk"])
        guild_id = int(pk_value.split("#", 1)[1])
        rounds_data = item.get("rounds", [])  # type: ignore[assignment]
        rounds = [
            RoundWindowDefinition.from_dict(round_item) for round_item in rounds_data
        ]
        rounds.sort(key=lambda round_: round_.position)
        updated_by_raw = item.get("updated_by", 0)
        try:
            updated_by = int(updated_by_raw)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            updated_by = 0
        return cls(
            guild_id=guild_id,
            rounds=rounds,
            updated_by=updated_by,
            updated_at=str(item.get("updated_at", "")),
        )

    def ensure_sequential_positions(self) -> None:
        self.rounds.sort(key=lambda round_: round_.position)
        for idx, round_ in enumerate(self.rounds, start=1):
            round_.position = idx


@dataclass(slots=True)
class TournamentConfig:
    guild_id: int
    division_id: str
    division_name: str
    team_size: int
    allowed_town_halls: list[int]
    max_teams: int
    updated_by: int
    updated_at: str
    division_role_id: int | None = None

    PK_TEMPLATE: ClassVar[str] = "GUILD#%s"
    SK_TEMPLATE: ClassVar[str] = "DIVISION#%s#CONFIG"
    LEGACY_SK_VALUE: ClassVar[str] = "CONFIG"

    @classmethod
    def key(cls, guild_id: int, division_id: str) -> dict[str, str]:
        return {
            "pk": cls.PK_TEMPLATE % guild_id,
            "sk": cls.SK_TEMPLATE % division_id,
        }

    def to_item(self) -> dict[str, object]:
        item = self.key(self.guild_id, self.division_id)
        item.update(
            {
                "division_id": self.division_id,
                "division_name": self.division_name,
                "team_size": self.team_size,
                "allowed_town_halls": self.allowed_town_halls,
                "max_teams": self.max_teams,
                "updated_by": str(self.updated_by),
                "updated_at": self.updated_at,
            }
        )
        if self.division_role_id is not None:
            item["division_role_id"] = str(self.division_role_id)
        return item

    @classmethod
    def from_item(cls, item: dict[str, object]) -> TournamentConfig:
        pk_value = str(item["pk"])
        guild_id = int(pk_value.split("#", 1)[1])
        sk_value = str(item.get("sk", ""))
        raw_division = item.get("division_id")
        if raw_division is not None:
            division_id = str(raw_division)
        else:
            parts = sk_value.split("#", 2)
            if len(parts) >= 3 and parts[0] == "DIVISION":
                division_id = parts[1]
            else:
                division_id = "default"
        division_name_value = item.get("division_name")
        division_name = (
            str(division_name_value)
            if division_name_value is not None
            else division_id.upper()
        )
        role_id_value = item.get("division_role_id")
        try:
            division_role_id = (
                int(role_id_value) if role_id_value not in (None, "", "None") else None
            )
        except (TypeError, ValueError):  # pragma: no cover - defensive
            division_role_id = None
        return cls(
            guild_id=guild_id,
            division_id=division_id,
            division_name=division_name,
            team_size=int(item["team_size"]),
            allowed_town_halls=[int(v) for v in item.get("allowed_town_halls", [])],
            max_teams=int(item["max_teams"]),
            updated_by=int(item.get("updated_by", 0)),
            updated_at=str(item.get("updated_at", "")),
            division_role_id=division_role_id,
        )


@dataclass(slots=True)
class PlayerEntry:
    name: str
    tag: str
    town_hall: int
    clan_name: str | None = None
    clan_tag: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "name": self.name,
            "tag": self.tag,
            "town_hall": self.town_hall,
        }
        if self.clan_name is not None:
            data["clan_name"] = self.clan_name
        if self.clan_tag is not None:
            data["clan_tag"] = self.clan_tag
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PlayerEntry:
        return cls(
            name=str(data.get("name", "")),
            tag=str(data.get("tag", "")),
            town_hall=int(data.get("town_hall", 0)),
            clan_name=(
                str(data.get("clan_name"))
                if data.get("clan_name") is not None
                else None
            ),
            clan_tag=(
                str(data.get("clan_tag")) if data.get("clan_tag") is not None else None
            ),
        )


@dataclass(slots=True)
class TeamRegistration:
    guild_id: int
    division_id: str
    user_id: int
    user_name: str
    players: list[PlayerEntry]
    registered_at: str
    team_name: str | None = None
    substitute: PlayerEntry | None = None

    PK_TEMPLATE: ClassVar[str] = "GUILD#%s"
    SK_TEMPLATE: ClassVar[str] = "DIVISION#%s#TEAM#%s"
    LEGACY_SK_TEMPLATE: ClassVar[str] = "TEAM#%s"

    @classmethod
    def key(cls, guild_id: int, division_id: str, user_id: int) -> dict[str, str]:
        return {
            "pk": cls.PK_TEMPLATE % guild_id,
            "sk": cls.SK_TEMPLATE % (division_id, user_id),
        }

    def to_item(self) -> dict[str, object]:
        item = self.key(self.guild_id, self.division_id, self.user_id)
        item.update(
            {
                "division_id": self.division_id,
                "user_id": str(self.user_id),
                "user_name": self.user_name,
                "registered_at": self.registered_at,
                "players": [player.to_dict() for player in self.players],
            }
        )
        if self.team_name is not None:
            item["team_name"] = self.team_name
        if self.substitute is not None:
            item["substitute"] = self.substitute.to_dict()
        return item

    @classmethod
    def from_item(cls, item: dict[str, object]) -> TeamRegistration:
        pk_value = str(item["pk"])
        guild_id = int(pk_value.split("#", 1)[1])
        sk_value = str(item.get("sk", ""))
        parts = sk_value.split("#")
        division_id = str(
            item.get("division_id") or (parts[1] if len(parts) > 2 else "default")
        )
        user_id = int(str(item.get("user_id") or parts[-1]))
        players_data: Iterable[dict[str, object]] = item.get("players", [])  # type: ignore[assignment]
        players = [PlayerEntry.from_dict(data) for data in players_data]
        team_name_value = item.get("team_name")
        substitute_data = item.get("substitute")
        substitute = (
            PlayerEntry.from_dict(substitute_data)  # type: ignore[arg-type]
            if isinstance(substitute_data, dict)
            else None
        )
        return cls(
            guild_id=guild_id,
            division_id=division_id,
            user_id=user_id,
            user_name=str(item.get("user_name", "")),
            players=players,
            registered_at=str(item.get("registered_at", "")),
            team_name=str(team_name_value) if team_name_value is not None else None,
            substitute=substitute,
        )

    @property
    def lines_for_channel(self) -> list[str]:
        lines: list[str] = []
        for player in self.players:
            clan_parts: list[str] = []
            if player.clan_name:
                clan_parts.append(player.clan_name)
            if player.clan_tag:
                clan_parts.append(player.clan_tag)
            clan_display = " ".join(clan_parts) if clan_parts else "No clan"
            lines.append(
                f"{player.name} (TH{player.town_hall})\n"
                f"  - Player Tag: {player.tag}\n"
                f"  - Clan: {clan_display}"
            )
        if self.substitute is not None:
            clan_parts: list[str] = []
            if self.substitute.clan_name:
                clan_parts.append(self.substitute.clan_name)
            if self.substitute.clan_tag:
                clan_parts.append(self.substitute.clan_tag)
            clan_display = " ".join(clan_parts) if clan_parts else "No clan"
            lines.append(
                f"{self.substitute.name} (TH{self.substitute.town_hall}) [Sub]\n"
                f"  - Player Tag: {self.substitute.tag}\n"
                f"  - Clan: {clan_display}"
            )
        return lines


@dataclass(slots=True)
class BracketSlot:
    seed: int | None
    team_id: int | None
    team_label: str
    source_match_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"team_label": self.team_label}
        if self.seed is not None:
            data["seed"] = self.seed
        if self.team_id is not None:
            data["team_id"] = str(self.team_id)
        if self.source_match_id is not None:
            data["source_match_id"] = self.source_match_id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BracketSlot:
        seed = data.get("seed")
        team_id = data.get("team_id")
        return cls(
            seed=int(seed) if seed is not None else None,
            team_id=int(team_id) if team_id is not None else None,
            team_label=str(data.get("team_label", "")),
            source_match_id=(
                str(data.get("source_match_id"))
                if data.get("source_match_id") is not None
                else None
            ),
        )

    def display(self) -> str:
        if self.team_id is None:
            return self.team_label
        if self.seed is not None:
            return f"#{self.seed} {self.team_label}"
        return self.team_label

    def adopt_from(self, other: BracketSlot) -> None:
        self.seed = other.seed
        self.team_id = other.team_id
        self.team_label = other.team_label


@dataclass(slots=True)
class BracketMatch:
    match_id: str
    round_index: int
    competitor_one: BracketSlot
    competitor_two: BracketSlot
    winner_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "match_id": self.match_id,
            "round_index": self.round_index,
            "competitor_one": self.competitor_one.to_dict(),
            "competitor_two": self.competitor_two.to_dict(),
        }
        if self.winner_index is not None:
            data["winner_index"] = self.winner_index
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BracketMatch:
        return cls(
            match_id=str(data.get("match_id", "")),
            round_index=int(data.get("round_index", 0)),
            competitor_one=BracketSlot.from_dict(
                data.get("competitor_one", {})  # type: ignore[arg-type]
            ),
            competitor_two=BracketSlot.from_dict(
                data.get("competitor_two", {})  # type: ignore[arg-type]
            ),
            winner_index=(
                int(data["winner_index"])
                if data.get("winner_index") is not None
                else None
            ),
        )

    def winner_slot(self) -> BracketSlot | None:
        if self.winner_index == 0:
            return self.competitor_one
        if self.winner_index == 1:
            return self.competitor_two
        return None


@dataclass(slots=True)
class BracketRound:
    name: str
    matches: list[BracketMatch]
    window_opens_at: str | None = None
    window_closes_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "name": self.name,
            "matches": [match.to_dict() for match in self.matches],
        }
        if self.window_opens_at is not None:
            data["window_opens_at"] = self.window_opens_at
        if self.window_closes_at is not None:
            data["window_closes_at"] = self.window_closes_at
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BracketRound:
        matches_data: Iterable[dict[str, object]] = data.get("matches", [])  # type: ignore[assignment]
        matches = [BracketMatch.from_dict(item) for item in matches_data]
        return cls(
            name=str(data.get("name", "")),
            matches=matches,
            window_opens_at=(
                str(data.get("window_opens_at"))
                if data.get("window_opens_at") is not None
                else None
            ),
            window_closes_at=(
                str(data.get("window_closes_at"))
                if data.get("window_closes_at") is not None
                else None
            ),
        )


@dataclass(slots=True)
class BracketState:
    guild_id: int
    division_id: str
    created_at: str
    rounds: list[BracketRound]

    PK_TEMPLATE: ClassVar[str] = "GUILD#%s"
    SK_TEMPLATE: ClassVar[str] = "DIVISION#%s#BRACKET"
    LEGACY_SK_VALUE: ClassVar[str] = "BRACKET"

    @classmethod
    def key(cls, guild_id: int, division_id: str) -> dict[str, str]:
        return {
            "pk": cls.PK_TEMPLATE % guild_id,
            "sk": cls.SK_TEMPLATE % division_id,
        }

    def to_item(self) -> dict[str, object]:
        item = self.key(self.guild_id, self.division_id)
        item.update(
            {
                "division_id": self.division_id,
                "created_at": self.created_at,
                "rounds": [round_.to_dict() for round_ in self.rounds],
            }
        )
        return item

    @classmethod
    def from_item(cls, item: dict[str, object]) -> BracketState:
        pk_value = str(item["pk"])
        guild_id = int(pk_value.split("#", 1)[1])
        sk_value = str(item.get("sk", ""))
        parts = sk_value.split("#")
        division_id = str(
            item.get("division_id") or (parts[1] if len(parts) > 2 else "default")
        )
        rounds_data: Iterable[dict[str, object]] = item.get("rounds", [])  # type: ignore[assignment]
        rounds = [BracketRound.from_dict(round_item) for round_item in rounds_data]
        return cls(
            guild_id=guild_id,
            division_id=division_id,
            created_at=str(item.get("created_at", "")),
            rounds=rounds,
        )

    def clone(self) -> BracketState:
        return BracketState.from_item(self.to_item())

    def find_match(self, match_id: str) -> BracketMatch | None:
        for round_ in self.rounds:
            for match in round_.matches:
                if match.match_id == match_id:
                    return match
        return None

    def all_matches(self) -> Iterable[BracketMatch]:
        for round_ in self.rounds:
            yield from round_.matches


__all__ = [
    "TournamentSeries",
    "TournamentRoundWindows",
    "RoundWindowDefinition",
    "TournamentConfig",
    "TeamRegistration",
    "PlayerEntry",
    "BracketSlot",
    "BracketMatch",
    "BracketRound",
    "BracketState",
    "utc_now_iso",
]
