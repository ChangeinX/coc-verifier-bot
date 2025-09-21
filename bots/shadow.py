"""Utilities for shadow-mode reporting."""

from __future__ import annotations

import logging
from typing import Iterable

import discord

from .config import ShadowConfig

log = logging.getLogger(__name__)


class ShadowReporter:
    def __init__(self, bot: discord.Client, config: ShadowConfig) -> None:
        self._bot = bot
        self._config = config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def channel_id(self) -> int | None:
        return self._config.channel_id

    async def report(
        self,
        guild: discord.Guild | None,
        message: str,
        *,
        embeds: Iterable[discord.Embed] | None = None,
    ) -> None:
        if not self.enabled:
            return

        if self.channel_id is None:
            log.info("[SHADOW] %s", message)
            return

        channel = None
        if guild is not None:
            channel = guild.get_channel(self.channel_id)
        if channel is None:
            channel = self._bot.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(self.channel_id)
            except discord.DiscordException as exc:  # pragma: no cover - network failure
                log.warning("Unable to fetch shadow channel %s: %s", self.channel_id, exc)
                channel = None

        if not isinstance(channel, discord.abc.Messageable):
            log.info("[SHADOW] %s", message)
            return

        try:
            kwargs = {"content": message}
            if embeds is not None:
                kwargs["embeds"] = list(embeds)
            await channel.send(**kwargs)
        except discord.DiscordException as exc:  # pragma: no cover - network failure
            log.warning(
                "Failed to send shadow report to channel %s: %s", self.channel_id, exc
            )

    async def noop_or_run(self, description: str, coro):
        if self.enabled:
            await self.report(None, f"[noop] {description}")
            return None
        return await coro
