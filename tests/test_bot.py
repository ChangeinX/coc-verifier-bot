"""Tests for the verification bot (bot.py)."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

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
        guild.get_channel.return_value = None

        with (
            patch.object(bot, "ADMIN_LOG_CHANNEL_ID", 67890),
            patch.object(bot.bot, "get_channel", return_value=text_channel),
        ):
            result = await bot.resolve_log_channel(guild)
            assert result == text_channel

    @pytest.mark.asyncio
    async def test_resolve_log_channel_fetch_from_api(self):
        """Test resolve_log_channel fetching channel from Discord API."""
        guild = MagicMock()
        text_channel = MagicMock(spec=discord.TextChannel)
        guild.get_channel.return_value = None

        with (
            patch.object(bot, "ADMIN_LOG_CHANNEL_ID", 67890),
            patch.object(bot.bot, "get_channel", return_value=None),
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
            patch.object(bot.bot, "get_channel", return_value=None),
            patch.object(
                bot.bot,
                "fetch_channel",
                side_effect=discord.HTTPException(mock_response, "Channel not found"),
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
        guild.get_channel.return_value = voice_channel

        with patch.object(bot, "ADMIN_LOG_CHANNEL_ID", 67890):
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
                "❌ Verification failed – you are not listed in any of our clans.",
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

        with (
            patch.object(bot, "table", mock_table),
            patch.object(
                type(bot.bot),
                "guilds",
                new_callable=PropertyMock,
                return_value=[mock_guild],
            ),
            patch.object(bot, "get_player", return_value=None),
        ):
            await bot.membership_check()

            # Verify deletion is NOT called (log-only mode)
            mock_table.delete_item.assert_not_called()


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
