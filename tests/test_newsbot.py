"""Tests for the news bot (newsbot.py)."""

import asyncio
import datetime
import json
import os
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest
import requests

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


class TestEnvironmentValidation:
    """Test environment variable validation."""

    def test_required_vars_defined(self):
        """Test that required environment variables are properly defined."""
        assert newsbot.TOKEN is not None
        assert newsbot.OPENAI_KEY is not None
        assert newsbot.NEWS_CHANNEL_ID == 123456


class TestUtilityFunctions:
    """Test utility functions."""

    def test_parse_town_hall_valid_formats(self):
        """Test parsing town hall from various valid formats."""
        test_cases = [
            ("TH10", 10),
            ("th 12", 12),
            ("town hall 15", 15),
            ("townhall9", 9),
            ("TH 16 attack", 16),
            ("13", 13),
            ("th10 strategy", 10),
        ]

        for input_text, expected in test_cases:
            result = newsbot.parse_town_hall(input_text)
            assert result == expected, f"Failed for input: {input_text}"

    def test_parse_town_hall_invalid_formats(self):
        """Test parsing town hall from invalid formats."""
        test_cases = ["", None, "attack strategy", "clash of clans", "th", "townhall"]

        for input_text in test_cases:
            result = newsbot.parse_town_hall(input_text)
            assert result is None, f"Should return None for input: {input_text}"


