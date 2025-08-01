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

TH_LEVELS = list(range(2, 18))
_current_index = 0


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
    url = "https://www.reddit.com/r/ClashOfClans/search.json"
    params = {"q": query, "restrict_sr": "on", "sort": "new", "limit": 5}
    headers = {"User-Agent": "coc-news-bot/1.0"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("Search failed: %s", exc)
        return []
    posts: List[str] = []
    for item in data.get("data", {}).get("children", []):
        d = item.get("data", {})
        if d.get("over_18") or d.get("score", 0) < 5:
            continue
        posts.append(d.get("title", "")[:200])
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
    global _current_index  # pylint: disable=global-statement
    now = datetime.datetime.now()
    if 3 <= now.hour < 10:
        return
    th = TH_LEVELS[_current_index % len(TH_LEVELS)]
    _current_index += 1
    posts = search_posts(f"Town Hall {th} news")
    if not posts:
        return
    prompt = (
        f"Using the following headlines about Town Hall {th} from Reddit, "
        f"share one interesting fact or update:\n- " + "\n- ".join(posts)
    )
    summary = ai_summary(prompt)
    if not summary:
        return
    channel = bot.get_channel(NEWS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return
    thread = await channel.create_thread(
        name=f"TH{th} facts {now:%m-%d %H:%M}", type=discord.ChannelType.public_thread
    )
    await thread.send(summary)


# -------- commands ---------

@tree.command(name="strat", description="Get war strategies for a Town Hall level")
@app_commands.describe(town_hall="Town Hall level, e.g. TH10 or Town Hall 10")
async def strat(interaction: discord.Interaction, town_hall: str) -> None:
    th = parse_town_hall(town_hall)
    if not th:
        await interaction.response.send_message(
            "Could not understand that Town Hall level.", ephemeral=True
        )
        return
    await interaction.response.defer()
    posts = search_posts(f"Town Hall {th} attack strategy")
    context = "\n".join(posts)
    prompt = (
        f"I'm a Town Hall {th} player in Clash of Clans. "
        f"What war attack strategies are currently strong? "
        f"Use these search snippets for context:\n{context}"
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
