"""Tests for the verification bot (bot.py)."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, PropertyMock, patch

import coc
import discord
import pytest

# Mock environment variables before importing
with patch.dict(
    os.environ,
    {
        "DISCORD_TOKEN": "fake_token",
        "COC_EMAIL": "fake_email",
        "COC_PASSWORD": "fake_password",
        "CLAN_TAG": "#TESTCLAN",
        "VERIFIED_ROLE_ID": "12345",
        "DDB_TABLE_NAME": "test_table",
        "AWS_REGION": "us-east-1",
        "ADMIN_LOG_CHANNEL_ID": "67890",
    },
):
    import bot

from verifier_bot import approvals


class TestEnvironmentValidation:
    """Test environment variable validation."""

    def test_required_vars_defined(self):
        """Test that required environment variables are properly defined."""
        assert bot.DISCORD_TOKEN is not None
        assert bot.COC_EMAIL is not None
        assert bot.COC_PASSWORD is not None
        assert bot.CLAN_TAG is not None
        assert bot.VERIFIED_ROLE_ID == 12345
        assert bot.DDB_TABLE_NAME is not None
        assert bot.AWS_REGION == "us-east-1"


class TestLogChannelResolution:
    """Test log channel resolution functionality."""

    @pytest.mark.asyncio
    async def test_resolve_log_channel_no_id(self):
        """Test resolve_log_channel with no ADMIN_LOG_CHANNEL_ID."""
        with patch.object(bot, "ADMIN_LOG_CHANNEL_ID", 0):
            guild = MagicMock()
            result = await bot.resolve_log_channel(guild)
            assert result is None

    @pytest.mark.asyncio
    async def test_resolve_log_channel_from_guild_cache(self):
        """Test resolve_log_channel finding channel in guild cache."""
        guild = MagicMock()
        text_channel = MagicMock(spec=discord.TextChannel)
        guild.get_channel.return_value = text_channel

        with patch.object(bot, "ADMIN_LOG_CHANNEL_ID", 67890):
            result = await bot.resolve_log_channel(guild)
            assert result == text_channel
            guild.get_channel.assert_called_once_with(67890)

    @pytest.mark.asyncio
    async def test_resolve_log_channel_from_bot_cache(self):
        """Test resolve_log_channel finding channel in bot global cache."""
        guild = MagicMock()
        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.guild.id = guild.id
        guild.get_channel.return_value = None

        with (
            patch.object(bot, "ADMIN_LOG_CHANNEL_ID", 67890),
            patch.object(bot.bot, "fetch_channel", return_value=text_channel),
        ):
            result = await bot.resolve_log_channel(guild)
            assert result == text_channel

    @pytest.mark.asyncio
    async def test_resolve_log_channel_fetch_from_api(self):
        """Test resolve_log_channel fetching channel from Discord API."""
        guild = MagicMock()
        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.guild.id = guild.id
        guild.get_channel.return_value = None

        with (
            patch.object(bot, "ADMIN_LOG_CHANNEL_ID", 67890),
            patch.object(
                bot.bot, "fetch_channel", return_value=text_channel
            ) as fetch_mock,
        ):
            result = await bot.resolve_log_channel(guild)
            assert result == text_channel
            fetch_mock.assert_called_once_with(67890)

    @pytest.mark.asyncio
    async def test_resolve_log_channel_fetch_fails(self):
        """Test resolve_log_channel handling API fetch failure."""
        guild = MagicMock()
        guild.get_channel.return_value = None

        # Create a proper mock response for HTTPException
        mock_response = MagicMock()
        mock_response.status = 404

        with (
            patch.object(bot, "ADMIN_LOG_CHANNEL_ID", 67890),
            patch.object(
                bot.bot,
                "fetch_channel",
                side_effect=discord.NotFound(mock_response, "Channel not found"),
            ) as fetch_mock,
        ):
            result = await bot.resolve_log_channel(guild)
            assert result is None
            fetch_mock.assert_called_once_with(67890)

    @pytest.mark.asyncio
    async def test_resolve_log_channel_not_text_channel(self):
        """Test resolve_log_channel with non-text channel."""
        guild = MagicMock()
        voice_channel = MagicMock(spec=discord.VoiceChannel)
        voice_channel.guild.id = guild.id
        guild.get_channel.return_value = None

        with (
            patch.object(bot, "ADMIN_LOG_CHANNEL_ID", 67890),
            patch.object(bot.bot, "fetch_channel", return_value=voice_channel),
        ):
            result = await bot.resolve_log_channel(guild)
            assert result is None


class TestClashAPIFunctions:
    """Test Clash of Clans API interaction functions."""

    @pytest.mark.asyncio
    async def test_get_player_success(self):
        """Test successful player retrieval."""
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"

        # Mock the entire coc_client since get_player is read-only
        mock_client = AsyncMock(spec=coc.Client)
        mock_client.get_player = AsyncMock(return_value=mock_player)

        with patch.object(bot, "coc_client", mock_client):
            result = await bot.get_player("#PLAYER1")
            assert result == mock_player
            mock_client.get_player.assert_called_once_with("#PLAYER1")

    @pytest.mark.asyncio
    async def test_get_player_not_found(self):
        """Test player not found scenario."""
        mock_client = AsyncMock(spec=coc.Client)
        mock_client.get_player = AsyncMock(side_effect=coc.NotFound("Player not found"))

        with patch.object(bot, "coc_client", mock_client):
            result = await bot.get_player("#INVALID")
            assert result is None
            mock_client.get_player.assert_called_once_with("#INVALID")

    @pytest.mark.asyncio
    async def test_get_player_http_exception(self):
        """Test CoC API HTTP exception."""
        mock_client = AsyncMock(spec=coc.Client)
        # Create a proper mock response for coc.HTTPException
        mock_response = MagicMock()
        mock_response.status = 500
        mock_client.get_player = AsyncMock(
            side_effect=coc.HTTPException(mock_response, "Server error")
        )

        with patch.object(bot, "coc_client", mock_client):
            result = await bot.get_player("#PLAYER1")
            assert result is None
            mock_client.get_player.assert_called_once_with("#PLAYER1")

    @pytest.mark.asyncio
    async def test_is_member_of_clan_true(self):
        """Test player is member of clan."""
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#TESTCLAN"
        mock_player.clan = mock_clan

        with (
            patch.object(bot, "get_player", return_value=mock_player),
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
        ):
            result = await bot.is_member_of_clan("#PLAYER1")
            assert result is True

    @pytest.mark.asyncio
    async def test_is_member_of_clan_false_different_clan(self):
        """Test player is member of different clan."""
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#OTHERCLAN"
        mock_player.clan = mock_clan

        with (
            patch.object(bot, "get_player", return_value=mock_player),
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
        ):
            result = await bot.is_member_of_clan("#PLAYER1")
            assert result is False

    @pytest.mark.asyncio
    async def test_is_member_of_feeder_clan_true(self):
        """Test player is member of feeder clan."""
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#FEEDERCLAN"
        mock_player.clan = mock_clan

        with (
            patch.object(bot, "get_player", return_value=mock_player),
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
            patch.object(bot, "FEEDER_CLAN_TAG", "#FEEDERCLAN"),
        ):
            result = await bot.is_member_of_clan("#PLAYER1")
            assert result is True

    @pytest.mark.asyncio
    async def test_get_player_clan_tag_main_clan(self):
        """Test get_player_clan_tag returns main clan tag."""
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#TESTCLAN"
        mock_player.clan = mock_clan

        with (
            patch.object(bot, "get_player", return_value=mock_player),
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
            patch.object(bot, "FEEDER_CLAN_TAG", "#FEEDERCLAN"),
        ):
            result = await bot.get_player_clan_tag("#PLAYER1")
            assert result == "#TESTCLAN"

    @pytest.mark.asyncio
    async def test_get_player_clan_tag_feeder_clan(self):
        """Test get_player_clan_tag returns feeder clan tag."""
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#FEEDERCLAN"
        mock_player.clan = mock_clan

        with (
            patch.object(bot, "get_player", return_value=mock_player),
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
            patch.object(bot, "FEEDER_CLAN_TAG", "#FEEDERCLAN"),
        ):
            result = await bot.get_player_clan_tag("#PLAYER1")
            assert result == "#FEEDERCLAN"

    @pytest.mark.asyncio
    async def test_get_player_clan_tag_no_match(self):
        """Test get_player_clan_tag returns None for non-member."""
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#OTHERCLAN"
        mock_player.clan = mock_clan

        with (
            patch.object(bot, "get_player", return_value=mock_player),
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
            patch.object(bot, "FEEDER_CLAN_TAG", "#FEEDERCLAN"),
        ):
            result = await bot.get_player_clan_tag("#PLAYER1")
            assert result is None

    @pytest.mark.asyncio
    async def test_is_member_of_clan_false_no_clan(self):
        """Test player has no clan."""
        mock_player = MagicMock(spec=coc.Player)
        mock_player.clan = None

        with patch.object(bot, "get_player", return_value=mock_player):
            result = await bot.is_member_of_clan("#PLAYER1")
            assert result is False

    @pytest.mark.asyncio
    async def test_is_member_of_clan_false_no_player(self):
        """Test player doesn't exist."""
        with patch.object(bot, "get_player", return_value=None):
            result = await bot.is_member_of_clan("#INVALID")
            assert result is False