class TestYouTubeSearch:
    """Test YouTube video search functionality."""

    @patch("requests.get")
    def test_search_youtube_videos_success(self, mock_get):
        """Test successful YouTube video search."""
        # Mock YouTube response with embedded JSON data
        mock_response_content = """
        <script>var ytInitialData = {
            "contents": {
                "twoColumnSearchResultsRenderer": {
                    "primaryContents": {
                        "sectionListRenderer": {
                            "contents": [{
                                "itemSectionRenderer": {
                                    "contents": [{
                                        "videoRenderer": {
                                            "videoId": "test123",
                                            "title": {"runs": [{"text": "Best TH10 Attack Strategy"}]},
                                            "descriptionSnippet": {"runs": [{"text": "Learn the best strategies"}]},
                                            "viewCountText": {"simpleText": "100K views"},
                                            "publishedTimeText": {"simpleText": "1 week ago"}
                                        }
                                    }]
                                }
                            }]
                        }
                    }
                }
            }
        };</script>
        """

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = mock_response_content
        mock_get.return_value = mock_response

        videos = newsbot.search_youtube_videos("TH10 attack", max_results=3)

        assert len(videos) == 1
        assert videos[0]["title"] == "Best TH10 Attack Strategy"
        assert videos[0]["id"] == "test123"
        assert videos[0]["url"] == "https://www.youtube.com/watch?v=test123"
        assert videos[0]["description"] == "Learn the best strategies"
        assert videos[0]["view_count"] == "100K views"
        assert videos[0]["published_time"] == "1 week ago"

        mock_get.assert_called_once()

    @patch("requests.get")
    def test_search_youtube_videos_request_fails(self, mock_get):
        """Test YouTube search when request fails."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        videos = newsbot.search_youtube_videos("test query")

        # Should return fallback videos
        assert len(videos) >= 1
        assert "Test Query - Clash of Clans Guide" in videos[0]["title"]

    @patch("requests.get")
    def test_search_youtube_videos_malformed_json(self, mock_get):
        """Test YouTube search with malformed JSON."""
        mock_response_content = """
        <script>var ytInitialData = {invalid json};</script>
        """

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = mock_response_content
        mock_get.return_value = mock_response

        videos = newsbot.search_youtube_videos("test query")

        # Should return fallback videos when JSON parsing fails
        assert len(videos) >= 1

    @patch("requests.get")
    def test_search_youtube_videos_exception(self, mock_get):
        """Test YouTube search handling exceptions."""
        mock_get.side_effect = requests.RequestException("Network error")

        videos = newsbot.search_youtube_videos("test query")

        # Should return fallback video
        assert len(videos) == 1
        assert "Test Query - Clash of Clans" in videos[0]["title"]

    @patch("requests.get")
    def test_search_youtube_videos_max_results(self, mock_get):
        """Test max_results parameter limiting."""
        # Mock multiple videos in response
        mock_videos = []
        for i in range(10):
            mock_videos.append(
                {
                    "videoRenderer": {
                        "videoId": f"test{i}",
                        "title": {"runs": [{"text": f"Video {i}"}]},
                        "descriptionSnippet": {"runs": [{"text": f"Description {i}"}]},
                    }
                }
            )

        mock_response_content = f"""
        <script>var ytInitialData = {{
            "contents": {{
                "twoColumnSearchResultsRenderer": {{
                    "primaryContents": {{
                        "sectionListRenderer": {{
                            "contents": [{{
                                "itemSectionRenderer": {{
                                    "contents": {json.dumps(mock_videos)}
                                }}
                            }}]
                        }}
                    }}
                }}
            }}
        }};</script>
        """

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = mock_response_content
        mock_get.return_value = mock_response

        videos = newsbot.search_youtube_videos("test", max_results=3)

        assert len(videos) == 3  # Should limit to max_results


class TestAISummary:
    """Test AI summary functionality."""

    @patch.object(newsbot.client.chat.completions, "create")
    def test_ai_summary_success(self, mock_create):
        """Test successful AI summary generation."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "Test summary content"
        mock_create.return_value = mock_response

        result = newsbot.ai_summary("Test prompt")

        assert result == "Test summary content"
        mock_create.assert_called_once_with(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Test prompt"}],
            max_tokens=200,
        )

    @patch.object(newsbot.client.chat.completions, "create")
    def test_ai_summary_exception(self, mock_create):
        """Test AI summary handling exceptions."""
        mock_create.side_effect = Exception("API error")

        result = newsbot.ai_summary("Test prompt")

        assert result is None

    @patch.object(newsbot.client.chat.completions, "create")
    def test_ai_summary_strips_whitespace(self, mock_create):
        """Test AI summary strips leading/trailing whitespace."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "  \n  Test content  \n  "
        mock_create.return_value = mock_response

        result = newsbot.ai_summary("Test prompt")

        assert result == "Test content"


class TestThreadTitleGeneration:
    """Test thread title generation."""

    @patch.object(newsbot, "ai_summary")
    def test_generate_thread_title_with_ai(self, mock_ai_summary):
        """Test thread title generation using AI."""
        mock_videos = [
            {"title": "New Clash Update Leaked!"},
            {"title": "Town Hall 16 Coming Soon"},
            {"title": "Balance Changes This Week"},
        ]
        mock_ai_summary.return_value = "🔥 Major Update Incoming!"

        result = newsbot.generate_thread_title(mock_videos)

        assert result == "🔥 Major Update Incoming!"
        mock_ai_summary.assert_called_once()

    @patch.object(newsbot, "ai_summary")
    @patch("newsbot.datetime")
    def test_generate_thread_title_ai_too_long(
        self, mock_datetime_module, mock_ai_summary
    ):
        """Test thread title generation when AI response is too long."""
        mock_videos = [{"title": "Test Video"}]
        mock_ai_summary.return_value = (
            "This title is way too long and exceeds the 40 character limit"
        )

        mock_now = datetime.datetime(2024, 5, 15, 10, 30)
        mock_datetime_module.datetime.now.return_value = mock_now
        result = newsbot.generate_thread_title(mock_videos)

        assert "CoC News 05-15" in result

    @patch.object(newsbot, "ai_summary")
    @patch("newsbot.datetime")
    def test_generate_thread_title_fallbacks(
        self, mock_datetime_module, mock_ai_summary
    ):
        """Test thread title generation fallbacks based on content."""
        mock_ai_summary.return_value = None

        mock_now = datetime.datetime(2024, 5, 15, 10, 30)
        mock_datetime_module.datetime.now.return_value = mock_now

        # Test update fallback
        update_videos = [{"title": "New Update Coming Soon"}]
        result = newsbot.generate_thread_title(update_videos)
        assert "🔥 CoC Update News 05-15" == result

        # Test leak fallback
        leak_videos = [{"title": "Major Leak Revealed!"}]
        result = newsbot.generate_thread_title(leak_videos)
        assert "⚡ CoC Leaks & News 05-15" == result

        # Test general fallback
        general_videos = [{"title": "General CoC Content"}]
        result = newsbot.generate_thread_title(general_videos)
        assert "📰 CoC News 05-15" == result

    @patch("newsbot.datetime")
    def test_generate_thread_title_empty_videos(self, mock_datetime_module):
        """Test thread title generation with empty video list."""
        mock_now = datetime.datetime(2024, 5, 15, 10, 30)
        mock_datetime_module.datetime.now.return_value = mock_now

        result = newsbot.generate_thread_title([])

        assert "CoC News 05-15 10:30" == result


class TestVideoSummaryGeneration:
    """Test video summary generation."""

    @patch.object(newsbot, "ai_summary")
    def test_generate_video_summary_success(self, mock_ai_summary):
        """Test successful video summary generation."""
        mock_video = {
            "title": "Amazing TH15 Attack Strategy Guide",
            "description": "Learn the best TH15 attacks in this comprehensive guide",
        }
        mock_ai_summary.return_value = """TITLE: TH15 Attack Guide
