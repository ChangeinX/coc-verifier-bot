"""Tests for CoC API authentication and re-authentication logic."""

from unittest.mock import AsyncMock, MagicMock

import coc
import pytest

from verifier_bot import coc_api


class TestCocApiAuthentication:
    """Test CoC API authentication handling."""

    @pytest.mark.asyncio
    async def test_get_player_success(self):
        """Test successful player retrieval."""
        mock_client = AsyncMock(spec=coc.Client)
        mock_player = MagicMock(spec=coc.Player)
        mock_player.tag = "#PLAYER1"
        mock_player.name = "Test Player"
        mock_client.get_player.return_value = mock_player

        result = await coc_api.get_player(mock_client, "#PLAYER1")

        assert result == mock_player
        mock_client.get_player.assert_called_once_with("#PLAYER1")

    @pytest.mark.asyncio
    async def test_get_player_403_error_triggers_reauth(self):
        """Test that 403 errors trigger re-authentication."""
        mock_client = AsyncMock(spec=coc.Client)
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.reason = "Forbidden"

        # First call returns 403, second call succeeds after re-auth
        mock_player = MagicMock(spec=coc.Player)
        exc_403 = coc.HTTPException(mock_response, "accessDenied")
        exc_403.status = 403  # Set status on exception for proper detection
        mock_client.get_player.side_effect = [exc_403, mock_player]
        mock_client.login = AsyncMock()

        result = await coc_api.get_player_with_retry(
            mock_client, "email@test.com", "password", "#PLAYER1", reauth_cooldown=0
        )

        # Should succeed after re-auth
        assert result == mock_player
        # Should have called get_player twice (initial failure + retry after auth)
        assert mock_client.get_player.call_count == 2
        # Should have called login once for re-authentication
        mock_client.login.assert_called_once_with("email@test.com", "password")

    @pytest.mark.asyncio
    async def test_get_player_with_retry_max_attempts(self):
        """Test that retry logic has a maximum attempt limit."""
        mock_client = AsyncMock(spec=coc.Client)
        mock_response = MagicMock()
        mock_response.status = 403

        exc_403 = coc.HTTPException(mock_response, "accessDenied")
        exc_403.status = 403  # Set status on exception for proper detection
        mock_client.get_player.side_effect = exc_403
        mock_client.login = AsyncMock()

        result = await coc_api.get_player_with_retry(
            mock_client,
            "email@test.com",
            "password",
            "#PLAYER1",
            max_retries=1,
            reauth_cooldown=0,
        )

        # Should return None after exhausting retries
        assert result is None
        # Should have called get_player twice (initial + 1 retry)
        assert mock_client.get_player.call_count == 2
        # Should have called login once for re-authentication attempt
        mock_client.login.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_player_non_403_error_not_retried(self):
        """Test that non-403 errors are not retried."""
        mock_client = AsyncMock(spec=coc.Client)
        mock_response = MagicMock()
        mock_response.status = 500

        mock_client.get_player.side_effect = coc.HTTPException(
            mock_response, "Server Error"
        )
        mock_client.login = AsyncMock()

        result = await coc_api.get_player_with_retry(
            mock_client, "email@test.com", "password", "#PLAYER1"
        )

        # Should return None immediately for non-403 errors
        assert result is None
        # Should have called get_player only once (no retry)
        mock_client.get_player.assert_called_once()
        # Should NOT have attempted re-authentication
        mock_client.login.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_player_not_found_not_retried(self):
        """Test that NotFound errors are not retried."""
        mock_client = AsyncMock(spec=coc.Client)
        mock_client.get_player.side_effect = coc.NotFound(
            MagicMock(), "Player not found"
        )
        mock_client.login = AsyncMock()

        result = await coc_api.get_player_with_retry(
            mock_client, "email@test.com", "password", "#PLAYER1"
        )

        # Should return None for NotFound errors
        assert result is None
        # Should have called get_player only once (no retry)
        mock_client.get_player.assert_called_once()
        # Should NOT have attempted re-authentication
        mock_client.login.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_requests_during_reauth(self):
        """Test that concurrent requests handle re-authentication properly."""
        import asyncio

        mock_client = AsyncMock(spec=coc.Client)
        mock_response = MagicMock()
        mock_response.status = 403

        # First call triggers 403, subsequent calls should work after re-auth
        mock_player = MagicMock(spec=coc.Player)
        exc_403 = coc.HTTPException(mock_response, "accessDenied")
        exc_403.status = 403  # Set status on exception for proper detection
        mock_client.get_player.side_effect = [
            exc_403,
            mock_player,
            mock_player,
        ]
        mock_client.login = AsyncMock()

        # Make concurrent requests
        tasks = [
            coc_api.get_player_with_retry(
                mock_client, "email@test.com", "password", "#PLAYER1", reauth_cooldown=0
            ),
            coc_api.get_player_with_retry(
                mock_client, "email@test.com", "password", "#PLAYER2", reauth_cooldown=0
            ),
        ]

        results = await asyncio.gather(*tasks)

        # Both should succeed
        assert results[0] == mock_player
        assert results[1] == mock_player
        # Login should be called at least once (could be more due to concurrency)
        assert mock_client.login.called

    @pytest.mark.asyncio
    async def test_invalid_credentials_during_reauth(self):
        """Test handling of invalid credentials during re-authentication."""
        mock_client = AsyncMock(spec=coc.Client)
        mock_response = MagicMock()
        mock_response.status = 403

        exc_403 = coc.HTTPException(mock_response, "accessDenied")
        exc_403.status = 403  # Set status on exception for proper detection
        mock_client.get_player.side_effect = exc_403
        mock_client.login.side_effect = coc.HTTPException(
            mock_response, "Invalid credentials"
        )

        result = await coc_api.get_player_with_retry(
            mock_client,
            "invalid@test.com",
            "wrong_password",
            "#PLAYER1",
            reauth_cooldown=0,
        )

        # Should return None when re-authentication fails
        assert result is None
        # Should have attempted login
        mock_client.login.assert_called_once_with("invalid@test.com", "wrong_password")
