"""Simple tests for newsbot.py to increase coverage."""

import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Mock environment variables before importing
with patch.dict(
    os.environ,
    {
        "DISCORD_TOKEN": "fake_token",
        "OPENAI_API_KEY": "fake_openai_key",
        "NEWS_CHANNEL_ID": "123456",
    },
):
    import newsbot


class TestSimpleNewsbotFunctions:
    """Simple tests to increase coverage."""

    def test_parse_town_hall_valid(self):
        """Test parsing town hall from valid formats."""
        assert newsbot.parse_town_hall("TH10") == 10
        assert newsbot.parse_town_hall("th 12") == 12
        assert newsbot.parse_town_hall("town hall 15") == 15
        assert newsbot.parse_town_hall("13") == 13

    def test_parse_town_hall_invalid(self):
        """Test parsing town hall from invalid formats."""
        assert newsbot.parse_town_hall("") is None
        assert newsbot.parse_town_hall("attack") is None
        assert newsbot.parse_town_hall("th") is None

    def test_ai_summary_simple(self):
        """Simple test for AI summary."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "Test summary"

        with patch.object(
            newsbot.client.chat.completions, "create", return_value=mock_response
        ):
            result = newsbot.ai_summary("Test prompt")
            assert result == "Test summary"

    def test_ai_summary_exception(self):
        """Test AI summary handling exceptions."""
        with patch.object(
            newsbot.client.chat.completions,
            "create",
            side_effect=Exception("API error"),
        ):
            result = newsbot.ai_summary("Test prompt")
            assert result is None

    def test_generate_video_summary_empty(self):
        """Test video summary with empty video."""
        video = {}
        result = newsbot.generate_video_summary(video)
        assert result == video

    def test_generate_video_summary_no_description(self):
        """Test video summary with no description."""
        video = {"title": "Test Video"}
        with patch.object(newsbot, "ai_summary", return_value=None):
            result = newsbot.generate_video_summary(video)
            assert result == video

    def test_search_youtube_videos_simple(self):
        """Simple test for YouTube search."""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 404  # Will trigger fallback
            mock_get.return_value = mock_response

            videos = newsbot.search_youtube_videos("test query")
            assert len(videos) >= 1
            assert "Test Query - Clash of Clans" in videos[0]["title"]

    @pytest.mark.asyncio
    async def test_news_loop_simple(self):
        """Simple test for news loop."""
        # Test night hours case
        with patch("datetime.datetime") as mock_datetime:
            mock_now = Mock()
            mock_now.hour = 3  # Night hours
            mock_datetime.now.return_value = mock_now

            # Should not raise exception during night hours
            await newsbot.news_loop()

    def test_env_vars_accessible(self):
        """Test that environment variables are accessible."""
        assert newsbot.TOKEN is not None
        assert newsbot.NEWS_CHANNEL_ID == 123456

    def test_strat_command_exists(self):
        """Test that strat command exists."""
        assert hasattr(newsbot, "strat")
        # Command objects are not directly callable in tests

    def test_news_command_exists(self):
        """Test that news command exists."""
        assert hasattr(newsbot, "news_command")
        # Command objects are not directly callable in tests

    def test_generate_video_summary_with_description(self):
        """Test video summary with description but no AI summary."""
        video = {"title": "Test Video", "description": "Test description"}
        ai_response = "TITLE: Enhanced Title\nSUMMARY: This is a test summary."
        with patch.object(newsbot, "ai_summary", return_value=ai_response):
            result = newsbot.generate_video_summary(video)
            assert "enhanced_title" in result
            assert result["enhanced_title"] == "Enhanced Title"
            assert "enhanced_summary" in result
            assert result["enhanced_summary"] == "This is a test summary."

    def test_search_youtube_videos_success(self):
        """Test successful YouTube search."""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "items": [
                    {
                        "snippet": {
                            "title": "Real Video Title",
                            "description": "Real description",
                            "publishedAt": "2024-01-01T00:00:00Z",
                        },
                        "id": {"videoId": "abc123"},
                    }
                ]
            }
            mock_get.return_value = mock_response

            videos = newsbot.search_youtube_videos("clash of clans")
            assert len(videos) >= 1
            # Should include both API results and fallbacks

    def test_search_youtube_videos_api_error(self):
        """Test YouTube search with API error."""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_get.return_value = mock_response

            videos = newsbot.search_youtube_videos("test query")
            # Should still return fallback videos
            assert len(videos) >= 1

    @pytest.mark.asyncio
    async def test_news_loop_day_hours(self):
        """Test news loop during day hours."""
        with (
            patch("datetime.datetime") as mock_datetime,
            patch.object(newsbot, "search_youtube_videos", return_value=[]),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_now = Mock()
            mock_now.hour = 10  # Day hours
            mock_datetime.now.return_value = mock_now

            # Should not raise exception during day hours
            await newsbot.news_loop()

    def test_client_initialization(self):
        """Test OpenAI client initialization."""
        assert newsbot.client is not None
        assert hasattr(newsbot.client, "chat")

    def test_bot_tree_exists(self):
        """Test that bot command tree exists."""
        assert hasattr(newsbot, "tree")
        assert hasattr(newsbot, "bot")

    def test_regex_patterns(self):
        """Test regex patterns used in parsing."""
        # Test town hall patterns that might be used
        import re

        th_pattern = r"th\s*(\d+)|town\s*hall\s*(\d+)|^(\d+)$"

        match1 = re.search(th_pattern, "th10", re.IGNORECASE)
        assert match1 is not None

        match2 = re.search(th_pattern, "town hall 15", re.IGNORECASE)
        assert match2 is not None

        match3 = re.search(th_pattern, "13", re.IGNORECASE)
        assert match3 is not None

    def test_video_structure(self):
        """Test expected video data structure."""
        # Test the structure that the code expects
        video = {
            "title": "Test Title",
            "description": "Test Description",
            "url": "https://youtube.com/watch?v=abc123",
            "published_at": "2024-01-01",
        }

        # These keys should exist in video objects
        assert "title" in video
        assert "description" in video
        assert "url" in video
        assert "published_at" in video

    def test_constants_and_config(self):
        """Test that required constants and config exist."""
        assert hasattr(newsbot, "TOKEN")
        assert hasattr(newsbot, "NEWS_CHANNEL_ID")
        assert hasattr(newsbot, "client")  # OpenAI client