class TestVerifyCommand:
    """Test the /verify command functionality."""

    @pytest.fixture
    def mock_interaction(self):
        """Create a mock Discord interaction."""
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 123456789
        interaction.user.name = "TestUser"
        interaction.user.add_roles = AsyncMock()
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.guild.id = 987654321
        # Properly set up nested async methods
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    @pytest.fixture
    def mock_role(self):
        """Create a mock Discord role."""
        role = MagicMock(spec=discord.Role)
        role.id = 12345
        return role

    @pytest.mark.asyncio
    async def test_verify_success(self, mock_interaction, mock_role):
        """Test successful verification."""
        # Setup mocks
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"
        mock_player.name = "TestPlayer"
        mock_clan = MagicMock()
        mock_clan.tag = "#TESTCLAN"
        mock_player.clan = mock_clan

        mock_interaction.guild.get_role.return_value = mock_role

        # Mock DynamoDB table
        mock_table = MagicMock()

        with (
            patch.object(
                bot, "get_player", new_callable=AsyncMock, return_value=mock_player
            ),
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
            patch.object(bot, "table", mock_table),
            patch.object(
                bot, "resolve_log_channel", new_callable=AsyncMock, return_value=None
            ),
        ):
            await bot.verify.callback(mock_interaction, "#PLAYER1")

            # Verify interaction responses
            mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
            mock_interaction.followup.send.assert_called_once_with(
                "✅ Success! You now have access.", ephemeral=True
            )

            # Verify role assignment
            mock_interaction.user.add_roles.assert_called_once_with(
                mock_role, reason="Passed CoC verification"
            )

            # Verify database storage
            mock_table.put_item.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_player_tag_normalization(self, mock_interaction, mock_role):
        """Test player tag is properly normalized."""
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"
        mock_player.name = "TestPlayer"
        mock_clan = MagicMock()
        mock_clan.tag = "#TESTCLAN"
        mock_player.clan = mock_clan

        mock_interaction.guild.get_role.return_value = mock_role

        with (
            patch.object(bot, "get_player") as get_player_mock,
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
            patch.object(bot, "table", MagicMock()),
            patch.object(bot, "resolve_log_channel", return_value=None),
        ):
            get_player_mock.return_value = mock_player

            # Test with lowercase and no # prefix
            await bot.verify.callback(mock_interaction, "player1")
            get_player_mock.assert_called_with("#PLAYER1")

    @pytest.mark.asyncio
    async def test_verify_feeder_clan_success(self, mock_interaction, mock_role):
        """Test successful verification for feeder clan member."""
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"
        mock_player.name = "TestPlayer"
        mock_clan = MagicMock()
        mock_clan.tag = "#FEEDERCLAN"
        mock_player.clan = mock_clan

        mock_interaction.guild.get_role.return_value = mock_role
        mock_table = MagicMock()

        with (
            patch.object(bot, "get_player") as get_player_mock,
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
            patch.object(bot, "FEEDER_CLAN_TAG", "#FEEDERCLAN"),
            patch.object(bot, "table", mock_table),
            patch.object(bot, "resolve_log_channel", return_value=None),
        ):
            get_player_mock.return_value = mock_player

            await bot.verify.callback(mock_interaction, "#PLAYER1")

            mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
            mock_interaction.followup.send.assert_called_once_with(
                "✅ Success! You now have access.", ephemeral=True
            )
            mock_interaction.user.add_roles.assert_called_once_with(
                mock_role, reason="Passed CoC verification"
            )

            # Verify database storage includes clan_tag
            mock_table.put_item.assert_called_once()
            stored_item = mock_table.put_item.call_args[1]["Item"]
            assert stored_item["clan_tag"] == "#FEEDERCLAN"

    @pytest.mark.asyncio
    async def test_verify_not_clan_member(self, mock_interaction):
        """Test verification fails when player is not in clan."""
        with patch.object(bot, "get_player", return_value=None):
            await bot.verify.callback(mock_interaction, "#PLAYER1")

            mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
            mock_interaction.followup.send.assert_called_once_with(
                "❌ Verification failed – player not found or CoC API unavailable.",
                ephemeral=True,
            )

    @pytest.mark.asyncio
    async def test_verify_role_not_found(self, mock_interaction):
        """Test verification fails when verified role is not found."""
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"
        mock_clan = MagicMock()
        mock_clan.tag = "#TESTCLAN"
        mock_player.clan = mock_clan

        mock_interaction.guild.get_role.return_value = None

        with (
            patch.object(bot, "get_player", return_value=mock_player),
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
        ):
            await bot.verify.callback(mock_interaction, "#PLAYER1")

            mock_interaction.followup.send.assert_called_once_with(
                "Setup error: verified role not found – contact an admin.",
                ephemeral=True,
            )

    @pytest.mark.asyncio
    async def test_verify_forbidden_role_assignment(self, mock_interaction, mock_role):
        """Test verification fails when bot lacks permission to assign role."""
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"
        mock_clan = MagicMock()
        mock_clan.tag = "#TESTCLAN"
        mock_player.clan = mock_clan

        mock_interaction.guild.get_role.return_value = mock_role

        # Create proper mock response for Forbidden exception
        mock_response = MagicMock()
        mock_response.status = 403
        mock_interaction.user.add_roles.side_effect = discord.Forbidden(
            mock_response, "Missing permissions"
        )

        with (
            patch.object(bot, "get_player", return_value=mock_player),
            patch.object(bot, "CLAN_TAG", "#TESTCLAN"),
        ):
            await bot.verify.callback(mock_interaction, "#PLAYER1")

            mock_interaction.followup.send.assert_called_once_with(
                "🚫 Bot lacks **Manage Roles** permission or the role hierarchy is incorrect.",
                ephemeral=True,
            )

    @pytest.mark.asyncio
    async def test_verify_coc_api_403_error(self, mock_interaction):
        """Test verification handles CoC API 403 authentication errors properly."""
        # Mock a 403 authentication error from CoC API
        mock_response = MagicMock()
        mock_response.status = 403

        with patch.object(bot, "get_player") as mock_get_player:
            # Simulate player not found due to auth error
            mock_get_player.return_value = None

            await bot.verify.callback(mock_interaction, "#PLAYER1")

            # Should show the generic "player not found" message - the re-auth happens transparently
            mock_interaction.followup.send.assert_called_once_with(
                "❌ Verification failed – player not found or CoC API unavailable.",
                ephemeral=True,
            )


