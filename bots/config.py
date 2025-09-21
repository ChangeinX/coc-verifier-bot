"""Configuration helpers for unified runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return default


def env_int(name: str, *, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ShadowConfig:
    enabled: bool
    channel_id: int | None


def read_shadow_config(*, default_enabled: bool = False) -> ShadowConfig:
    return ShadowConfig(
        enabled=env_bool("SHADOW_MODE", default=default_enabled),
        channel_id=env_int("SHADOW_CHANNEL_ID"),
    )