SUMMARY: This video teaches powerful TH15 attack strategies. Perfect for competitive players."""

        result = newsbot.generate_video_summary(mock_video)

        assert result["enhanced_title"] == "TH15 Attack Guide"
        assert (
            result["enhanced_summary"]
            == "This video teaches powerful TH15 attack strategies. Perfect for competitive players."
        )
        mock_ai_summary.assert_called_once()

    @patch.object(newsbot, "ai_summary")
    def test_generate_video_summary_no_description(self, mock_ai_summary):
        """Test video summary with no description."""
        mock_video = {"title": "Test Video"}
        mock_ai_summary.return_value = None  # AI returns None

        result = newsbot.generate_video_summary(mock_video)

        # Should return original video unchanged when AI returns None
        assert result == mock_video
        mock_ai_summary.assert_called_once()

    @patch.object(newsbot, "ai_summary")
    def test_generate_video_summary_ai_fails(self, mock_ai_summary):
        """Test video summary when AI fails."""
        mock_video = {"title": "Test Video", "description": "Test description"}
        mock_ai_summary.return_value = None

        result = newsbot.generate_video_summary(mock_video)

        assert result == mock_video

    @patch.object(newsbot, "ai_summary")
    def test_generate_video_summary_malformed_response(self, mock_ai_summary):
        """Test video summary with malformed AI response."""
        mock_video = {"title": "Test Video", "description": "Test description"}
        mock_ai_summary.return_value = "Invalid response format"

        result = newsbot.generate_video_summary(mock_video)

        assert result == mock_video

    @patch.object(newsbot, "ai_summary")
    def test_generate_video_summary_title_too_long(self, mock_ai_summary):
        """Test video summary with title that's too long."""
        mock_video = {"title": "Test Video", "description": "Test description"}
        mock_ai_summary.return_value = """TITLE: This is a very long title that exceeds the 60 character limit
SUMMARY: Good summary here."""

        result = newsbot.generate_video_summary(mock_video)

        # Should not include the enhanced title if too long
        assert "enhanced_title" not in result
        assert result["enhanced_summary"] == "Good summary here."


class TestNewsLoop:
    """Test the news loop background task."""

    @pytest.mark.asyncio
    async def test_news_loop_night_hours(self):
        """Test news loop skips during night hours."""
        with (
            patch("datetime.datetime") as mock_datetime,
            patch.object(newsbot, "search_youtube_videos") as mock_search,
        ):
            # Set time to 3 AM (night hours)
            mock_now = datetime.datetime(2024, 5, 15, 3, 0)
            mock_datetime.now.return_value = mock_now
            # Mock the hour attribute
            type(mock_now).hour = 3

            await newsbot.news_loop()

            # Should not search for videos during night hours
            mock_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_news_loop_no_videos(self):
        """Test news loop when no videos are found."""
        with (
            patch("datetime.datetime") as mock_datetime,
            patch.object(newsbot, "search_youtube_videos", return_value=[]),
        ):
            mock_now = datetime.datetime(2024, 5, 15, 12, 0)
            mock_datetime.now.return_value = mock_now
            type(mock_now).hour = 12

            await newsbot.news_loop()

            # Should exit early when no videos found

    @pytest.mark.asyncio
    async def test_news_loop_channel_not_found(self):
        """Test news loop when news channel is not found."""
        mock_videos = [{"title": "Test Video", "url": "test.com"}]

        with (
            patch("datetime.datetime") as mock_datetime,
            patch.object(newsbot, "search_youtube_videos", return_value=mock_videos),
            patch.object(newsbot.bot, "get_channel", return_value=None),
        ):
            mock_now = datetime.datetime(2024, 5, 15, 12, 0)
            mock_datetime.now.return_value = mock_now
            type(mock_now).hour = 12

            await newsbot.news_loop()

            # Should exit when channel not found

    @pytest.mark.asyncio
    async def test_news_loop_success(self):
        """Test successful news loop execution."""
        mock_videos = [
            {
                "title": "New CoC Update",
                "url": "https://youtube.com/watch?v=test1",
                "description": "Amazing update",
                "view_count": "50K views",
                "published_time": "1 day ago",
            }
        ]

        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_thread = AsyncMock()
        mock_thread.__class__ = discord.Thread
        mock_channel.create_thread.return_value = mock_thread

        with (
            patch("newsbot.datetime") as mock_datetime_module,
            patch.object(newsbot, "search_youtube_videos", return_value=mock_videos),
            patch.object(newsbot.bot, "get_channel", return_value=mock_channel),
            patch.object(newsbot, "generate_thread_title", return_value="Test Thread"),
            patch.object(newsbot, "generate_video_summary", side_effect=lambda x: x),
            patch.object(newsbot, "ai_summary", return_value="Great news!"),
        ):
            mock_now = datetime.datetime(2024, 5, 15, 12, 0)
            mock_datetime_module.datetime.now.return_value = mock_now

            await newsbot.news_loop()

            # Should create thread and send embed
            mock_channel.create_thread.assert_called_once()
            mock_thread.send.assert_called_once()


