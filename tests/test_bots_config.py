"""Tests for bots.config module."""

import os
from unittest import mock

import pytest

from bots.config import ShadowConfig, env_bool, env_int, read_shadow_config


class TestEnvBool:
    """Test env_bool function."""

    def test_env_bool_default_false(self):
        """Should return default False when env var not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = env_bool("TEST_VAR")
            assert result is False

    def test_env_bool_default_true(self):
        """Should return custom default when env var not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = env_bool("TEST_VAR", default=True)
            assert result is True

    def test_env_bool_true_values(self):
        """Should return True for valid true values."""
        true_values = ["1", "true", "yes", "on", "TRUE", "YES", "ON", " True ", " 1 "]
        for value in true_values:
            with mock.patch.dict(os.environ, {"TEST_VAR": value}, clear=True):
                result = env_bool("TEST_VAR")
                assert result is True, f"Failed for value: {value}"

    def test_env_bool_false_values(self):
        """Should return False for valid false values."""
        false_values = [
            "0",
            "false",
            "no",
            "off",
            "FALSE",
            "NO",
            "OFF",
            " False ",
            " 0 ",
        ]
        for value in false_values:
            with mock.patch.dict(os.environ, {"TEST_VAR": value}, clear=True):
                result = env_bool("TEST_VAR")
                assert result is False, f"Failed for value: {value}"

    def test_env_bool_invalid_values(self):
        """Should return default for invalid values."""
        invalid_values = ["invalid", "maybe", "2", "", "  "]
        for value in invalid_values:
            with mock.patch.dict(os.environ, {"TEST_VAR": value}, clear=True):
                result = env_bool("TEST_VAR", default=True)
                assert result is True, f"Failed for value: {value}"

                result = env_bool("TEST_VAR", default=False)
                assert result is False, f"Failed for value: {value}"


class TestEnvInt:
    """Test env_int function."""

    def test_env_int_default_none(self):
        """Should return None when env var not set and no default."""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = env_int("TEST_VAR")
            assert result is None

    def test_env_int_custom_default(self):
        """Should return custom default when env var not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = env_int("TEST_VAR", default=42)
            assert result == 42

    def test_env_int_empty_string(self):
        """Should return default for empty string."""
        with mock.patch.dict(os.environ, {"TEST_VAR": ""}, clear=True):
            result = env_int("TEST_VAR", default=42)
            assert result == 42

    def test_env_int_valid_values(self):
        """Should parse valid integer values."""
        test_cases = [
            ("123", 123),
            ("0", 0),
            ("-456", -456),
            ("  789  ", 789),
        ]
        for env_value, expected in test_cases:
            with mock.patch.dict(os.environ, {"TEST_VAR": env_value}, clear=True):
                result = env_int("TEST_VAR")
                assert result == expected, f"Failed for value: {env_value}"

    def test_env_int_invalid_values(self):
        """Should return default for invalid values."""
        invalid_values = ["not_a_number", "12.34", "abc123", "123abc"]
        for value in invalid_values:
            with mock.patch.dict(os.environ, {"TEST_VAR": value}, clear=True):
                result = env_int("TEST_VAR", default=42)
                assert result == 42, f"Failed for value: {value}"


class TestShadowConfig:
    """Test ShadowConfig dataclass."""

    def test_shadow_config_creation(self):
        """Should create ShadowConfig with given values."""
        config = ShadowConfig(enabled=True, channel_id=123456)
        assert config.enabled is True
        assert config.channel_id == 123456

    def test_shadow_config_none_channel(self):
        """Should allow None channel_id."""
        config = ShadowConfig(enabled=False, channel_id=None)
        assert config.enabled is False
        assert config.channel_id is None

    def test_shadow_config_immutable(self):
        """Should be immutable (frozen dataclass)."""
        config = ShadowConfig(enabled=True, channel_id=123)
        with pytest.raises(AttributeError):
            config.enabled = False
        with pytest.raises(AttributeError):
            config.channel_id = 456


class TestReadShadowConfig:
    """Test read_shadow_config function."""

    def test_read_shadow_config_default(self):
        """Should use defaults when no env vars set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            config = read_shadow_config()
            assert config.enabled is False
            assert config.channel_id is None

    def test_read_shadow_config_custom_default(self):
        """Should use custom default for enabled."""
        with mock.patch.dict(os.environ, {}, clear=True):
            config = read_shadow_config(default_enabled=True)
            assert config.enabled is True
            assert config.channel_id is None

    def test_read_shadow_config_from_env(self):
        """Should read config from environment variables."""
        env_vars = {"SHADOW_MODE": "true", "SHADOW_CHANNEL_ID": "987654321"}
        with mock.patch.dict(os.environ, env_vars, clear=True):
            config = read_shadow_config()
            assert config.enabled is True
            assert config.channel_id == 987654321

    def test_read_shadow_config_mixed_env(self):
        """Should handle partial env var configuration."""
        # Only shadow mode set
        with mock.patch.dict(os.environ, {"SHADOW_MODE": "yes"}, clear=True):
            config = read_shadow_config()
            assert config.enabled is True
            assert config.channel_id is None

        # Only channel ID set
        with mock.patch.dict(os.environ, {"SHADOW_CHANNEL_ID": "123"}, clear=True):
            config = read_shadow_config()
            assert config.enabled is False
            assert config.channel_id == 123

    def test_read_shadow_config_invalid_env(self):
        """Should handle invalid environment values gracefully."""
        env_vars = {"SHADOW_MODE": "invalid", "SHADOW_CHANNEL_ID": "not_a_number"}
        with mock.patch.dict(os.environ, env_vars, clear=True):
            config = read_shadow_config(default_enabled=True)
            assert config.enabled is True  # Falls back to default
            assert config.channel_id is None  # Falls back to default
