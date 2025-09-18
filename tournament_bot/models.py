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
class TournamentConfig:
    guild_id: int
    team_size: int
    allowed_town_halls: list[int]
    max_teams: int
    updated_by: int
    updated_at: str

    PK_TEMPLATE: ClassVar[str] = "GUILD#%s"
    SK_VALUE: ClassVar[str] = "CONFIG"

    @classmethod
    def key(cls, guild_id: int) -> dict[str, str]:
        return {"pk": cls.PK_TEMPLATE % guild_id, "sk": cls.SK_VALUE}

    def to_item(self) -> dict[str, object]:
        item = self.key(self.guild_id)
        item.update(
            {
                "team_size": self.team_size,
                "allowed_town_halls": self.allowed_town_halls,
                "max_teams": self.max_teams,
                "updated_by": str(self.updated_by),
                "updated_at": self.updated_at,
            }
        )
        return item

    @classmethod
    def from_item(cls, item: dict[str, object]) -> TournamentConfig:
        guild_id = int(str(item["pk"]).split("#", 1)[1])
        return cls(
            guild_id=guild_id,
            team_size=int(item["team_size"]),
            allowed_town_halls=[int(v) for v in item.get("allowed_town_halls", [])],
            max_teams=int(item["max_teams"]),
            updated_by=int(item.get("updated_by", 0)),
            updated_at=str(item.get("updated_at", "")),
        )


@dataclass(slots=True)
class PlayerEntry:
    name: str
    tag: str
    town_hall: int

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "tag": self.tag, "town_hall": self.town_hall}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PlayerEntry:
        return cls(
            name=str(data.get("name", "")),
            tag=str(data.get("tag", "")),
            town_hall=int(data.get("town_hall", 0)),
        )


@dataclass(slots=True)
class TeamRegistration:
    guild_id: int
    user_id: int
    user_name: str
    players: list[PlayerEntry]
    registered_at: str

    PK_TEMPLATE: ClassVar[str] = "GUILD#%s"
    SK_TEMPLATE: ClassVar[str] = "TEAM#%s"

    @classmethod
    def key(cls, guild_id: int, user_id: int) -> dict[str, str]:
        return {
            "pk": cls.PK_TEMPLATE % guild_id,
            "sk": cls.SK_TEMPLATE % user_id,
        }

    def to_item(self) -> dict[str, object]:
        item = self.key(self.guild_id, self.user_id)
        item.update(
            {
                "user_id": str(self.user_id),
                "user_name": self.user_name,
                "registered_at": self.registered_at,
                "players": [player.to_dict() for player in self.players],
            }
        )
        return item

    @classmethod
    def from_item(cls, item: dict[str, object]) -> TeamRegistration:
        guild_id = int(str(item["pk"]).split("#", 1)[1])
        user_id = int(str(item.get("user_id") or str(item["sk"]).split("#", 1)[1]))
        players_data: Iterable[dict[str, object]] = item.get("players", [])  # type: ignore[assignment]
        players = [PlayerEntry.from_dict(data) for data in players_data]
        return cls(
            guild_id=guild_id,
            user_id=user_id,
            user_name=str(item.get("user_name", "")),
            players=players,
            registered_at=str(item.get("registered_at", "")),
        )

    @property
    def lines_for_channel(self) -> list[str]:
        return [
            f"{self.user_name} | {player.name} | {player.tag}"
            for player in self.players
        ]


__all__ = [
    "TournamentConfig",
    "TeamRegistration",
    "PlayerEntry",
    "utc_now_iso",
]
