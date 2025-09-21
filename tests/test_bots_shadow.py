"""Tests for bots.shadow module."""

from unittest import mock

import discord
import pytest

from bots.config import ShadowConfig
from bots.shadow import ShadowReporter


class MockBot:
    """Mock Discord bot for testing."""

    def __init__(self):
        self.channels = {}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        if channel_id == 999:  # Simulate fetch failure
            raise discord.NotFound(mock.Mock(), "Channel not found")
        return self.channels.get(channel_id)


class MockGuild:
    """Mock Discord guild for testing."""

    def __init__(self):
        self.channels = {}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)


class MockChannel(discord.abc.Messageable):
    """Mock Discord channel for testing."""

    def __init__(self, channel_id, is_messageable=True, send_failure=False):
        self.id = channel_id
        self.is_messageable = is_messageable
        self.send_failure = send_failure
        self.sent_messages = []

    async def send(self, **kwargs):
        if self.send_failure:
            raise discord.HTTPException(mock.Mock(), "Failed to send")
        self.sent_messages.append(kwargs)
        return mock.Mock()

    async def _get_channel(self):
        return self

    def _get_guild(self):
        return None


class TestShadowReporter:
    """Test ShadowReporter class."""

    def test_init(self):
        """Should initialize with bot and config."""
        bot = MockBot()
        config = ShadowConfig(enabled=True, channel_id=123)
        reporter = ShadowReporter(bot, config)

        assert reporter._bot is bot
        assert reporter._config is config

    def test_enabled_property(self):
        """Should return config enabled status."""
        bot = MockBot()

        # Test enabled
        config = ShadowConfig(enabled=True, channel_id=123)
        reporter = ShadowReporter(bot, config)
        assert reporter.enabled is True

        # Test disabled
        config = ShadowConfig(enabled=False, channel_id=123)
        reporter = ShadowReporter(bot, config)
        assert reporter.enabled is False

    def test_channel_id_property(self):
        """Should return config channel_id."""
        bot = MockBot()

        # Test with channel ID
        config = ShadowConfig(enabled=True, channel_id=123456)
        reporter = ShadowReporter(bot, config)
        assert reporter.channel_id == 123456

        # Test with None channel ID
        config = ShadowConfig(enabled=True, channel_id=None)
        reporter = ShadowReporter(bot, config)
        assert reporter.channel_id is None

    @pytest.mark.asyncio
    async def test_report_disabled(self):
        """Should do nothing when disabled."""
        bot = MockBot()
        config = ShadowConfig(enabled=False, channel_id=123)
        reporter = ShadowReporter(bot, config)

        with mock.patch("bots.shadow.log") as mock_log:
            await reporter.report(None, "test message")
            mock_log.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_report_no_channel_id(self):
        """Should log to console when no channel ID."""
        bot = MockBot()
        config = ShadowConfig(enabled=True, channel_id=None)
        reporter = ShadowReporter(bot, config)

        with mock.patch("bots.shadow.log") as mock_log:
            await reporter.report(None, "test message")
            mock_log.info.assert_called_once_with("[SHADOW] %s", "test message")

    @pytest.mark.asyncio
    async def test_report_guild_channel_found(self):
        """Should send to guild channel when found."""
        bot = MockBot()
        guild = MockGuild()
        channel = MockChannel(123)
        guild.channels[123] = channel

        config = ShadowConfig(enabled=True, channel_id=123)
        reporter = ShadowReporter(bot, config)

        await reporter.report(guild, "test message")

        assert len(channel.sent_messages) == 1
        assert channel.sent_messages[0]["content"] == "test message"

    @pytest.mark.asyncio
    async def test_report_bot_channel_found(self):
        """Should send to bot channel when guild channel not found."""
        bot = MockBot()
        guild = MockGuild()
        channel = MockChannel(123)
        bot.channels[123] = channel

        config = ShadowConfig(enabled=True, channel_id=123)
        reporter = ShadowReporter(bot, config)

        await reporter.report(guild, "test message")

        assert len(channel.sent_messages) == 1
        assert channel.sent_messages[0]["content"] == "test message"

    @pytest.mark.asyncio
    async def test_report_fetch_channel_success(self):
        """Should fetch channel when not found in cache."""
        bot = MockBot()
        guild = MockGuild()
        channel = MockChannel(123)

        config = ShadowConfig(enabled=True, channel_id=123)
        reporter = ShadowReporter(bot, config)

        with mock.patch.object(
            bot, "fetch_channel", return_value=channel
        ) as mock_fetch:
            await reporter.report(guild, "test message")
            mock_fetch.assert_called_once_with(123)

        assert len(channel.sent_messages) == 1
        assert channel.sent_messages[0]["content"] == "test message"

    @pytest.mark.asyncio
    async def test_report_fetch_channel_failure(self):
        """Should log when fetch_channel fails."""
        bot = MockBot()
        guild = MockGuild()

        config = ShadowConfig(enabled=True, channel_id=999)
        reporter = ShadowReporter(bot, config)

        with mock.patch("bots.shadow.log") as mock_log:
            await reporter.report(guild, "test message")
            mock_log.warning.assert_called()
            # Should also log the message
            mock_log.info.assert_called_with("[SHADOW] %s", "test message")

    @pytest.mark.asyncio
    async def test_report_non_messageable_channel(self):
        """Should log when channel is not messageable."""
        bot = MockBot()
        guild = MockGuild()

        # Create a non-messageable channel
        class NonMessageableChannel:
            def __init__(self):
                self.id = 123

        channel = NonMessageableChannel()
        guild.channels[123] = channel

        config = ShadowConfig(enabled=True, channel_id=123)
        reporter = ShadowReporter(bot, config)

        with mock.patch("bots.shadow.log") as mock_log:
            await reporter.report(guild, "test message")
            mock_log.info.assert_called_with("[SHADOW] %s", "test message")

    @pytest.mark.asyncio
    async def test_report_with_embeds(self):
        """Should send message with embeds."""
        bot = MockBot()
        channel = MockChannel(123)
        bot.channels[123] = channel

        config = ShadowConfig(enabled=True, channel_id=123)
        reporter = ShadowReporter(bot, config)

        embed1 = discord.Embed(title="Test Embed 1")
        embed2 = discord.Embed(title="Test Embed 2")
        embeds = [embed1, embed2]

        await reporter.report(None, "test message", embeds=embeds)

        assert len(channel.sent_messages) == 1
        sent = channel.sent_messages[0]
        assert sent["content"] == "test message"
        assert sent["embeds"] == embeds

    @pytest.mark.asyncio
    async def test_report_send_failure(self):
        """Should log when sending message fails."""
        bot = MockBot()
        channel = MockChannel(123, send_failure=True)
        bot.channels[123] = channel

        config = ShadowConfig(enabled=True, channel_id=123)
        reporter = ShadowReporter(bot, config)

        with mock.patch("bots.shadow.log") as mock_log:
            await reporter.report(None, "test message")
            mock_log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_noop_or_run_enabled(self):
        """Should report noop message and return None when enabled."""
        bot = MockBot()
        channel = MockChannel(123)
        bot.channels[123] = channel

        config = ShadowConfig(enabled=True, channel_id=123)
        reporter = ShadowReporter(bot, config)

        async def dummy_coro():
            return "should not run"

        # Pass the coroutine object, not call it
        result = await reporter.noop_or_run("test operation", dummy_coro())

        assert result is None
        assert len(channel.sent_messages) == 1
        assert channel.sent_messages[0]["content"] == "[noop] test operation"

    @pytest.mark.asyncio
    async def test_noop_or_run_disabled(self):
        """Should run coroutine and return result when disabled."""
        bot = MockBot()
        config = ShadowConfig(enabled=False, channel_id=123)
        reporter = ShadowReporter(bot, config)

        async def dummy_coro():
            return "expected result"

        result = await reporter.noop_or_run("test operation", dummy_coro())

        assert result == "expected result"


# Integration test to ensure shadow reporter works with actual discord.py types
@pytest.mark.asyncio
async def test_shadow_reporter_with_discord_abc_messageable():
    """Test that ShadowReporter works with discord.abc.Messageable."""
    bot = MockBot()

    # Create a mock that inherits from discord.abc.Messageable
    class MockMessageableChannel(discord.abc.Messageable):
        def __init__(self):
            self.id = 123
            self.sent_messages = []

        async def send(self, **kwargs):
            self.sent_messages.append(kwargs)
            return mock.Mock()

        # Required abstract methods
        async def _get_channel(self):
            return self

        def _get_guild(self):
            return None

    channel = MockMessageableChannel()
    bot.channels[123] = channel

    config = ShadowConfig(enabled=True, channel_id=123)
    reporter = ShadowReporter(bot, config)

    await reporter.report(None, "test message")

    assert len(channel.sent_messages) == 1
    assert channel.sent_messages[0]["content"] == "test message"
