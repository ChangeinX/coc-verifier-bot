"""Tests for the unified bot module."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bots.unified import EnvironmentConfig, UnifiedRuntime, main


class TestEnvironmentConfig:
    """Test environment configuration loading."""

    def test_load_with_all_required_variables(self):
        """Should load configuration when all required variables are set."""
        env_vars = {
            "DISCORD_TOKEN": "test_token",
            "COC_EMAIL": "test@example.com",
            "COC_PASSWORD": "test_password",
            "CLAN_TAG": "#TEST123",
            "VERIFIED_ROLE_ID": "123456789",
            "GIVEAWAY_CHANNEL_ID": "987654321",
            "GIVEAWAY_TABLE_NAME": "test_giveaways",
            "TOURNAMENT_TABLE_NAME": "test_tournaments",
            "DDB_TABLE_NAME": "test_verifications",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = EnvironmentConfig.load()

            assert config.discord_token == "test_token"
            assert config.coc_email == "test@example.com"
            assert config.coc_password == "test_password"
            assert config.clan_tag == "#TEST123"
            assert config.verified_role_id == "123456789"
            assert config.giveaway_channel_id == "987654321"
            assert config.giveaway_table_name == "test_giveaways"
            assert config.tournament_table_name == "test_tournaments"
            assert config.verification_table_name == "test_verifications"

    def test_load_with_optional_variables(self):
        """Should load configuration with optional variables."""
        env_vars = {
            "DISCORD_TOKEN": "test_token",
            "COC_EMAIL": "test@example.com",
            "COC_PASSWORD": "test_password",
            "CLAN_TAG": "#TEST123",
            "VERIFIED_ROLE_ID": "123456789",
            "GIVEAWAY_CHANNEL_ID": "987654321",
            "GIVEAWAY_TABLE_NAME": "test_giveaways",
            "TOURNAMENT_TABLE_NAME": "test_tournaments",
            "DDB_TABLE_NAME": "test_verifications",
            "FEEDER_CLAN_TAG": "#FEEDER123",
            "ADMIN_LOG_CHANNEL_ID": "111222333",
            "GIVEAWAY_TEST": "true",
            "TOURNAMENT_REGISTRATION_CHANNEL_ID": "444555666",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = EnvironmentConfig.load()

            assert config.feeder_clan_tag == "#FEEDER123"
            assert config.admin_log_channel_id == "111222333"
            assert config.giveaway_test_mode is True
            assert config.tournament_registration_channel_id == "444555666"

    def test_load_missing_required_variables(self):
        """Should raise RuntimeError when required variables are missing."""
        env_vars = {
            "DISCORD_TOKEN": "test_token",
            # Missing other required variables
        }

        with patch.dict(os.environ, env_vars, clear=True):
            with pytest.raises(RuntimeError, match="Missing env vars"):
                EnvironmentConfig.load()

    def test_giveaway_test_mode_parsing(self):
        """Should parse giveaway test mode correctly."""
        test_cases = [
            ("true", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("0", False),
            ("no", False),
            ("", False),
        ]

        base_env = {
            "DISCORD_TOKEN": "test_token",
            "COC_EMAIL": "test@example.com",
            "COC_PASSWORD": "test_password",
            "CLAN_TAG": "#TEST123",
            "VERIFIED_ROLE_ID": "123456789",
            "GIVEAWAY_CHANNEL_ID": "987654321",
            "GIVEAWAY_TABLE_NAME": "test_giveaways",
            "TOURNAMENT_TABLE_NAME": "test_tournaments",
            "DDB_TABLE_NAME": "test_verifications",
        }

        for test_value, expected in test_cases:
            env_vars = {**base_env, "GIVEAWAY_TEST": test_value}
            with patch.dict(os.environ, env_vars, clear=True):
                config = EnvironmentConfig.load()
                assert config.giveaway_test_mode is expected


class TestUnifiedRuntime:
    """Test unified runtime functionality."""

    @patch("bots.unified.boto3.resource")
    @patch("bots.unified.read_shadow_config")
    def test_init(self, mock_read_shadow_config, mock_boto3_resource):
        """Should initialize runtime with proper dependencies."""
        mock_read_shadow_config.return_value = MagicMock(enabled=True, channel_id=123)
        mock_boto3_resource.return_value = MagicMock()

        config = EnvironmentConfig(
            discord_token="test_token",
            coc_email="test@example.com",
            coc_password="test_password",
            clan_tag="#TEST123",
            feeder_clan_tag=None,
            verified_role_id="123456789",
            admin_log_channel_id=None,
            giveaway_channel_id="987654321",
            giveaway_table_name="test_giveaways",
            giveaway_test_mode=False,
            tournament_table_name="test_tournaments",
            tournament_registration_channel_id=None,
            verification_table_name="test_verifications",
        )

        runtime = UnifiedRuntime(config)

        assert runtime.config == config
        assert runtime.bot is not None
        assert runtime.tree is not None
        assert runtime.shadow_config is not None
        assert runtime.shadow_reporter is not None
        assert runtime.dynamodb is not None
        assert runtime.coc_client is None

    @patch("bots.unified.verification.configure_runtime")
    @patch("bots.unified.giveaway.configure_runtime")
    @patch("bots.unified.tournament.configure_runtime")
    @patch("bots.unified.boto3.resource")
    @patch("bots.unified.read_shadow_config")
    def test_configure_features(
        self,
        mock_read_shadow_config,
        mock_boto3_resource,
        mock_tournament_config,
        mock_giveaway_config,
        mock_verification_config,
    ):
        """Should configure all bot features."""
        mock_read_shadow_config.return_value = MagicMock(enabled=False, channel_id=None)
        mock_boto3_resource.return_value = MagicMock()

        config = EnvironmentConfig(
            discord_token="test_token",
            coc_email="test@example.com",
            coc_password="test_password",
            clan_tag="#TEST123",
            feeder_clan_tag=None,
            verified_role_id="123456789",
            admin_log_channel_id=None,
            giveaway_channel_id="987654321",
            giveaway_table_name="test_giveaways",
            giveaway_test_mode=False,
            tournament_table_name="test_tournaments",
            tournament_registration_channel_id=None,
            verification_table_name="test_verifications",
        )

        runtime = UnifiedRuntime(config)
        runtime.configure_features()

        # Verify all modules were configured
        mock_verification_config.assert_called_once()
        mock_giveaway_config.assert_called_once()
        mock_tournament_config.assert_called_once()

    @patch("bots.unified.EnvironmentConfig.load")
    @patch("bots.unified.boto3.resource")
    @patch("bots.unified.read_shadow_config")
    def test_create(self, mock_read_shadow_config, mock_boto3_resource, mock_env_load):
        """Should create runtime from environment."""
        mock_config = MagicMock()
        mock_env_load.return_value = mock_config
        mock_read_shadow_config.return_value = MagicMock(enabled=True, channel_id=123)
        mock_boto3_resource.return_value = MagicMock()

        runtime = UnifiedRuntime.create()

        assert runtime.config == mock_config
        mock_env_load.assert_called_once()

    @patch("bots.unified.coc.Client")
    @patch("bots.unified.boto3.resource")
    @patch("bots.unified.read_shadow_config")
    @pytest.mark.asyncio
    async def test_run_without_shadow_mode(
        self, mock_read_shadow_config, mock_boto3_resource, mock_coc_client
    ):
        """Should run bot and initialize CoC client when not in shadow mode."""
        mock_read_shadow_config.return_value = MagicMock(enabled=False, channel_id=None)
        mock_boto3_resource.return_value = MagicMock()
        mock_coc_instance = AsyncMock()
        mock_coc_client.return_value = mock_coc_instance

        config = EnvironmentConfig(
            discord_token="test_token",
            coc_email="test@example.com",
            coc_password="test_password",
            clan_tag="#TEST123",
            feeder_clan_tag=None,
            verified_role_id="123456789",
            admin_log_channel_id=None,
            giveaway_channel_id="987654321",
            giveaway_table_name="test_giveaways",
            giveaway_test_mode=False,
            tournament_table_name="test_tournaments",
            tournament_registration_channel_id=None,
            verification_table_name="test_verifications",
        )

        runtime = UnifiedRuntime(config)

        # Mock the bot's __aenter__ and __aexit__ methods
        runtime.bot = AsyncMock()
        runtime.bot.__aenter__ = AsyncMock(return_value=runtime.bot)
        runtime.bot.__aexit__ = AsyncMock(return_value=None)
        runtime.bot.start = AsyncMock()

        with patch.object(runtime, "configure_features") as mock_configure:
            await runtime.run()

            # Should initialize CoC client and reconfigure features
            mock_coc_instance.login.assert_called_once_with(
                "test@example.com", "test_password"
            )
            assert (
                mock_configure.call_count == 2
            )  # Once before, once after CoC client init
            runtime.bot.start.assert_called_once_with("test_token")

    @patch("bots.unified.boto3.resource")
    @patch("bots.unified.read_shadow_config")
    @pytest.mark.asyncio
    async def test_run_with_shadow_mode(
        self, mock_read_shadow_config, mock_boto3_resource
    ):
        """Should run bot in shadow mode without initializing CoC client."""
        mock_read_shadow_config.return_value = MagicMock(enabled=True, channel_id=123)
        mock_boto3_resource.return_value = MagicMock()

        config = EnvironmentConfig(
            discord_token="test_token",
            coc_email="test@example.com",
            coc_password="test_password",
            clan_tag="#TEST123",
            feeder_clan_tag=None,
            verified_role_id="123456789",
            admin_log_channel_id=None,
            giveaway_channel_id="987654321",
            giveaway_table_name="test_giveaways",
            giveaway_test_mode=False,
            tournament_table_name="test_tournaments",
            tournament_registration_channel_id=None,
            verification_table_name="test_verifications",
        )

        runtime = UnifiedRuntime(config)

        # Mock the bot's __aenter__ and __aexit__ methods
        runtime.bot = AsyncMock()
        runtime.bot.__aenter__ = AsyncMock(return_value=runtime.bot)
        runtime.bot.__aexit__ = AsyncMock(return_value=None)
        runtime.bot.start = AsyncMock()

        with patch.object(runtime, "configure_features") as mock_configure:
            await runtime.run()

            # Should not initialize CoC client in shadow mode
            assert runtime.coc_client is None
            mock_configure.assert_called_once()  # Only called once initially
            runtime.bot.start.assert_called_once_with("test_token")


@patch("bots.unified.UnifiedRuntime.create")
@patch("bots.unified.logging.basicConfig")
@pytest.mark.asyncio
async def test_main(mock_logging_config, mock_runtime_create):
    """Should create and run unified runtime."""
    mock_runtime = AsyncMock()
    mock_runtime_create.return_value = mock_runtime

    await main()

    mock_logging_config.assert_called_once()
    mock_runtime_create.assert_called_once()
    mock_runtime.run.assert_called_once()
