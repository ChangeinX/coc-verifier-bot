"""Tests for the config module."""

import os
from unittest.mock import patch, mock_open
from dataclasses import FrozenInstanceError
import pytest

from bots.config import ShadowConfig, read_shadow_config


class TestShadowConfig:
    """Test shadow configuration dataclass."""

    def test_init_enabled(self):
        """Should initialize with enabled configuration."""
        config = ShadowConfig(enabled=True, channel_id=123456)

        assert config.enabled is True
        assert config.channel_id == 123456

    def test_init_disabled(self):
        """Should initialize with disabled configuration."""
        config = ShadowConfig(enabled=False, channel_id=None)

        assert config.enabled is False
        assert config.channel_id is None

    def test_frozen_dataclass(self):
        """Should be frozen and immutable."""
        config = ShadowConfig(enabled=True, channel_id=123456)

        with pytest.raises(FrozenInstanceError):
            config.enabled = False

    def test_equality(self):
        """Should support equality comparison."""
        config1 = ShadowConfig(enabled=True, channel_id=123456)
        config2 = ShadowConfig(enabled=True, channel_id=123456)
        config3 = ShadowConfig(enabled=False, channel_id=None)

        assert config1 == config2
        assert config1 != config3


class TestReadShadowConfig:
    """Test shadow configuration reading."""

    def test_read_shadow_config_file_exists_valid_json(self):
        """Should read configuration from valid JSON file."""
        config_data = '{"enabled": true, "channel_id": 123456}'

        with patch("builtins.open", mock_open(read_data=config_data)):
            with patch("os.path.exists", return_value=True):
                config = read_shadow_config()

                assert config.enabled is True
                assert config.channel_id == 123456

    def test_read_shadow_config_file_exists_invalid_json(self):
        """Should return default config when JSON is invalid."""
        config_data = '{"enabled": true, "channel_id":'  # Invalid JSON

        with patch("builtins.open", mock_open(read_data=config_data)):
            with patch("os.path.exists", return_value=True):
                config = read_shadow_config(default_enabled=False)

                assert config.enabled is False
                assert config.channel_id is None

    def test_read_shadow_config_file_not_exists_default_false(self):
        """Should return default disabled config when file doesn't exist."""
        with patch("os.path.exists", return_value=False):
            config = read_shadow_config(default_enabled=False)

            assert config.enabled is False
            assert config.channel_id is None

    def test_read_shadow_config_file_not_exists_default_true(self):
        """Should return default enabled config when file doesn't exist."""
        with patch("os.path.exists", return_value=False):
            config = read_shadow_config(default_enabled=True)

            assert config.enabled is True
            assert config.channel_id is None

    def test_read_shadow_config_file_read_error(self):
        """Should return default config when file read fails."""
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", side_effect=IOError("Permission denied")):
                config = read_shadow_config(default_enabled=False)

                assert config.enabled is False
                assert config.channel_id is None

    def test_read_shadow_config_missing_keys(self):
        """Should handle missing keys in JSON file."""
        config_data = '{}'  # Empty JSON

        with patch("builtins.open", mock_open(read_data=config_data)):
            with patch("os.path.exists", return_value=True):
                config = read_shadow_config(default_enabled=False)

                assert config.enabled is False
                assert config.channel_id is None

    def test_read_shadow_config_partial_keys(self):
        """Should handle partial keys in JSON file."""
        config_data = '{"enabled": true}'  # Missing channel_id

        with patch("builtins.open", mock_open(read_data=config_data)):
            with patch("os.path.exists", return_value=True):
                config = read_shadow_config(default_enabled=False)

                assert config.enabled is True
                assert config.channel_id is None

    def test_read_shadow_config_wrong_types(self):
        """Should handle wrong types in JSON file."""
        config_data = '{"enabled": "yes", "channel_id": "not_a_number"}'

        with patch("builtins.open", mock_open(read_data=config_data)):
            with patch("os.path.exists", return_value=True):
                config = read_shadow_config(default_enabled=False)

                # Should fall back to defaults when types are wrong
                assert config.enabled is False
                assert config.channel_id is None