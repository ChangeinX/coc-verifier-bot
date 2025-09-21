from __future__ import annotations

import re
from datetime import UTC, datetime


class InvalidValueError(ValueError):
    """Base exception for validation failures."""


class InvalidTownHallError(InvalidValueError):
    """Raised when town hall levels outside the supported range are provided."""


_TAG_PATTERN = re.compile(r"#[A-Z0-9]+$")
_SPLIT_PATTERN = re.compile(r"[\s,]+")


def normalize_player_tag(tag: str) -> str:
    tag = tag.strip().upper()
    if not tag:
        raise InvalidValueError("Player tag cannot be empty")
    if not tag.startswith("#"):
        tag = "#" + tag
    if not _TAG_PATTERN.match(tag):
        raise InvalidValueError(f"Invalid player tag: {tag}")
    return tag


def parse_player_tags(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        raise InvalidValueError("At least one player tag is required")
    parts = [p for p in _SPLIT_PATTERN.split(raw) if p]
    if not parts:
        raise InvalidValueError("At least one player tag is required")
    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = normalize_player_tag(part)
        if tag in seen:
            raise InvalidValueError(f"Duplicate player tag provided: {tag}")
        seen.add(tag)
        normalized.append(tag)
    return normalized


def parse_town_hall_levels(raw: str) -> list[int]:
    raw = raw.strip()
    if not raw:
        raise InvalidTownHallError("At least one town hall level must be provided")
    parts = [p for p in _SPLIT_PATTERN.split(raw) if p]
    if not parts:
        raise InvalidTownHallError("At least one town hall level must be provided")
    levels: set[int] = set()
    for part in parts:
        try:
            level = int(part)
        except ValueError as exc:
            raise InvalidTownHallError(
                f"Town hall level must be a number: {part}"
            ) from exc
        if level < 1 or level > 25:
            raise InvalidTownHallError(
                f"Town hall level {level} is outside supported range (1-25)"
            )
        levels.add(level)
    return sorted(levels)


def validate_team_size(team_size: int) -> int:
    if team_size < 5:
        raise InvalidValueError("Team size must be at least 5 players")
    if team_size % 5 != 0:
        raise InvalidValueError("Team size must be in increments of 5")
    if team_size > 50:
        raise InvalidValueError("Team size above 50 players is not supported")
    return team_size


def validate_max_teams(max_teams: int) -> int:
    if max_teams < 2:
        raise InvalidValueError("Maximum teams must be at least 2")
    if max_teams % 2 != 0:
        raise InvalidValueError("Maximum teams must be in increments of 2")
    if max_teams > 200:
        raise InvalidValueError("Maximum teams above 200 are not supported")
    return max_teams


def validate_team_name(raw: str) -> str:
    name = raw.strip()
    if len(name) < 3:
        raise InvalidValueError("Team name must be at least 3 characters long")
    if len(name) > 100:
        raise InvalidValueError("Team name must be 100 characters or fewer")
    return name


def parse_registration_datetime(raw: str) -> datetime:
    value = raw.strip()
    if not value:
        raise InvalidValueError("A date/time value is required")

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InvalidValueError(
            "Use ISO format such as 2024-05-01T18:00 or 2024-05-01 18:00"
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed


def validate_registration_window(
    opens_at: datetime, closes_at: datetime
) -> tuple[datetime, datetime]:
    opens_at_utc = opens_at.astimezone(UTC)
    closes_at_utc = closes_at.astimezone(UTC)

    if closes_at_utc <= opens_at_utc:
        raise InvalidValueError("Registration end must be after the start time")
    return opens_at_utc, closes_at_utc


__all__ = [
    "InvalidValueError",
    "InvalidTownHallError",
    "normalize_player_tag",
    "parse_player_tags",
    "parse_town_hall_levels",
    "validate_team_size",
    "validate_max_teams",
    "parse_registration_datetime",
    "validate_registration_window",
    "validate_team_name",
]
