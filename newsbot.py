import asyncio
import datetime
import json
import logging
import os
import re
import urllib.parse

import discord
import requests
from discord import app_commands
from discord.ext import tasks
from openai import OpenAI

TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
NEWS_CHANNEL_ID = int(os.getenv("NEWS_CHANNEL_ID"))

# Keep OpenAI client for future integration
client = OpenAI(api_key=OPENAI_KEY)

intents = discord.Intents.default()
intents.guilds = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# -------- utility functions ---------


def parse_town_hall(text: str) -> int | None:
    """Parse town hall level from text."""
    if not text:
        return None
    t = text.lower().replace(" ", "")
    m = re.search(r"(?:th|townhall)?(\d+)", t)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def search_youtube_videos(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube for Clash of Clans videos and return video data."""
    videos = []

    try:
        # Create search query
        search_query = urllib.parse.quote(f"{query} clash of clans")
        url = f"https://www.youtube.com/results?search_query={search_query}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            content = resp.text

            # Extract video data from YouTube's embedded JSON
            start_marker = "var ytInitialData = "
            start_idx = content.find(start_marker)
            if start_idx != -1:
                start_idx += len(start_marker)
                end_idx = content.find(";</script>", start_idx)
                if end_idx != -1:
                    json_str = content[start_idx:end_idx]
                    try:
                        data = json.loads(json_str)
                        contents = (
                            data.get("contents", {})
                            .get("twoColumnSearchResultsRenderer", {})
                            .get("primaryContents", {})
                            .get("sectionListRenderer", {})
                            .get("contents", [])
                        )

                        for section in contents:
                            items = section.get("itemSectionRenderer", {}).get(
                                "contents", []
                            )
                            for item in items:
                                if "videoRenderer" in item:
                                    video = item["videoRenderer"]
                                    title = (
                                        video.get("title", {})
                                        .get("runs", [{}])[0]
                                        .get("text", "")
                                    )
                                    video_id = video.get("videoId", "")

                                    # Extract description snippet
                                    description = ""
                                    description_snippet = video.get(
                                        "descriptionSnippet"
                                    )
                                    if (
                                        description_snippet
                                        and "runs" in description_snippet
                                    ):
                                        description_parts = [
                                            run.get("text", "")
                                            for run in description_snippet["runs"]
                                        ]
                                        description = "".join(description_parts)

                                    # Extract view count and published time for additional context
                                    view_count = ""
                                    published_time = ""

                                    view_count_text = video.get("viewCountText")
                                    if (
                                        view_count_text
                                        and "simpleText" in view_count_text
                                    ):
                                        view_count = view_count_text["simpleText"]

                                    published_time_text = video.get("publishedTimeText")
                                    if (
                                        published_time_text
                                        and "simpleText" in published_time_text
                                    ):
                                        published_time = published_time_text[
                                            "simpleText"
                                        ]

                                    if title and video_id and len(title) > 10:
                                        videos.append(
                                            {
                                                "title": title,
                                                "description": description,
                                                "url": f"https://www.youtube.com/watch?v={video_id}",
                                                "id": video_id,
                                                "view_count": view_count,
                                                "published_time": published_time,
                                            }
                                        )
                                        if len(videos) >= max_results:
                                            break
                                if len(videos) >= max_results:
                                    break
                    except json.JSONDecodeError:
                        logging.warning("Failed to parse YouTube JSON data")

        # Fallback if scraping fails
        if not videos:
            fallback_titles = [
                f"{query.title()} - Clash of Clans Guide",
                f"Best {query} Strategy - CoC Tutorial",
                f"{query} Attack Tips - Clash of Clans",
            ]
            for i, title in enumerate(fallback_titles[:max_results]):
                videos.append(
                    {
                        "title": title,
                        "description": f"A comprehensive guide about {query} in Clash of Clans",
                        "url": f"https://www.youtube.com/results?search_query={urllib.parse.quote(title)}",
                        "id": f"fallback_{i}",
                        "view_count": "",
                        "published_time": "",
                    }
                )

    except Exception as exc:
        logging.warning("YouTube search failed: %s", exc)
        # Provide basic fallback
        videos.append(
            {
                "title": f"{query.title()} - Clash of Clans",
                "description": f"Content related to {query} in Clash of Clans",
                "url": f"https://www.youtube.com/results?search_query={urllib.parse.quote(query + ' clash of clans')}",
                "id": "fallback",
                "view_count": "",
                "published_time": "",
            }
        )

    return videos


def ai_summary(prompt: str) -> str | None:
    """Generate AI summary using OpenAI."""
    try:
        res = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        return res.choices[0].message.content.strip()
    except Exception as exc:
        logging.warning("OpenAI error: %s", exc)
        return None


def generate_thread_title(videos: list[dict]) -> str:
    """Generate an engaging thread title based on video content using AI."""
    if not videos:
        return f"CoC News {datetime.datetime.now():%m-%d %H:%M}"

    # Collect video titles for AI processing
    video_titles = [video["title"] for video in videos[:3]]

    prompt = f"""Based on these Clash of Clans YouTube video titles, create a short, engaging thread title (max 40 characters):

Video titles:
{chr(10).join(f"- {title}" for title in video_titles)}

The thread title should:
- Be exciting and clickable
- Mention the main topic (update, news, leak, etc.)
- Be under 40 characters
- Not include "CoC News" prefix

Examples: "🔥 Major Update Incoming!", "⚡ Town Hall 16 Leaked!", "💥 Balance Changes Drop!"

Just respond with the title only, no quotes or extra text."""

    ai_title = ai_summary(prompt)
    if ai_title and len(ai_title) <= 40:
        return ai_title

    # Fallback to a better default
    now = datetime.datetime.now()
    if any("update" in title.lower() for title in video_titles):
        return f"🔥 CoC Update News {now:%m-%d}"
    elif any("leak" in title.lower() for title in video_titles):
        return f"⚡ CoC Leaks & News {now:%m-%d}"
    else:
        return f"📰 CoC News {now:%m-%d}"


def generate_video_summary(video: dict) -> dict:
    """Generate an enhanced title and summary for a video using AI."""
    if not video.get("description") and not video.get("title"):
        return video

    prompt = f"""Based on this Clash of Clans YouTube video information, create:
1. An engaging, shorter title (max 60 characters) that captures the essence
2. A 2-sentence summary of what the video covers

Video Title: {video["title"]}
Description: {video.get("description", "No description available")}

Format your response as:
TITLE: [your title here]
SUMMARY: [your 2-sentence summary here]

Keep it exciting and informative for CoC players."""

    ai_response = ai_summary(prompt)

    if ai_response:
        try:
            lines = ai_response.split("\n")
            title_line = next(
                (line for line in lines if line.startswith("TITLE:")), None
            )
            summary_line = next(
                (line for line in lines if line.startswith("SUMMARY:")), None
            )

            enhanced_video = video.copy()

            if title_line:
                enhanced_title = title_line.replace("TITLE:", "").strip()
                if len(enhanced_title) <= 60 and enhanced_title:
                    enhanced_video["enhanced_title"] = enhanced_title

            if summary_line:
                enhanced_summary = summary_line.replace("SUMMARY:", "").strip()
                if enhanced_summary:
                    enhanced_video["enhanced_summary"] = enhanced_summary

            return enhanced_video
        except Exception as e:
            logging.warning(f"Failed to parse AI response: {e}")

    return video


@tasks.loop(hours=24)
async def news_loop() -> None:
    """Post YouTube videos about latest Clash of Clans news."""
    now = datetime.datetime.now()

    # Skip during night hours (2 AM to 8 AM)
    if 2 <= now.hour < 8:
        logging.info("Skipping news post during night hours")
        return

    # Search for latest CoC news videos
    videos = search_youtube_videos("clash of clans update news", max_results=3)

    if not videos:
        logging.info("No recent videos found")
        return

    channel = bot.get_channel(NEWS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        logging.warning("News channel not found or not a text channel")
        return

    # Generate an engaging thread title using AI
    thread_title = generate_thread_title(videos)

    # Create thread for news
    thread = await channel.create_thread(
        name=thread_title,
        type=discord.ChannelType.public_thread,
    )

    # Generate AI-enhanced descriptions for each video
    enhanced_videos = []
    for video in videos:
        enhanced_video = generate_video_summary(video)
        enhanced_videos.append(enhanced_video)

    # Create overall summary for embed description
    all_titles = [v["title"] for v in videos]
    overview_prompt = f"""Based on these Clash of Clans news video titles, write a brief, exciting overview (max 100 characters) for the embed description:

{chr(10).join(f"- {title}" for title in all_titles)}

Make it sound exciting and encourage clicking. Examples:
"🔥 Major updates and balance changes revealed!"
"⚡ Exclusive leaks and upcoming features!"
"💥 Game-changing announcements inside!"

Just respond with the description only."""

    ai_description = ai_summary(overview_prompt)
    embed_description = (
        ai_description
        if ai_description and len(ai_description) <= 100
        else "Check out these recent videos about CoC updates and news!"
    )

    # Post the videos
    embed = discord.Embed(
        title="🎮 Latest Clash of Clans News",
        description=embed_description,
        color=0x00FF00,
        timestamp=now,
    )

    for _i, video in enumerate(enhanced_videos, 1):
        # Use enhanced title if available, otherwise original
        display_title = video.get("enhanced_title", video["title"])

        # Create field value with enhanced summary if available
        if video.get("enhanced_summary"):
            field_value = (
                f"**[{display_title}]({video['url']})**\n{video['enhanced_summary']}"
            )
        else:
            field_value = f"[{display_title}]({video['url']})"

        # Add view count and publish time if available
        metadata = []
        if video.get("view_count"):
            metadata.append(f"👁️ {video['view_count']}")
        if video.get("published_time"):
            metadata.append(f"📅 {video['published_time']}")

        if metadata:
            field_value += f"\n*{' • '.join(metadata)}*"

        embed.add_field(name="📺", value=field_value, inline=False)

    embed.set_footer(text="🔔 Stay updated with the latest CoC news!")

    await thread.send(embed=embed)
    logging.info(f"Posted news videos to thread: {thread_title}")


# -------- commands ---------


@tree.command(name="strat", description="Get attack strategy videos from YouTube")
@app_commands.describe(
    query="Search terms like 'TH10 dragon attack' or 'hog rider strategy'"
)
async def strat(interaction: discord.Interaction, query: str | None = None) -> None:
    """Get attack strategy videos from YouTube."""
    await interaction.response.defer()

    # Parse town hall level if provided
    th = parse_town_hall(query or "")

    # Build search query
    search_terms = []
    if th:
        search_terms.append(f"TH{th}")
    if query:
        search_terms.append(query)
    search_terms.append("attack strategy")

    search_query = " ".join(search_terms)

    # Search for strategy videos
    videos = search_youtube_videos(search_query, max_results=5)

    if not videos:
        await interaction.followup.send(
            "❌ No strategy videos found. Try a different search term!", ephemeral=True
        )
        return

    # Create embed with strategy videos
    embed = discord.Embed(
        title="⚔️ Attack Strategy Videos",
        description=f"Strategy videos for: **{query or 'General attacks'}**"
        + (f" (Town Hall {th})" if th else ""),
        color=0xFF6B35,
        timestamp=datetime.datetime.now(),
    )

    for _i, video in enumerate(videos, 1):
        title = video["title"]
        if len(title) > 80:
            display = f"{title[:77]}..."
        else:
            display = title
        embed.add_field(
            name=f"📹 Strategy {_i}",
            value=f"[{display}]({video['url']})",
            inline=False,
        )

    embed.set_footer(text="Click the links to watch on YouTube")

    await interaction.followup.send(embed=embed)


@tree.command(name="news", description="Get latest Clash of Clans news videos")
async def news_command(interaction: discord.Interaction) -> None:
    """Get latest CoC news videos on demand."""
    await interaction.response.defer()

    videos = search_youtube_videos("clash of clans update news leak", max_results=5)

    if not videos:
        await interaction.followup.send(
            "❌ No recent news videos found!", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="📰 Latest Clash of Clans News",
        description="Recent news and update videos from YouTube",
        color=0x1E90FF,
        timestamp=datetime.datetime.now(),
    )

    for _i, video in enumerate(videos, 1):
        title = video["title"]
        if len(title) > 80:
            display = f"{title[:77]}..."
        else:
            display = title
        embed.add_field(
            name="🎬",
            value=f"[{display}]({video['url']})",
            inline=False,
        )

    embed.set_footer(text="Stay updated with the latest CoC news!")

    await interaction.followup.send(embed=embed)


# -------- lifecycle ---------


@bot.event
async def on_ready() -> None:
    """Bot startup event."""
    await tree.sync()
    news_loop.start()
    logging.info("News bot ready as %s", bot.user)


async def main() -> None:
    """Main function to start the bot."""
    if not TOKEN or not NEWS_CHANNEL_ID:
        raise RuntimeError(
            "Missing required environment variables (DISCORD_TOKEN, NEWS_CHANNEL_ID)"
        )

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
