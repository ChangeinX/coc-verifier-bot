import os
from typing import Final

DISCORD_TOKEN: Final[str | None] = os.getenv("DISCORD_TOKEN")
COC_EMAIL: Final[str | None] = os.getenv("COC_EMAIL")
COC_PASSWORD: Final[str | None] = os.getenv("COC_PASSWORD")
CLAN_TAG: Final[str | None] = os.getenv("CLAN_TAG")
VERIFIED_ROLE_ID: Final[int] = int(os.getenv("VERIFIED_ROLE_ID", "0"))
ADMIN_LOG_CHANNEL_ID: Final[int] = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0"))
DDB_TABLE_NAME: Final[str | None] = os.getenv("DDB_TABLE_NAME")
AWS_REGION: Final[str] = os.getenv("AWS_REGION", "us-east-1")
OPENAI_API_KEY: Final[str | None] = os.getenv("OPENAI_API_KEY")

REQUIRED_VARS = (
    "DISCORD_TOKEN",
    "COC_EMAIL",
    "COC_PASSWORD",
    "CLAN_TAG",
    "VERIFIED_ROLE_ID",
    "DDB_TABLE_NAME",
    "OPENAI_API_KEY",
)
