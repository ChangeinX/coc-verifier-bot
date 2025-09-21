"""Simple tests to increase coverage."""

from bots.config import ShadowConfig


def test_shadow_config_str():
    """Test shadow config string representation."""
    config = ShadowConfig(enabled=True, channel_id=123)
    assert "ShadowConfig" in str(config)


def test_shadow_config_repr():
    """Test shadow config repr."""
    config = ShadowConfig(enabled=False, channel_id=None)
    assert "enabled=False" in repr(config)


def test_shadow_config_hash():
    """Test shadow config is hashable."""
    config = ShadowConfig(enabled=True, channel_id=123)
    assert isinstance(hash(config), int)


def test_import_main_directly():
    """Test importing main module directly."""
    import bots.__main__

    # Test the imports exist
    assert hasattr(bots.__main__, "asyncio")
    assert hasattr(bots.__main__, "main")


def test_import_unified():
    """Test importing unified module."""
    from bots import unified

    assert hasattr(unified, "main")
    assert hasattr(unified, "UnifiedRuntime")
    assert hasattr(unified, "EnvironmentConfig")


def test_import_config():
    """Test importing config module."""
    from bots import config

    assert hasattr(config, "ShadowConfig")
    assert hasattr(config, "read_shadow_config")


def test_import_shadow():
    """Test importing shadow module."""
    from bots import shadow

    assert hasattr(shadow, "ShadowReporter")
