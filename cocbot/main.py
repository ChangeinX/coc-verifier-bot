import asyncio
import logging
import os

from .config import REQUIRED_VARS, DISCORD_TOKEN, COC_EMAIL, COC_PASSWORD
from .clients import bot, coc_client, tree
from . import commands  # noqa: F401
from .tasks import membership_check

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("coc-gateway")


@bot.event
async def on_ready():
    await tree.sync()
    await coc_client.login(COC_EMAIL, COC_PASSWORD)
    membership_check.start()
    log.info("Bot ready as %s (%s)", bot.user, bot.user.id)


async def main() -> None:
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
