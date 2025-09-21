"""Tests for the shadow reporting module."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from bots.shadow import ShadowReporter
from bots.config import ShadowConfig


class TestShadowReporter:
    """Test shadow reporter functionality."""

    def test_init_enabled(self):
        """Should initialize with enabled configuration."""
        config = ShadowConfig(enabled=True, channel_id=123456)
        bot = MagicMock()

        reporter = ShadowReporter(bot, config)

        assert reporter.enabled is True
        assert reporter.channel_id == 123456
        assert reporter._bot == bot

    def test_init_disabled(self):
        """Should initialize with disabled configuration."""
        config = ShadowConfig(enabled=False, channel_id=None)
        bot = MagicMock()

        reporter = ShadowReporter(bot, config)

        assert reporter.enabled is False
        assert reporter.channel_id is None
        assert reporter._bot == bot

    @pytest.mark.asyncio
    async def test_report_disabled(self):
        """Should not report when disabled."""
        config = ShadowConfig(enabled=False, channel_id=None)
        bot = MagicMock()
        reporter = ShadowReporter(bot, config)

        # Should return early when disabled
        await reporter.report(None, "test message")

        # Bot should not be called
        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_report_no_channel_id(self):
        """Should not report when no channel ID configured."""
        config = ShadowConfig(enabled=True, channel_id=None)
        bot = MagicMock()
        reporter = ShadowReporter(bot, config)

        await reporter.report(None, "test message")

        # Bot should not be called
        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_report_success_with_guild(self):
        """Should report successfully when enabled and configured with guild."""
        config = ShadowConfig(enabled=True, channel_id=123456)
        bot = MagicMock()
        channel = AsyncMock()
        guild = MagicMock()
        guild.get_channel.return_value = channel

        reporter = ShadowReporter(bot, config)

        await reporter.report(guild, "test message")

        guild.get_channel.assert_called_once_with(123456)
        channel.send.assert_called_once_with(content="test message")

    @pytest.mark.asyncio
    async def test_report_success_with_bot(self):
        """Should report successfully using bot channel when guild channel not found."""
        config = ShadowConfig(enabled=True, channel_id=123456)
        bot = MagicMock()
        channel = AsyncMock()
        guild = MagicMock()
        guild.get_channel.return_value = None
        bot.get_channel.return_value = channel

        reporter = ShadowReporter(bot, config)

        await reporter.report(guild, "test message")

        guild.get_channel.assert_called_once_with(123456)
        bot.get_channel.assert_called_once_with(123456)
        channel.send.assert_called_once_with(content="test message")

    @pytest.mark.asyncio
    async def test_report_with_embeds(self):
        """Should report with embeds when provided."""
        config = ShadowConfig(enabled=True, channel_id=123456)
        bot = MagicMock()
        channel = AsyncMock()
        guild = MagicMock()
        guild.get_channel.return_value = channel

        reporter = ShadowReporter(bot, config)
        embeds = [MagicMock(), MagicMock()]

        await reporter.report(guild, "test message", embeds=embeds)

        guild.get_channel.assert_called_once_with(123456)
        channel.send.assert_called_once_with(content="test message", embeds=embeds)

    @pytest.mark.asyncio
    async def test_report_no_guild(self):
        """Should report without guild name when guild is None."""
        config = ShadowConfig(enabled=True, channel_id=123456)
        bot = MagicMock()
        channel = AsyncMock()
        bot.get_channel.return_value = channel

        reporter = ShadowReporter(bot, config)

        await reporter.report(None, "test message")

        bot.get_channel.assert_called_once_with(123456)
        channel.send.assert_called_once_with(content="test message")

    @pytest.mark.asyncio
    async def test_report_channel_not_found_fetch_succeeds(self):
        """Should handle case where channel is not found but fetch succeeds."""
        config = ShadowConfig(enabled=True, channel_id=123456)
        bot = MagicMock()
        bot.get_channel.return_value = None
        channel = AsyncMock()
        bot.fetch_channel.return_value = channel

        reporter = ShadowReporter(bot, config)
        guild = MagicMock()
        guild.get_channel.return_value = None

        await reporter.report(guild, "test message")

        guild.get_channel.assert_called_once_with(123456)
        bot.get_channel.assert_called_once_with(123456)
        bot.fetch_channel.assert_called_once_with(123456)
        channel.send.assert_called_once_with(content="test message")

    @pytest.mark.asyncio
    async def test_report_fetch_channel_fails(self):
        """Should handle case where fetching channel fails."""
        import discord
        config = ShadowConfig(enabled=True, channel_id=123456)
        bot = MagicMock()
        bot.get_channel.return_value = None
        bot.fetch_channel.side_effect = discord.NotFound(MagicMock(), "Channel not found")

        reporter = ShadowReporter(bot, config)
        guild = MagicMock()
        guild.get_channel.return_value = None

        # Should not raise exception
        await reporter.report(guild, "test message")

        guild.get_channel.assert_called_once_with(123456)
        bot.get_channel.assert_called_once_with(123456)
        bot.fetch_channel.assert_called_once_with(123456)

    @pytest.mark.asyncio
    async def test_report_send_fails(self):
        """Should handle case where sending message fails."""
        import discord
        config = ShadowConfig(enabled=True, channel_id=123456)
        bot = MagicMock()
        channel = AsyncMock()
        channel.send.side_effect = discord.HTTPException(MagicMock(), "Send failed")
        guild = MagicMock()
        guild.get_channel.return_value = channel

        reporter = ShadowReporter(bot, config)

        # Should not raise exception
        await reporter.report(guild, "test message")

        guild.get_channel.assert_called_once_with(123456)
        channel.send.assert_called_once_with(content="test message")

    @pytest.mark.asyncio
    async def test_report_channel_not_messageable(self):
        """Should handle case where channel is not messageable."""
        config = ShadowConfig(enabled=True, channel_id=123456)
        bot = MagicMock()
        channel = MagicMock()  # Not messageable
        guild = MagicMock()
        guild.get_channel.return_value = channel

        reporter = ShadowReporter(bot, config)

        # Should not raise exception
        await reporter.report(guild, "test message")

        guild.get_channel.assert_called_once_with(123456)

    @pytest.mark.asyncio
    async def test_noop_or_run_enabled(self):
        """Should report and return None when enabled."""
        config = ShadowConfig(enabled=True, channel_id=None)  # Use logging fallback
        bot = MagicMock()

        reporter = ShadowReporter(bot, config)
        coro = AsyncMock()

        result = await reporter.noop_or_run("test description", coro)

        assert result is None
        coro.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_or_run_disabled(self):
        """Should execute coroutine when disabled."""
        config = ShadowConfig(enabled=False, channel_id=None)
        bot = MagicMock()

        reporter = ShadowReporter(bot, config)
        coro = AsyncMock(return_value="test_result")

        result = await reporter.noop_or_run("test description", coro)

        assert result == "test_result"
        coro.assert_called_once()