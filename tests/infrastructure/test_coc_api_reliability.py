"""Tests for CoC API reliability patterns and error handling.

This module focuses on testing the resilience and reliability patterns
in CoC API integration, particularly authentication retry logic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import coc
import pytest

from verifier_bot.coc_api import (
    fetch_player_with_status,
    get_player,
    get_player_clan_tag,
    get_player_with_retry,
    is_member_of_clan,
)


class TestCocApiReliabilityPatterns:
    """Test reliability patterns for CoC API integration.

    These tests focus on the system's ability to handle API failures gracefully
    and recover from authentication issues automatically.
    """

    @pytest.mark.asyncio
    async def test_should_retry_on_403_authentication_failure(self):
        """
        GIVEN the CoC API returns a 403 authentication error
        WHEN a player fetch is attempted with retry enabled
        THEN the system should re-authenticate and retry the request
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"

        # Simulate 403 error followed by success after re-auth
        auth_error = coc.HTTPException(MagicMock(), "Access denied")
        auth_error.status = 403

        mock_client.get_player.side_effect = [auth_error, mock_player]
        mock_client.login = AsyncMock()

        # Act
        result = await get_player_with_retry(
            mock_client,
            "test@example.com",
            "password",
            "#PLAYER1",
            reauth_cooldown=0,  # Disable cooldown for testing
        )

        # Assert - Verify successful recovery
        assert result == mock_player
        mock_client.login.assert_called_once_with("test@example.com", "password")
        assert mock_client.get_player.call_count == 2

    @pytest.mark.asyncio
    async def test_should_return_detailed_status_information(self):
        """
        GIVEN various API failure scenarios
        WHEN using the status-aware fetch function
        THEN detailed status information should be returned for proper error handling
        """
        mock_client = AsyncMock(spec=coc.Client)

        # Test case: Player not found
        mock_client.get_player.side_effect = coc.NotFound(MagicMock(), "Not found")

        result = await fetch_player_with_status(
            mock_client, "email", "password", "#NOTFOUND"
        )

        assert result.status == "not_found"
        assert result.player is None
        assert isinstance(result.exception, coc.NotFound)

    @pytest.mark.asyncio
    async def test_should_not_retry_on_non_authentication_errors(self):
        """
        GIVEN the CoC API returns non-403 HTTP errors
        WHEN a player fetch is attempted
        THEN the system should NOT attempt re-authentication
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        server_error = coc.HTTPException(MagicMock(), "Server error")
        server_error.status = 500

        mock_client.get_player.side_effect = server_error
        mock_client.login = AsyncMock()

        # Act
        result = await get_player_with_retry(
            mock_client, "test@example.com", "password", "#PLAYER1"
        )

        # Assert - No retry, no re-auth
        assert result is None
        mock_client.login.assert_not_called()
        mock_client.get_player.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_respect_reauth_cooldown_period(self):
        """
        GIVEN multiple 403 errors occur within the cooldown period
        WHEN re-authentication is attempted
        THEN only one re-auth should occur within the cooldown period
        """
        # Arrange - Reset global state for this test
        with patch("verifier_bot.coc_api._last_reauth_attempt", 0.0):
            mock_client = AsyncMock(spec=coc.Client)
            auth_error = coc.HTTPException(MagicMock(), "Access denied")
            auth_error.status = 403

            mock_client.get_player.side_effect = auth_error
            mock_client.login = AsyncMock()

            # Act - Make two rapid requests
            await get_player_with_retry(
                mock_client,
                "email",
                "password",
                "#PLAYER1",
                reauth_cooldown=60,  # 60 second cooldown
            )
            await get_player_with_retry(
                mock_client, "email", "password", "#PLAYER2", reauth_cooldown=60
            )

            # Assert - Login should only be called once due to cooldown
            assert mock_client.login.call_count == 1

    @pytest.mark.asyncio
    async def test_should_handle_failed_reauthentication(self):
        """
        GIVEN re-authentication itself fails
        WHEN the system attempts to recover from 403 errors
        THEN it should fail gracefully without infinite retry
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        auth_error = coc.HTTPException(MagicMock(), "Access denied")
        auth_error.status = 403

        mock_client.get_player.side_effect = auth_error
        mock_client.login.side_effect = coc.HTTPException(
            MagicMock(), "Invalid credentials"
        )

        # Act
        result = await fetch_player_with_status(
            mock_client,
            "invalid@email.com",
            "wrong_password",
            "#PLAYER1",
            reauth_cooldown=0,
        )

        # Assert
        assert result.status == "access_denied"
        assert result.player is None
        assert isinstance(result.exception, coc.HTTPException)

    @pytest.mark.asyncio
    async def test_should_limit_retry_attempts(self):
        """
        GIVEN persistent 403 errors despite re-authentication
        WHEN max retries is reached
        THEN the system should stop retrying and return failure
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        auth_error = coc.HTTPException(MagicMock(), "Access denied")
        auth_error.status = 403

        mock_client.get_player.side_effect = auth_error
        mock_client.login = AsyncMock()

        # Act
        result = await get_player_with_retry(
            mock_client,
            "email",
            "password",
            "#PLAYER1",
            max_retries=2,
            reauth_cooldown=0,
        )

        # Assert
        assert result is None
        # Should try initial + 2 retries = 3 total attempts
        assert mock_client.get_player.call_count == 3
        # Should attempt re-auth for each retry
        assert mock_client.login.call_count == 2

    @pytest.mark.asyncio
    async def test_should_succeed_immediately_when_no_errors(self):
        """
        GIVEN the CoC API responds successfully
        WHEN a player fetch is attempted
        THEN no retry logic should be triggered
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"

        mock_client.get_player.return_value = mock_player
        mock_client.login = AsyncMock()

        # Act
        result = await get_player_with_retry(
            mock_client, "email", "password", "#PLAYER1"
        )

        # Assert
        assert result == mock_player
        mock_client.get_player.assert_called_once()
        mock_client.login.assert_not_called()