class TestWhoisCommand:
    """Test the /whois command functionality."""

    @pytest.fixture
    def mock_interaction(self):
        """Create a mock Discord interaction."""
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    @pytest.fixture
    def mock_member(self):
        """Create a mock Discord member."""
        member = MagicMock(spec=discord.Member)
        member.id = 123456789
        member.display_name = "TestUser"
        return member

    @pytest.mark.asyncio
    async def test_whois_success(self, mock_interaction, mock_member):
        """Test successful whois lookup."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "discord_id": "123456789",
                "player_name": "TestPlayer",
                "player_tag": "#PLAYER1",
            }
        }

        with patch.object(bot, "table", mock_table):
            await bot.whois.callback(mock_interaction, mock_member)

            mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
            mock_interaction.followup.send.assert_called_once_with(
                "TestUser is TestPlayer", ephemeral=True
            )

    @pytest.mark.asyncio
    async def test_whois_no_table(self, mock_interaction, mock_member):
        """Test whois when database is not configured."""
        with patch.object(bot, "table", None):
            await bot.whois.callback(mock_interaction, mock_member)

            mock_interaction.followup.send.assert_called_once_with(
                "Database not configured.", ephemeral=True
            )

    @pytest.mark.asyncio
    async def test_whois_no_record(self, mock_interaction, mock_member):
        """Test whois when no record is found."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}

        with patch.object(bot, "table", mock_table):
            await bot.whois.callback(mock_interaction, mock_member)

            mock_interaction.followup.send.assert_called_once_with(
                "No record found.", ephemeral=True
            )

    @pytest.mark.asyncio
    async def test_whois_database_error(self, mock_interaction, mock_member):
        """Test whois handling database errors."""
        mock_table = MagicMock()
        mock_table.get_item.side_effect = Exception("Database error")

        with patch.object(bot, "table", mock_table):
            await bot.whois.callback(mock_interaction, mock_member)

            mock_interaction.followup.send.assert_called_once_with(
                "Lookup failed.", ephemeral=True
            )


class TestMembershipCheck:
    """Test the membership check background task."""

    @pytest.mark.asyncio
    async def test_membership_check_no_table(self):
        """Test membership check when table is None."""
        with patch.object(bot, "table", None):
            # Should not raise an exception
            await bot.membership_check()

    @pytest.mark.asyncio
    async def test_membership_check_no_guilds(self):
        """Test membership check when bot has no guilds."""
        with (
            patch.object(bot, "table", MagicMock()),
            patch.object(
                type(bot.bot), "guilds", new_callable=PropertyMock, return_value=[]
            ),
        ):
            # Should not raise an exception
            await bot.membership_check()

    @pytest.mark.asyncio
    async def test_membership_check_scan_error(self):
        """Test membership check handling scan errors."""
        mock_table = MagicMock()
        mock_table.scan.side_effect = Exception("Scan error")
        mock_guild = MagicMock()

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
        ):
            # Should not raise an exception
            await bot.membership_check()

    @pytest.mark.asyncio
    async def test_membership_check_member_left_clan(self):
        """Test membership check when member left clan."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "123456789",
                    "player_tag": "#PLAYER1",
                    "player_name": "TestPlayer",
                }
            ]
        }

        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_guild.get_member.return_value = mock_member

        fetch_result = SimpleNamespace(status="not_found", player=None, exception=None)

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
            patch.object(
                bot.coc_api,
                "fetch_player_with_status",
                new=AsyncMock(return_value=fetch_result),
            ),
        ):
            await bot.membership_check()

            # Verify deletion is NOT called (log-only mode)
            mock_table.delete_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_membership_check_skips_pending_removal_entries(self):
        """Test membership check skips PENDING_REMOVAL entries without crashing."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "123456789",  # Valid Discord ID
                    "player_tag": "#PLAYER1",
                    "player_name": "ValidPlayer",
                },
                {
                    "discord_id": "PENDING_REMOVAL_abc123",  # Should be skipped
                    "removal_id": "abc123",
                    "target_discord_id": "123456789",
                    "player_tag": "#PLAYER2",
                    "player_name": "PendingPlayer",
                    "reason": "Left clan",
                },
                {
                    "discord_id": "non_numeric_entry",  # Should be skipped
                    "some_field": "some_value",
                },
            ]
        }

        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_guild.get_member.return_value = mock_member

        fetch_results = {
            "#PLAYER1": SimpleNamespace(
                status="not_found", player=None, exception=None
            ),
            "#PLAYER2": SimpleNamespace(
                status="not_found", player=None, exception=None
            ),
        }

        async def mock_fetch_player_with_status(*args, **kwargs):
            # player_tag is the fourth positional argument
            player_tag = args[3]
            return fetch_results[player_tag]

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
            patch.object(
                bot.coc_api,
                "fetch_player_with_status",
                new=AsyncMock(side_effect=mock_fetch_player_with_status),
            ),
        ):
            # This should not raise ValueError anymore
            await bot.membership_check()

            # Verify only the valid Discord ID was processed
            mock_guild.get_member.assert_called_once_with(123456789)