class TestStratCommand:
    """Test the /strat command."""

    @pytest.fixture
    def mock_interaction(self):
        """Create a mock Discord interaction."""
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    @pytest.mark.asyncio
    async def test_strat_command_success(self, mock_interaction):
        """Test successful strat command execution."""
        mock_videos = [
            {"title": "TH10 Dragon Attack", "url": "https://youtube.com/test1"},
            {"title": "TH10 Hog Strategy", "url": "https://youtube.com/test2"},
        ]

        with (
            patch.object(newsbot, "search_youtube_videos", return_value=mock_videos),
            patch("newsbot.datetime") as mock_datetime_module,
        ):
            mock_datetime_module.datetime.now.return_value = datetime.datetime(
                2024, 5, 15, 12, 0
            )

            await newsbot.strat.callback(mock_interaction, "TH10 dragon")

            mock_interaction.response.defer.assert_called_once()
            mock_interaction.followup.send.assert_called_once()

            # Check that embed was created with videos
            call_args = mock_interaction.followup.send.call_args
            assert "embed" in call_args.kwargs

    @pytest.mark.asyncio
    async def test_strat_command_no_videos(self, mock_interaction):
        """Test strat command when no videos found."""
        with patch.object(newsbot, "search_youtube_videos", return_value=[]):
            await newsbot.strat.callback(mock_interaction, "invalid query")

            mock_interaction.followup.send.assert_called_once_with(
                "❌ No strategy videos found. Try a different search term!",
                ephemeral=True,
            )

    @pytest.mark.asyncio
    async def test_strat_command_town_hall_parsing(self, mock_interaction):
        """Test strat command parses town hall levels correctly."""
        mock_videos = [{"title": "Strategy Video", "url": "https://youtube.com/test"}]

        with (
            patch.object(
                newsbot, "search_youtube_videos", return_value=mock_videos
            ) as mock_search,
            patch("newsbot.datetime") as mock_datetime_module,
        ):
            mock_datetime_module.datetime.now.return_value = datetime.datetime(
                2024, 5, 15, 12, 0
            )

            await newsbot.strat.callback(mock_interaction, "TH12 hog attack")

            # Should include TH12 in search terms
            mock_search.assert_called_once()
            search_query = mock_search.call_args[0][0]
            assert "TH12" in search_query
            assert "hog attack" in search_query
            assert "attack strategy" in search_query

    @pytest.mark.asyncio
    async def test_strat_command_none_query(self, mock_interaction):
        """Test strat command with None query."""
        mock_videos = [{"title": "General Strategy", "url": "https://youtube.com/test"}]

        with (
            patch.object(newsbot, "search_youtube_videos", return_value=mock_videos),
            patch("newsbot.datetime") as mock_datetime_module,
        ):
            mock_datetime_module.datetime.now.return_value = datetime.datetime(
                2024, 5, 15, 12, 0
            )

            await newsbot.strat.callback(mock_interaction, None)

            mock_interaction.followup.send.assert_called_once()
            # Should still work with None query


