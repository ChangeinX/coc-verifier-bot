"""Tests for bot.py module proxy."""

import os
from unittest import mock


@mock.patch.dict(
    os.environ,
    {
        "DISCORD_TOKEN": "test_token",
        "COC_EMAIL": "test@example.com",
        "COC_PASSWORD": "test_password",
        "CLAN_TAG": "#TEST123",
        "DDB_TABLE_NAME": "test_table",
        "VERIFIED_ROLE_ID": "123456",
        "AWS_REGION": "us-east-1",
    },
    clear=True,
)
@mock.patch("boto3.resource")
@mock.patch("discord.Client")
@mock.patch("discord.app_commands.CommandTree")
def test_module_proxy_delattr(mock_tree, mock_client, mock_boto):
    """Test the __delattr__ method of the module proxy."""
    import bot

    # Set an attribute on the proxy
    bot.test_attr = "test_value"

    # Verify it exists
    assert hasattr(bot, "test_attr")

    # Delete the attribute
    delattr(bot, "test_attr")

    # Verify it's gone
    assert not hasattr(bot, "test_attr")


@mock.patch.dict(
    os.environ,
    {
        "DISCORD_TOKEN": "test_token",
        "COC_EMAIL": "test@example.com",
        "COC_PASSWORD": "test_password",
        "CLAN_TAG": "#TEST123",
        "DDB_TABLE_NAME": "test_table",
        "VERIFIED_ROLE_ID": "123456",
        "AWS_REGION": "us-east-1",
    },
    clear=True,
)
@mock.patch("boto3.resource")
@mock.patch("discord.Client")
@mock.patch("discord.app_commands.CommandTree")
def test_module_proxy_dir(mock_tree, mock_client, mock_boto):
    """Test the __dir__ method of the module proxy."""
    import bot

    # Get directory listing
    dir_result = dir(bot)

    # Should be a sorted list containing implementation attributes
    assert isinstance(dir_result, list)
    assert len(dir_result) > 0

    # Should contain some expected attributes from the verification module
    assert any("verify" in attr.lower() for attr in dir_result)


@mock.patch.dict(
    os.environ,
    {
        "DISCORD_TOKEN": "test_token",
        "COC_EMAIL": "test@example.com",
        "COC_PASSWORD": "test_password",
        "CLAN_TAG": "#TEST123",
        "DDB_TABLE_NAME": "test_table",
        "VERIFIED_ROLE_ID": "123456",
        "AWS_REGION": "us-east-1",
    },
    clear=True,
)
@mock.patch("boto3.resource")
@mock.patch("discord.Client")
@mock.patch("discord.app_commands.CommandTree")
def test_module_proxy_setattr(mock_tree, mock_client, mock_boto):
    """Test the __setattr__ method of the module proxy."""
    import bot

    # Set an attribute on the proxy
    bot.test_setattr = "test_value"

    # Verify it was set correctly
    assert bot.test_setattr == "test_value"
    assert hasattr(bot, "test_setattr")


@mock.patch.dict(
    os.environ,
    {
        "DISCORD_TOKEN": "test_token",
        "COC_EMAIL": "test@example.com",
        "COC_PASSWORD": "test_password",
        "CLAN_TAG": "#TEST123",
        "DDB_TABLE_NAME": "test_table",
        "VERIFIED_ROLE_ID": "123456",
        "AWS_REGION": "us-east-1",
    },
    clear=True,
)
@mock.patch("boto3.resource")
@mock.patch("discord.Client")
@mock.patch("discord.app_commands.CommandTree")
def test_module_proxy_getattr(mock_tree, mock_client, mock_boto):
    """Test the __getattr__ method of the module proxy."""
    import bot

    # Should be able to access attributes from the implementation
    # This covers the __getattr__ method
    assert hasattr(bot, "normalize_player_tag")

    # Access an attribute to trigger __getattr__
    tag_func = bot.normalize_player_tag
    assert callable(tag_func)