class TestMainFunction:
    """Test the main function and environment validation."""

    def test_main_missing_env_vars(self):
        """Test main function raises error for missing environment variables."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="Missing env vars"):
                asyncio.run(bot.main())

    @pytest.mark.asyncio
    async def test_main_with_all_env_vars(self):
        """Test main function with all required environment variables."""
        env_vars = {
            "DISCORD_TOKEN": "fake_token",
            "COC_EMAIL": "fake_email",
            "COC_PASSWORD": "fake_password",
            "CLAN_TAG": "#TESTCLAN",
            "VERIFIED_ROLE_ID": "12345",
            "DDB_TABLE_NAME": "test_table",
        }

        with (
            patch.dict(os.environ, env_vars),
            patch.object(bot.bot, "start") as start_mock,
        ):
            # Mock the start method to avoid actually starting the bot
            start_mock.side_effect = KeyboardInterrupt()

            try:
                await bot.main()
            except KeyboardInterrupt:
                pass  # Expected for this test

            start_mock.assert_called_once_with("fake_token")


class TestBotEvents:
    """Test bot event handlers."""

    @pytest.mark.asyncio
    async def test_on_ready(self):
        """Test the on_ready event handler."""
        # Mock the bot.user property by patching the bot type
        mock_user = MagicMock()
        mock_user.id = 123456
        mock_user.__str__ = lambda: "TestBot"

        with (
            patch.object(bot.tree, "sync", new_callable=AsyncMock) as sync_mock,
            patch.object(bot, "coc_client") as coc_client_mock,
            patch.object(bot.membership_check, "start") as start_mock,
            patch.object(
                type(bot.bot), "user", new_callable=PropertyMock, return_value=mock_user
            ),
        ):
            coc_client_mock.login = AsyncMock()
            await bot.on_ready()

            sync_mock.assert_called_once()
            coc_client_mock.login.assert_called_once_with(
                bot.COC_EMAIL, bot.COC_PASSWORD
            )
            start_mock.assert_called_once()


class TestMemberRemovalView:
    """Test the member removal approval system."""

    @pytest.mark.asyncio
    async def test_member_removal_view_init(self):
        """Test MemberRemovalView initialization."""
        view = bot.MemberRemovalView(
            "removal123", "discord123", "#PLAYER123", "TestPlayer", "Left clan"
        )

        assert view.removal_id == "removal123"
        assert view.discord_id == "discord123"
        assert view.player_tag == "#PLAYER123"
        assert view.player_name == "TestPlayer"
        assert view.reason == "Left clan"
        assert view.timeout == 86400  # 24 hours

    @pytest.mark.asyncio
    async def test_store_pending_removal_success(self):
        """Test successful storage of pending removal in DynamoDB."""
        mock_table = MagicMock()
        view = bot.MemberRemovalView(
            "removal123", "discord123", "#PLAYER123", "TestPlayer", "Left clan"
        )

        with patch.object(bot, "table", mock_table):
            await view.store_pending_removal()

            mock_table.put_item.assert_called_once()
            call_args = mock_table.put_item.call_args[1]["Item"]
            assert call_args["discord_id"] == "PENDING_REMOVAL_removal123"
            assert call_args["removal_id"] == "removal123"
            assert call_args["target_discord_id"] == "discord123"
            assert call_args["player_tag"] == "#PLAYER123"
            assert call_args["player_name"] == "TestPlayer"
            assert call_args["reason"] == "Left clan"
            assert call_args["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_store_pending_removal_no_table(self):
        """Test store_pending_removal when table is None."""
        view = bot.MemberRemovalView(
            "removal123", "discord123", "#PLAYER123", "TestPlayer", "Left clan"
        )

        with patch.object(bot, "table", None):
            await view.store_pending_removal()
            # Should not raise an exception

    @pytest.mark.asyncio
    async def test_store_pending_removal_exception(self):
        """Test store_pending_removal handling DynamoDB exceptions."""
        mock_table = MagicMock()
        mock_table.put_item.side_effect = Exception("DynamoDB error")
        view = bot.MemberRemovalView(
            "removal123", "discord123", "#PLAYER123", "TestPlayer", "Left clan"
        )

        with patch.object(bot, "table", mock_table):
            await view.store_pending_removal()
            mock_table.put_item.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_pending_removal_success(self):
        """Test successful removal of pending removal from DynamoDB."""
        mock_table = MagicMock()
        view = bot.MemberRemovalView(
            "removal123", "discord123", "#PLAYER123", "TestPlayer", "Left clan"
        )

        with patch.object(bot, "table", mock_table):
            await view.remove_pending_removal()

            mock_table.delete_item.assert_called_once_with(
                Key={"discord_id": "PENDING_REMOVAL_removal123"}
            )

    @pytest.mark.asyncio
    async def test_remove_pending_removal_no_table(self):
        """Test remove_pending_removal when table is None."""
        view = bot.MemberRemovalView(
            "removal123", "discord123", "#PLAYER123", "TestPlayer", "Left clan"
        )

        with patch.object(bot, "table", None):
            await view.remove_pending_removal()
            # Should not raise an exception

    @pytest.mark.asyncio
    async def test_remove_pending_removal_exception(self):
        """Test remove_pending_removal handling DynamoDB exceptions."""
        mock_table = MagicMock()
        mock_table.delete_item.side_effect = Exception("DynamoDB error")
        view = bot.MemberRemovalView(
            "removal123", "discord123", "#PLAYER123", "TestPlayer", "Left clan"
        )

        with patch.object(bot, "table", mock_table):
            await view.remove_pending_removal()
            mock_table.delete_item.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_removal_success(self):
        """Test successful approval and member removal."""
        mock_table = MagicMock()
        mock_interaction = AsyncMock()
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_user = MagicMock()
        mock_user.name = "admin"
        mock_user.mention = "@admin"

        mock_interaction.guild = mock_guild
        mock_interaction.user = mock_user
        mock_guild.get_member.return_value = mock_member
        mock_member.mention = "@testuser"
        mock_member.kick = AsyncMock()

        # Mock message editing
        mock_embed = MagicMock()
        mock_embed.add_field = MagicMock()
        mock_message = MagicMock()
        mock_message.embeds = [mock_embed]
        mock_message.edit = AsyncMock()
        mock_interaction.message = mock_message

        view = bot.MemberRemovalView(
            "removal123", "987654321", "#PLAYER123", "TestPlayer", "Left clan"
        )

        mock_button = MagicMock()

        with patch.object(bot, "table", mock_table):
            # Call the original method bypassing the button decorator
            from bot import MemberRemovalView

            # Get the original unbound method
            approve_func = MemberRemovalView.__dict__["approve_removal"]
            # Call it with view as self
            await approve_func(view, mock_interaction, mock_button)

            # Verify member was kicked
            mock_member.kick.assert_called_once()

            # Verify database operations
            mock_table.delete_item.assert_any_call(Key={"discord_id": "987654321"})
            mock_table.delete_item.assert_any_call(
                Key={"discord_id": "PENDING_REMOVAL_removal123"}
            )

            # Verify interaction response
            mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
            mock_interaction.followup.send.assert_called_once()

            # Verify embed was updated
            mock_embed.add_field.assert_called()

    @pytest.mark.asyncio
    async def test_approve_removal_member_not_found(self):
        """Test approval when member is not found in server."""
        mock_table = MagicMock()
        mock_interaction = AsyncMock()
        mock_guild = MagicMock()
        mock_user = MagicMock()
        mock_user.mention = "@admin"

        mock_interaction.guild = mock_guild
        mock_interaction.user = mock_user
        mock_guild.get_member.return_value = None  # Member not found

        # Mock message editing
        mock_embed = MagicMock()
        mock_message = MagicMock()
        mock_message.embeds = [mock_embed]
        mock_message.edit = AsyncMock()
        mock_interaction.message = mock_message

        view = bot.MemberRemovalView(
            "removal123", "987654321", "#PLAYER123", "TestPlayer", "Left clan"
        )

        mock_button = MagicMock()

        with patch.object(bot, "table", mock_table):
            from bot import MemberRemovalView

            approve_func = MemberRemovalView.__dict__["approve_removal"]
            await approve_func(view, mock_interaction, mock_button)

            # Verify pending removal was cleaned up
            mock_table.delete_item.assert_called_once_with(
                Key={"discord_id": "PENDING_REMOVAL_removal123"}
            )

            # Verify appropriate response was sent
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args[0][0]
            assert "❌ Member 987654321 not found" in call_args

    @pytest.mark.asyncio
    async def test_approve_removal_no_guild(self):
        """Test approval when guild is None."""
        mock_interaction = AsyncMock()
        mock_interaction.guild = None

        view = bot.MemberRemovalView(
            "removal123", "987654321", "#PLAYER123", "TestPlayer", "Left clan"
        )

        mock_button = MagicMock()
        from bot import MemberRemovalView

        approve_func = MemberRemovalView.__dict__["approve_removal"]
        await approve_func(view, mock_interaction, mock_button)

        mock_interaction.followup.send.assert_called_once_with(
            "Error: Guild not found.", ephemeral=True
        )

    @pytest.mark.asyncio
    async def test_approve_removal_kick_forbidden(self):
        """Test approval when bot lacks permission to kick member."""
        mock_table = MagicMock()
        mock_interaction = AsyncMock()
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_user = MagicMock()
        mock_user.name = "admin"
        mock_user.mention = "@admin"

        mock_interaction.guild = mock_guild
        mock_interaction.user = mock_user
        mock_guild.get_member.return_value = mock_member
        mock_member.mention = "@testuser"
        mock_member.kick = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "Forbidden")
        )

        # Mock message editing
        mock_embed = MagicMock()
        mock_message = MagicMock()
        mock_message.embeds = [mock_embed]
        mock_message.edit = AsyncMock()
        mock_interaction.message = mock_message

        view = bot.MemberRemovalView(
            "removal123", "987654321", "#PLAYER123", "TestPlayer", "Left clan"
        )

        mock_button = MagicMock()

        with patch.object(bot, "table", mock_table):
            from bot import MemberRemovalView

            approve_func = MemberRemovalView.__dict__["approve_removal"]
            await approve_func(view, mock_interaction, mock_button)

            # Verify kick was attempted but failed
            mock_member.kick.assert_called_once()

            # Verify database record was still deleted
            mock_table.delete_item.assert_any_call(Key={"discord_id": "987654321"})

            # Verify response mentions kick failure
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args[0][0]
            assert "⚠️ Could not kick member" in call_args

    @pytest.mark.asyncio
    async def test_deny_removal_success(self):
        """Test successful denial of member removal."""
        mock_table = MagicMock()
        mock_interaction = AsyncMock()
        mock_user = MagicMock()
        mock_user.mention = "@admin"

        mock_interaction.user = mock_user

        # Mock message editing
        mock_embed = MagicMock()
        mock_message = MagicMock()
        mock_message.embeds = [mock_embed]
        mock_message.edit = AsyncMock()
        mock_interaction.message = mock_message

        view = bot.MemberRemovalView(
            "removal123", "987654321", "#PLAYER123", "TestPlayer", "Left clan"
        )

        mock_button = MagicMock()

        with patch.object(bot, "table", mock_table):
            from bot import MemberRemovalView

            deny_func = MemberRemovalView.__dict__["deny_removal"]
            await deny_func(view, mock_interaction, mock_button)

            # Verify pending removal was removed
            mock_table.delete_item.assert_called_once_with(
                Key={"discord_id": "PENDING_REMOVAL_removal123"}
            )

            # Verify interaction response
            mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args[0][0]
            assert "**Denied removal of TestPlayer**" in call_args

    @pytest.mark.asyncio
    async def test_on_timeout(self):
        """Test view timeout handling."""
        mock_table = MagicMock()
        view = bot.MemberRemovalView(
            "removal123", "987654321", "#PLAYER123", "TestPlayer", "Left clan"
        )

        with patch.object(bot, "table", mock_table):
            await view.on_timeout()

            # Verify pending removal was cleaned up on timeout
            mock_table.delete_item.assert_called_once_with(
                Key={"discord_id": "PENDING_REMOVAL_removal123"}
            )


class TestSendRemovalApprovalRequest:
    """Test the send_removal_approval_request function."""

    @pytest.mark.asyncio
    async def test_send_removal_approval_request_success(self):
        """Test successful sending of removal approval request."""
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_member.id = 123456789
        mock_member.display_name = "TestUser"
        mock_member.mention = "@TestUser"
        mock_log_channel = AsyncMock()

        with (
            patch.object(bot, "resolve_log_channel", return_value=mock_log_channel),
            patch("uuid.uuid4") as mock_uuid,
        ):
            mock_uuid.return_value.hex = "abcdef123456789"

            await bot.send_removal_approval_request(
                mock_guild, mock_member, "#PLAYER123", "TestPlayer", "Left clan"
            )

            # Verify log channel resolution was called
            bot.resolve_log_channel.assert_called_once_with(mock_guild)

            # Verify message was sent with embed and view
            mock_log_channel.send.assert_called_once()
            call_kwargs = mock_log_channel.send.call_args[1]
            assert "embed" in call_kwargs
            assert "view" in call_kwargs

            # Verify embed content
            embed = call_kwargs["embed"]
            assert embed.title == "🚨 Member Removal Request"
            assert "TestUser" in embed.description
            assert "@TestUser" in embed.description

    @pytest.mark.asyncio
    async def test_send_removal_approval_request_no_log_channel(self):
        """Test send_removal_approval_request when no log channel is configured."""
        mock_guild = MagicMock()
        mock_member = MagicMock()

        with patch.object(bot, "resolve_log_channel", return_value=None):
            await bot.send_removal_approval_request(
                mock_guild, mock_member, "#PLAYER123", "TestPlayer", "Left clan"
            )

            # Should complete without error, just log a warning

    @pytest.mark.asyncio
    async def test_send_removal_approval_request_send_forbidden(self):
        """Test send_removal_approval_request when bot lacks send permission."""
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_member.id = 123456789
        mock_member.display_name = "TestUser"
        mock_member.mention = "@TestUser"
        mock_log_channel = AsyncMock()
        mock_log_channel.send.side_effect = discord.Forbidden(MagicMock(), "Forbidden")
        mock_log_channel.id = 67890

        with (
            patch.object(bot, "resolve_log_channel", return_value=mock_log_channel),
            patch("uuid.uuid4") as mock_uuid,
        ):
            mock_uuid.return_value.hex = "abcdef123456789"

            await bot.send_removal_approval_request(
                mock_guild, mock_member, "#PLAYER123", "TestPlayer", "Left clan"
            )

            # Should handle the exception gracefully
            mock_log_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_removal_approval_request_http_exception(self):
        """Test send_removal_approval_request with HTTP exception."""
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_member.id = 123456789
        mock_member.display_name = "TestUser"
        mock_member.mention = "@TestUser"
        mock_log_channel = AsyncMock()
        mock_log_channel.send.side_effect = discord.HTTPException(
            MagicMock(), "HTTP Error"
        )

        with (
            patch.object(bot, "resolve_log_channel", return_value=mock_log_channel),
            patch("uuid.uuid4") as mock_uuid,
        ):
            mock_uuid.return_value.hex = "abcdef123456789"

            await bot.send_removal_approval_request(
                mock_guild, mock_member, "#PLAYER123", "TestPlayer", "Left clan"
            )

            # Should handle the exception gracefully
            mock_log_channel.send.assert_called_once()


class TestCleanupExpiredPendingRemovals:
    """Test the cleanup_expired_pending_removals function."""

    @pytest.mark.asyncio
    async def test_cleanup_expired_pending_removals_no_table(self):
        """Test cleanup when table is None."""
        with patch.object(bot, "table", None):
            await bot.cleanup_expired_pending_removals()
            # Should complete without error

    @pytest.mark.asyncio
    async def test_cleanup_expired_pending_removals_no_expired(self):
        """Test cleanup when there are no expired requests."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": []}

        with patch.object(bot, "table", mock_table):
            await bot.cleanup_expired_pending_removals()

            mock_table.scan.assert_called_once()
            mock_table.delete_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_expired_pending_removals_with_expired(self):
        """Test cleanup when there are expired requests."""
        from datetime import UTC, datetime, timedelta

        # Create a timestamp from 25 hours ago (expired)
        expired_time = datetime.now(UTC) - timedelta(hours=25)
        recent_time = datetime.now(UTC) - timedelta(minutes=30)

        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "PENDING_REMOVAL_expired1",
                    "removal_id": "expired1",
                    "timestamp": expired_time.isoformat(),
                },
                {
                    "discord_id": "PENDING_REMOVAL_recent1",
                    "removal_id": "recent1",
                    "timestamp": recent_time.isoformat(),
                },
                {
                    "discord_id": "regular_user_123",  # Not a pending removal
                    "player_tag": "#PLAYER123",
                },
            ]
        }

        with patch.object(bot, "table", mock_table):
            await bot.cleanup_expired_pending_removals()

            mock_table.scan.assert_called_once()
            # Should only delete the expired item
            mock_table.delete_item.assert_called_once_with(
                Key={"discord_id": "PENDING_REMOVAL_expired1"}
            )

    @pytest.mark.asyncio
    async def test_cleanup_expired_pending_removals_invalid_timestamp(self):
        """Test cleanup handling invalid timestamps."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "PENDING_REMOVAL_invalid1",
                    "removal_id": "invalid1",
                    "timestamp": "invalid-timestamp",
                },
                {
                    "discord_id": "PENDING_REMOVAL_invalid2",
                    "removal_id": "invalid2",
                    "timestamp": None,
                },
            ]
        }

        with patch.object(bot, "table", mock_table):
            await bot.cleanup_expired_pending_removals()

            mock_table.scan.assert_called_once()
            # Should not delete anything due to invalid timestamps
            mock_table.delete_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_expired_pending_removals_scan_exception(self):
        """Test cleanup handling scan exceptions."""
        mock_table = MagicMock()
        mock_table.scan.side_effect = Exception("DynamoDB scan error")

        with patch.object(bot, "table", mock_table):
            await bot.cleanup_expired_pending_removals()

            # Should handle the exception gracefully
            mock_table.scan.assert_called_once()


class TestMembershipCheckWithApprovalSystem:
    """Test membership check integration with approval system."""

    @pytest.mark.asyncio
    async def test_membership_check_sends_approval_request(self):
        """Test membership check sends approval request for members who left clan."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "123456789",
                    "player_tag": "#PLAYER1",
                    "player_name": "TestPlayer",
                    "clan_tag": "#TESTCLAN",
                }
            ]
        }

        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_member.id = 123456789
        mock_guild.get_member.return_value = mock_member

        fetch_result = SimpleNamespace(status="not_found", player=None, exception=None)

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
            patch.object(
                bot.coc_api,
                "fetch_player_with_status",
                new=AsyncMock(return_value=fetch_result),
            ),
            patch.object(
                bot,
                "has_pending_removal",
                new=AsyncMock(return_value=False),
            ),
            patch.object(bot, "send_removal_approval_request") as mock_send_approval,
        ):
            await bot.membership_check()

            # Verify approval request was sent
            mock_send_approval.assert_called_once_with(
                mock_guild,
                mock_member,
                "#PLAYER1",
                "TestPlayer",
                "Player TestPlayer (#PLAYER1) is no longer in any clan",
            )

    @pytest.mark.asyncio
    async def test_membership_check_approval_request_exception(self):
        """Test membership check handles exceptions when sending approval request."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "123456789",
                    "player_tag": "#PLAYER1",
                    "player_name": "TestPlayer",
                }
            ]
        }

        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_guild.get_member.return_value = mock_member

        fetch_result = SimpleNamespace(status="not_found", player=None, exception=None)

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
            patch.object(
                bot.coc_api,
                "fetch_player_with_status",
                new=AsyncMock(return_value=fetch_result),
            ),
            patch.object(
                bot,
                "has_pending_removal",
                new=AsyncMock(return_value=False),
            ),
            patch.object(bot, "send_removal_approval_request") as mock_send_approval,
        ):
            mock_send_approval.side_effect = Exception("Network error")

            await bot.membership_check()

            # Should handle the exception gracefully
            mock_send_approval.assert_called_once()


class TestOnReadyWithCleanup:
    """Test on_ready event with cleanup integration."""

    @pytest.mark.asyncio
    async def test_on_ready_calls_cleanup(self):
        """Test that on_ready calls cleanup_expired_pending_removals."""
        mock_user = MagicMock()
        mock_user.id = 123456
        mock_user.__str__ = lambda: "TestBot"

        with (
            patch.object(bot.tree, "sync", new_callable=AsyncMock) as sync_mock,
            patch.object(bot, "coc_client") as coc_client_mock,
            patch.object(bot, "cleanup_expired_pending_removals") as cleanup_mock,
            patch.object(bot.membership_check, "start") as start_mock,
            patch.object(
                type(bot.bot), "user", new_callable=PropertyMock, return_value=mock_user
            ),
        ):
            coc_client_mock.login = AsyncMock()
            cleanup_mock.return_value = None

            await bot.on_ready()

            sync_mock.assert_called_once()
            coc_client_mock.login.assert_called_once()
            cleanup_mock.assert_called_once()
            start_mock.assert_called_once()


class TestHasPendingRemoval:
    """Test the has_pending_removal function for preventing duplicate requests."""

    @pytest.mark.asyncio
    async def test_has_pending_removal_no_table(self):
        """Test has_pending_removal when table is None."""
        with patch.object(bot, "table", None):
            result = await bot.has_pending_removal("123456789")
            assert result is False

    @pytest.mark.asyncio
    async def test_has_pending_removal_exists(self):
        """Test has_pending_removal when a pending removal exists."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "PENDING_REMOVAL_abc123",
                    "target_discord_id": "123456789",
                    "removal_id": "abc123",
                }
            ]
        }

        with patch.object(bot, "table", mock_table):
            result = await bot.has_pending_removal("123456789")
            assert result is True
            mock_table.scan.assert_called_once()

    @pytest.mark.asyncio
    async def test_has_pending_removal_not_exists(self):
        """Test has_pending_removal when no pending removal exists."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": []  # No matching items
        }

        with patch.object(bot, "table", mock_table):
            result = await bot.has_pending_removal("123456789")
            assert result is False
            mock_table.scan.assert_called_once()

    @pytest.mark.asyncio
    async def test_has_pending_removal_scan_exception(self):
        """Test has_pending_removal handling scan exceptions."""
        mock_table = MagicMock()
        mock_table.scan.side_effect = Exception("DynamoDB scan error")

        with patch.object(bot, "table", mock_table):
            result = await bot.has_pending_removal("123456789")
            assert result is False  # Should return False on error to not block requests
            mock_table.scan.assert_called_once()


class TestMembershipCheckDuplicatePrevention:
    """Test that membership_check prevents duplicate removal requests."""

    @pytest.mark.asyncio
    async def test_membership_check_skips_existing_pending_removal(self):
        """Test that membership_check skips sending request if one already exists."""
        mock_table = MagicMock()
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_member.id = 123456789

        # Mock table scan to return a member who left clan
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "123456789",
                    "player_tag": "#PLAYER123",
                    "player_name": "TestPlayer",
                    "clan_tag": "#CLAN123",
                }
            ]
        }

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
            patch.object(
                bot.coc_api,
                "fetch_player_with_status",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        status="not_found", player=None, exception=None
                    )
                ),
            ),
            patch.object(
                bot,
                "has_pending_removal",
                new=AsyncMock(return_value=True)
            ),  # Already has pending request
            patch.object(bot, "send_removal_approval_request") as mock_send_request,
        ):
            mock_guild.get_member.return_value = mock_member

            # Call the membership check
            await bot.membership_check()

            # Verify that send_removal_approval_request was NOT called
            mock_send_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_membership_check_sends_request_when_no_pending(self):
        """Test that membership_check sends request when no pending removal exists."""
        mock_table = MagicMock()
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_member.id = 123456789

        # Mock table scan to return a member who left clan
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "123456789",
                    "player_tag": "#PLAYER123",
                    "player_name": "TestPlayer",
                    "clan_tag": "#CLAN123",
                }
            ]
        }

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
            patch.object(
                bot.coc_api,
                "fetch_player_with_status",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        status="not_found", player=None, exception=None
                    )
                ),
            ),
            patch.object(
                bot,
                "has_pending_removal",
                new=AsyncMock(return_value=False)
            ),  # No pending request
            patch.object(bot, "send_removal_approval_request") as mock_send_request,
        ):
            mock_guild.get_member.return_value = mock_member

            # Call the membership check
            await bot.membership_check()

            # Verify that send_removal_approval_request WAS called
            mock_send_request.assert_called_once_with(
                mock_guild,
                mock_member,
                "#PLAYER123",
                "TestPlayer",
                "Player TestPlayer (#PLAYER123) is no longer in any clan",
            )


    @pytest.mark.asyncio
    async def test_membership_check_clears_stale_pending_removal(self):
        """Ensure stale pending removals are cleared when member remains in clan."""
        mock_table = MagicMock()
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_member.id = 123456789

        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "123456789",
                    "player_tag": "#PLAYER123",
                    "player_name": "TestPlayer",
                    "clan_tag": bot.CLAN_TAG,
                }
            ]
        }

        player = MagicMock()
        player.clan = MagicMock()
        player.clan.tag = bot.CLAN_TAG

        fetch_result = SimpleNamespace(status="ok", player=player, exception=None)

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
            patch.object(
                bot.coc_api,
                "fetch_player_with_status",
                new=AsyncMock(return_value=fetch_result),
            ),
            patch.object(
                bot,
                "has_pending_removal",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                approvals,
                "clear_pending_removals_for_target",
                new=AsyncMock(return_value=1),
            ) as mock_clear_pending,
            patch.object(bot, "send_removal_approval_request") as mock_send_request,
        ):
            mock_guild.get_member.return_value = mock_member

            await bot.membership_check()

            mock_clear_pending.assert_awaited_once_with(mock_table, "123456789")
            mock_send_request.assert_not_called()


class TestMembershipCheckAuthFailures:
    """Test membership check handling of Clash API authentication failures."""

    @pytest.mark.asyncio
    async def test_membership_check_skips_on_access_denied(self):
        """Ensure membership check skips removals and clears invalid pending requests."""

        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "123456789",
                    "player_tag": "#PLAYER123",
                    "player_name": "TestPlayer",
                    "clan_tag": "#CLAN123",
                }
            ]
        }

        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_member.id = 123456789
        mock_guild.get_member.return_value = mock_member

        fetch_result = SimpleNamespace(
            status="access_denied", player=None, exception=None
        )

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
            patch.object(
                bot.coc_api,
                "fetch_player_with_status",
                new=AsyncMock(return_value=fetch_result),
            ),
            patch.object(
                bot,
                "has_pending_removal",
                new=AsyncMock(return_value=False),
            ) as mock_has_pending,
            patch.object(bot, "send_removal_approval_request") as mock_send_request,
            patch(
                "verifier_bot.approvals.clear_pending_removals_for_target",
                new=AsyncMock(return_value=1),
                create=True,
            ) as mock_clear_pending,
        ):
            await bot.membership_check()

            mock_send_request.assert_not_called()
            mock_clear_pending.assert_awaited_once_with(mock_table, "123456789")
            mock_has_pending.assert_not_called()


class TestPendingRemovalCleanup:
    """Test pending removal cleanup utilities."""

    @pytest.mark.asyncio
    async def test_clear_pending_removals_for_target(self):
        """Ensure pending removal entries are removed for a target user."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "discord_id": "PENDING_REMOVAL_123",
                    "target_discord_id": "123456789",
                    "removal_id": "123",
                },
                {
                    "discord_id": "PENDING_REMOVAL_456",
                    "target_discord_id": "123456789",
                    "removal_id": "456",
                },
            ]
        }

        deleted_count = await approvals.clear_pending_removals_for_target(
            mock_table, "123456789"
        )

        assert deleted_count == 2
        mock_table.scan.assert_called_once()
        mock_table.delete_item.assert_any_call(
            Key={"discord_id": "PENDING_REMOVAL_123"}
        )
        mock_table.delete_item.assert_any_call(
            Key={"discord_id": "PENDING_REMOVAL_456"}
        )


