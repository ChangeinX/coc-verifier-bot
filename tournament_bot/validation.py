from __future__ import annotations

import re


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
            raise InvalidTownHallError(f"Town hall level must be a number: {part}") from exc
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


__all__ = [
    "InvalidValueError",
    "InvalidTownHallError",
    "normalize_player_tag",
    "parse_player_tags",
    "parse_town_hall_levels",
    "validate_team_size",
    "validate_max_teams",
]