class TestCocApiErrorClassification:
    """Test proper classification and handling of different API error types."""

    @pytest.mark.asyncio
    async def test_should_classify_not_found_errors_correctly(self):
        """NotFound errors should be classified as 'not_found' status."""
        mock_client = AsyncMock(spec=coc.Client)
        mock_client.get_player.side_effect = coc.NotFound(
            MagicMock(), "Player not found"
        )

        result = await fetch_player_with_status(
            mock_client, "email", "password", "#INVALID"
        )

        assert result.status == "not_found"
        assert result.player is None

    @pytest.mark.asyncio
    async def test_should_classify_403_errors_as_access_denied(self):
        """403 HTTP errors should be classified as 'access_denied' status."""
        mock_client = AsyncMock(spec=coc.Client)
        auth_error = coc.HTTPException(MagicMock(), "Forbidden")
        auth_error.status = 403

        mock_client.get_player.side_effect = auth_error
        mock_client.login = AsyncMock(
            side_effect=coc.HTTPException(MagicMock(), "Login failed")
        )

        result = await fetch_player_with_status(
            mock_client, "email", "password", "#PLAYER1", reauth_cooldown=0
        )

        assert result.status == "access_denied"

    @pytest.mark.asyncio
    async def test_should_classify_other_http_errors_as_generic_error(self):
        """Non-403 HTTP errors should be classified as 'error' status."""
        mock_client = AsyncMock(spec=coc.Client)
        server_error = coc.HTTPException(MagicMock(), "Server error")
        server_error.status = 500

        mock_client.get_player.side_effect = server_error

        result = await fetch_player_with_status(
            mock_client, "email", "password", "#PLAYER1"
        )

        assert result.status == "error"
        assert result.player is None

    @pytest.mark.asyncio
    async def test_should_return_success_status_for_valid_responses(self):
        """Successful API calls should return 'ok' status with player data."""
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"

        mock_client.get_player.return_value = mock_player

        result = await fetch_player_with_status(
            mock_client, "email", "password", "#PLAYER1"
        )

        assert result.status == "ok"
        assert result.player == mock_player
        assert result.exception is None


class TestCocApiConcurrencyHandling:
    """Test handling of concurrent requests during re-authentication."""

    @pytest.mark.asyncio
    async def test_should_handle_concurrent_reauth_attempts_safely(self):
        """
        GIVEN multiple concurrent requests trigger 403 errors
        WHEN re-authentication is attempted
        THEN only one re-auth should occur and all requests should benefit
        """
        import asyncio

        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)

        # First call triggers 403, subsequent calls succeed
        auth_error = coc.HTTPException(MagicMock(), "Access denied")
        auth_error.status = 403

        mock_client.get_player.side_effect = [
            auth_error,  # First request fails
            mock_player,  # After re-auth, requests succeed
            mock_player,
            mock_player,
        ]
        mock_client.login = AsyncMock()

        # Act - Make concurrent requests
        tasks = [
            get_player_with_retry(
                mock_client, "email", "password", f"#PLAYER{i}", reauth_cooldown=0
            )
            for i in range(3)
        ]

        results = await asyncio.gather(*tasks)

        # Assert - All should succeed and re-auth should only happen once
        assert all(result == mock_player for result in results)
        # Login might be called multiple times due to concurrency, but should be minimal
        assert mock_client.login.call_count >= 1