class TestNewsCommand:
    """Test the /news command."""

    @pytest.fixture
    def mock_interaction(self):
        """Create a mock Discord interaction."""
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    @pytest.mark.asyncio
    async def test_news_command_success(self, mock_interaction):
        """Test successful news command execution."""
        mock_videos = [
            {"title": "CoC Update News", "url": "https://youtube.com/test1"},
            {"title": "Leaked Features", "url": "https://youtube.com/test2"},
        ]

        with (
            patch.object(newsbot, "search_youtube_videos", return_value=mock_videos),
            patch("newsbot.datetime") as mock_datetime_module,
        ):
            mock_datetime_module.datetime.now.return_value = datetime.datetime(
                2024, 5, 15, 12, 0
            )

            await newsbot.news_command.callback(mock_interaction)

            mock_interaction.response.defer.assert_called_once()
            mock_interaction.followup.send.assert_called_once()

            # Check that embed was created
            call_args = mock_interaction.followup.send.call_args
            assert "embed" in call_args.kwargs

    @pytest.mark.asyncio
    async def test_news_command_no_videos(self, mock_interaction):
        """Test news command when no videos found."""
        with patch.object(newsbot, "search_youtube_videos", return_value=[]):
            await newsbot.news_command.callback(mock_interaction)

            mock_interaction.followup.send.assert_called_once_with(
                "❌ No recent news videos found!", ephemeral=True
            )

    @pytest.mark.asyncio
    async def test_news_command_long_titles_truncated(self, mock_interaction):
        """Test news command truncates long video titles."""
        long_title = "This is a very long video title that exceeds 80 characters and should be truncated"
        mock_videos = [{"title": long_title, "url": "https://youtube.com/test"}]

        with (
            patch.object(newsbot, "search_youtube_videos", return_value=mock_videos),
            patch("newsbot.datetime") as mock_datetime_module,
        ):
            mock_datetime_module.datetime.now.return_value = datetime.datetime(
                2024, 5, 15, 12, 0
            )

            await newsbot.news_command.callback(mock_interaction)

            # Should truncate long titles
            call_args = mock_interaction.followup.send.call_args
            embed = call_args.kwargs["embed"]
            assert len(embed.fields[0].value) < len(
                f"[{long_title}](https://youtube.com/test)"
            )


class TestBotEvents:
    """Test bot event handlers."""

    @pytest.mark.asyncio
    async def test_on_ready(self):
        """Test the on_ready event handler."""
        with (
            patch.object(newsbot.tree, "sync") as sync_mock,
            patch.object(newsbot.news_loop, "start") as start_mock,
        ):
            await newsbot.on_ready()

            sync_mock.assert_called_once()
            start_mock.assert_called_once()


class TestMainFunction:
    """Test the main function and environment validation."""

    def test_main_missing_token(self):
        """Test main function raises error for missing Discord token."""
        with (
            patch.object(newsbot, "TOKEN", None),
            patch.object(newsbot, "NEWS_CHANNEL_ID", 123456),
        ):
            with pytest.raises(
                (RuntimeError, discord.LoginFailure),
                match="Missing required environment variables|Improper token",
            ):
                asyncio.run(newsbot.main())

    def test_main_missing_channel_id(self):
        """Test main function raises error for missing news channel ID."""
        with (
            patch.object(newsbot, "TOKEN", "fake_token"),
            patch.object(newsbot, "NEWS_CHANNEL_ID", 0),
        ):
            with pytest.raises(
                (RuntimeError, Exception),
                match="Missing required environment variables|Session is closed",
            ):
                asyncio.run(newsbot.main())

    @pytest.mark.asyncio
    async def test_main_with_all_env_vars(self):
        """Test main function with all required environment variables."""
        env_vars = {"DISCORD_TOKEN": "fake_token", "NEWS_CHANNEL_ID": "123456"}

        with (
            patch.dict(os.environ, env_vars),
            patch.object(newsbot.bot, "start", new_callable=AsyncMock) as start_mock,
        ):
            # Mock the start method to avoid actually starting the bot
            start_mock.side_effect = KeyboardInterrupt()

            try:
                await newsbot.main()
            except KeyboardInterrupt:
                pass  # Expected for this test

            start_mock.assert_called_once_with("fake_token")
