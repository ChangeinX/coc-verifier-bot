import asyncio
import datetime
import logging
import os
import re
from typing import List

import discord
import openai
import requests
from discord import app_commands
from discord.ext import tasks

TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
NEWS_CHANNEL_ID = int(os.getenv("NEWS_CHANNEL_ID", "0"))

openai.api_key = OPENAI_KEY

intents = discord.Intents.default()
intents.guilds = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# -------- utility functions ---------

def parse_town_hall(text: str) -> int | None:
    t = text.lower().replace(" ", "")
    m = re.search(r"(?:th|townhall)?(\d+)", t)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def search_posts(query: str) -> List[str]:
    """Return recent high-score Reddit post titles using Pushshift."""
    url = "https://api.pushshift.io/reddit/search/submission/"
    params = {
        "q": query,
        "subreddit": "ClashOfClans",
        "sort": "desc",
        "sort_type": "score",
        "size": 5,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("Search failed: %s", exc)
        return []
    posts: List[str] = []
    for item in data.get("data", []):
        if item.get("over_18") or item.get("score", 0) < 5:
            continue
        title = item.get("title", "")
        if title:
            posts.append(title[:200])
    return posts


def ai_summary(prompt: str) -> str | None:
    try:
        res = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        return res.choices[0].message["content"].strip()
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("OpenAI error: %s", exc)
        return None


# -------- scheduled posts ---------

@tasks.loop(hours=2)
async def news_loop() -> None:
    """Post a short news blurb from recent Reddit headlines."""
    now = datetime.datetime.now()
    if 3 <= now.hour < 10:
        return
    posts = search_posts("clash of clans update")
    if not posts:
        return
    prompt = (
        "Using the following Clash of Clans headlines from Reddit, "
        "share one fun fact or update to get players talking:\n- "
        + "\n- ".join(posts)
    )
    summary = ai_summary(prompt)
    if not summary:
        return
    channel = bot.get_channel(NEWS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return
    thread = await channel.create_thread(
        name=f"CoC news {now:%m-%d %H:%M}",
        type=discord.ChannelType.public_thread,
    )
    await thread.send(summary)


# -------- commands ---------

@tree.command(name="strat", description="Get war strategies")
@app_commands.describe(query="Optional keywords like TH10 or air attack")
async def strat(interaction: discord.Interaction, query: str | None = None) -> None:
    th = parse_town_hall(query or "")
    await interaction.response.defer()
    posts = search_posts(f"{query or ''} attack strategy")
    context = "\n".join(posts)
    prompt = (
        "What are some effective Clash of Clans war strategies"
        + (f" for Town Hall {th}" if th else "")
        + "? Use these community snippets for context:\n"
        + context
    )
    summary = ai_summary(prompt)
    if not summary:
        await interaction.followup.send("Failed to get strategies.", ephemeral=True)
        return
    await interaction.followup.send(summary)


# -------- lifecycle ---------

@bot.event
async def on_ready() -> None:
    await tree.sync()
    news_loop.start()
    logging.info("News bot ready as %s", bot.user)


async def main() -> None:
    if not TOKEN or not OPENAI_KEY or not NEWS_CHANNEL_ID:
        raise RuntimeError("Missing required environment variables")
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