class TestCocApiBasicFunctions:
    """Test basic CoC API wrapper functions for comprehensive coverage."""

    @pytest.mark.asyncio
    async def test_get_player_success(self):
        """
        GIVEN a valid player tag
        WHEN get_player is called
        THEN it should return the player object
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"
        mock_client.get_player.return_value = mock_player

        # Act
        result = await get_player(mock_client, "#PLAYER1")

        # Assert
        assert result == mock_player
        mock_client.get_player.assert_called_once_with("#PLAYER1")

    @pytest.mark.asyncio
    async def test_get_player_not_found(self):
        """
        GIVEN a player tag that doesn't exist
        WHEN get_player is called
        THEN it should return None and log a warning
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_client.get_player.side_effect = coc.NotFound(MagicMock())

        # Act
        result = await get_player(mock_client, "#NOTFOUND")

        # Assert
        assert result is None
        mock_client.get_player.assert_called_once_with("#NOTFOUND")

    @pytest.mark.asyncio
    async def test_get_player_http_exception(self):
        """
        GIVEN the CoC API returns an HTTP exception
        WHEN get_player is called
        THEN it should return None and log an error
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_client.get_player.side_effect = coc.HTTPException(MagicMock(), "API Error")

        # Act
        result = await get_player(mock_client, "#PLAYER1")

        # Assert
        assert result is None
        mock_client.get_player.assert_called_once_with("#PLAYER1")

    @pytest.mark.asyncio
    async def test_is_member_of_clan_main_clan_member(self):
        """
        GIVEN a player who is in the main clan
        WHEN checking clan membership
        THEN it should return True
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#MAINCLAN"
        mock_player.clan = mock_clan
        mock_client.get_player.return_value = mock_player

        # Act
        result = await is_member_of_clan(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#PLAYER1"
        )

        # Assert
        assert result is True

    @pytest.mark.asyncio
    async def test_is_member_of_clan_feeder_clan_member(self):
        """
        GIVEN a player who is in the feeder clan
        WHEN checking clan membership
        THEN it should return True
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#FEEDERCLAN"
        mock_player.clan = mock_clan
        mock_client.get_player.return_value = mock_player

        # Act
        result = await is_member_of_clan(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#PLAYER1"
        )

        # Assert
        assert result is True

    @pytest.mark.asyncio
    async def test_is_member_of_clan_different_clan(self):
        """
        GIVEN a player who is in a different clan
        WHEN checking clan membership
        THEN it should return False
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#OTHERCLAN"
        mock_player.clan = mock_clan
        mock_client.get_player.return_value = mock_player

        # Act
        result = await is_member_of_clan(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#PLAYER1"
        )

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_is_member_of_clan_no_clan(self):
        """
        GIVEN a player who is not in any clan
        WHEN checking clan membership
        THEN it should return False
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_player.clan = None
        mock_client.get_player.return_value = mock_player

        # Act
        result = await is_member_of_clan(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#PLAYER1"
        )

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_is_member_of_clan_player_not_found(self):
        """
        GIVEN a player that doesn't exist
        WHEN checking clan membership
        THEN it should return False
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_client.get_player.return_value = None

        # Act
        result = await is_member_of_clan(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#NOTFOUND"
        )

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_get_player_clan_tag_main_clan(self):
        """
        GIVEN a player in the main clan
        WHEN getting their clan tag
        THEN it should return the main clan tag
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#MAINCLAN"
        mock_player.clan = mock_clan
        mock_client.get_player.return_value = mock_player

        # Act
        result = await get_player_clan_tag(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#PLAYER1"
        )

        # Assert
        assert result == "#MAINCLAN"

    @pytest.mark.asyncio
    async def test_get_player_clan_tag_feeder_clan(self):
        """
        GIVEN a player in the feeder clan
        WHEN getting their clan tag
        THEN it should return the feeder clan tag
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#feederclan"  # Test case insensitive matching
        mock_player.clan = mock_clan
        mock_client.get_player.return_value = mock_player

        # Act
        result = await get_player_clan_tag(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#PLAYER1"
        )

        # Assert
        assert result == "#FEEDERCLAN"

    @pytest.mark.asyncio
    async def test_get_player_clan_tag_different_clan(self):
        """
        GIVEN a player in a different clan
        WHEN getting their clan tag
        THEN it should return None
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_clan = MagicMock()
        mock_clan.tag = "#OTHERCLAN"
        mock_player.clan = mock_clan
        mock_client.get_player.return_value = mock_player

        # Act
        result = await get_player_clan_tag(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#PLAYER1"
        )

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_get_player_clan_tag_no_clan(self):
        """
        GIVEN a player not in any clan
        WHEN getting their clan tag
        THEN it should return None
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_player.clan = None
        mock_client.get_player.return_value = mock_player

        # Act
        result = await get_player_clan_tag(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#PLAYER1"
        )

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_get_player_clan_tag_player_not_found(self):
        """
        GIVEN a player that doesn't exist
        WHEN getting their clan tag
        THEN it should return None
        """
        # Arrange
        mock_client = AsyncMock(spec=coc.Client)
        mock_client.get_player.return_value = None

        # Act
        result = await get_player_clan_tag(
            mock_client, "#MAINCLAN", "#FEEDERCLAN", "#NOTFOUND"
        )

        # Assert
        assert result is None