class TestTimestampFix:
    """Test that timestamp field becomes static after approval/denial actions."""

    @pytest.mark.asyncio
    async def test_timestamp_field_updated_to_static_on_approval(self):
        """Test that timestamp field becomes static when approval happens."""
        mock_table = MagicMock()
        mock_interaction = AsyncMock()
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_user = MagicMock()
        mock_user.name = "admin"
        mock_user.mention = "@admin"

        mock_interaction.guild = mock_guild
        mock_interaction.user = mock_user
        mock_guild.get_member.return_value = mock_member
        mock_member.mention = "@testuser"
        mock_member.kick = AsyncMock()

        # Mock the embed with fields including Requested field
        mock_embed = MagicMock()
        mock_field_requested = MagicMock()
        mock_field_requested.name = "Requested"
        mock_field_requested.inline = True
        mock_field_other = MagicMock()
        mock_field_other.name = "Other Field"
        mock_embed.fields = [mock_field_other, mock_field_requested]

        mock_message = MagicMock()
        mock_message.embeds = [mock_embed]
        mock_message.edit = AsyncMock()
        mock_interaction.message = mock_message

        view = bot.MemberRemovalView(
            "removal123", "987654321", "#PLAYER123", "TestPlayer", "Left clan"
        )

        mock_button = MagicMock()

        with patch.object(bot, "table", mock_table):
            from bot import MemberRemovalView

            approve_func = MemberRemovalView.__dict__["approve_removal"]
            await approve_func(view, mock_interaction, mock_button)

            # Verify embed.set_field_at was called to update the timestamp field
            mock_embed.set_field_at.assert_called()
            # Check that it was called with index 1 (the Requested field)
            call_args = mock_embed.set_field_at.call_args
            assert call_args[0][0] == 1  # Index of Requested field
            assert call_args[1]["name"] == "Requested"
            # Verify the value uses static format (contains 'F' timestamp format)
            assert ":F>" in call_args[1]["value"]

    @pytest.mark.asyncio
    async def test_timestamp_field_updated_to_static_on_denial(self):
        """Test that timestamp field becomes static when denial happens."""
        mock_table = MagicMock()
        mock_interaction = AsyncMock()
        mock_user = MagicMock()
        mock_user.mention = "@admin"

        mock_interaction.user = mock_user

        # Mock the embed with fields including Requested field
        mock_embed = MagicMock()
        mock_field_requested = MagicMock()
        mock_field_requested.name = "Requested"
        mock_field_requested.inline = True
        mock_embed.fields = [mock_field_requested]

        mock_message = MagicMock()
        mock_message.embeds = [mock_embed]
        mock_message.edit = AsyncMock()
        mock_interaction.message = mock_message

        view = bot.MemberRemovalView(
            "removal123", "987654321", "#PLAYER123", "TestPlayer", "Left clan"
        )

        mock_button = MagicMock()

        with patch.object(bot, "table", mock_table):
            from bot import MemberRemovalView

            deny_func = MemberRemovalView.__dict__["deny_removal"]
            await deny_func(view, mock_interaction, mock_button)

            # Verify embed.set_field_at was called to update the timestamp field
            mock_embed.set_field_at.assert_called()
            call_args = mock_embed.set_field_at.call_args
            assert call_args[0][0] == 0  # Index of Requested field
            assert call_args[1]["name"] == "Requested"
            # Verify the value uses static format (contains 'F' timestamp format)
            assert ":F>" in call_args[1]["value"]

    @pytest.mark.asyncio
    async def test_update_timestamp_field_to_static_helper(self):
        """Test the _update_timestamp_field_to_static helper method."""
        view = bot.MemberRemovalView(
            "removal123", "987654321", "#PLAYER123", "TestPlayer", "Left clan"
        )

        # Mock embed with Requested field
        mock_embed = MagicMock()
        mock_field1 = MagicMock()
        mock_field1.name = "Discord User"
        mock_field1.inline = True
        mock_field2 = MagicMock()
        mock_field2.name = "Requested"
        mock_field2.inline = True
        mock_embed.fields = [mock_field1, mock_field2]

        # Call the helper method
        view._update_timestamp_field_to_static(mock_embed)

        # Verify set_field_at was called for the Requested field
        mock_embed.set_field_at.assert_called_once_with(
            1,  # Index of Requested field
            name="Requested",
            value=ANY,  # We'll check the format separately
            inline=True,
        )

        # Verify the timestamp format is static (contains :F>)
        call_args = mock_embed.set_field_at.call_args
        timestamp_value = call_args[1]["value"]
        assert ":F>" in timestamp_value
