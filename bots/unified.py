"""Unified Discord bot runtime that composes verification, giveaway, and tournament features."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Sequence

import boto3
import coc
import discord
from discord import app_commands

from bots.config import read_shadow_config
from bots.shadow import ShadowReporter
from bots import verification, giveaway, tournament

log = logging.getLogger("coc-unified")


@dataclass(slots=True)
class EnvironmentConfig:
    discord_token: str
    coc_email: str
    coc_password: str
    clan_tag: str
    feeder_clan_tag: str | None
    verified_role_id: str
    admin_log_channel_id: str | None
    giveaway_channel_id: str
    giveaway_table_name: str
    giveaway_test_mode: bool
    tournament_table_name: str
    tournament_registration_channel_id: str | None
    verification_table_name: str

    @classmethod
    def load(cls) -> "EnvironmentConfig":
        missing: list[str] = []

        def need(name: str) -> str:
            value = os.getenv(name)
            if not value:
                missing.append(name)
                return ""
            return value

        discord_token = need("DISCORD_TOKEN")
        coc_email = need("COC_EMAIL")
        coc_password = need("COC_PASSWORD")
        clan_tag = need("CLAN_TAG")
        verified_role_id = need("VERIFIED_ROLE_ID")
        giveaway_channel_id = need("GIVEAWAY_CHANNEL_ID")
        giveaway_table_name = need("GIVEAWAY_TABLE_NAME")
        tournament_table_name = need("TOURNAMENT_TABLE_NAME")
        verification_table_name = need("DDB_TABLE_NAME")

        if missing:
            raise RuntimeError("Missing env vars: " + ", ".join(sorted(set(missing))))

        feeder_clan_tag = os.getenv("FEEDER_CLAN_TAG") or None
        admin_log_channel_id = os.getenv("ADMIN_LOG_CHANNEL_ID") or None
        giveaway_test_mode = os.getenv("GIVEAWAY_TEST", "false").lower() in {"1", "true", "yes"}
        tournament_registration_channel_id = (
            os.getenv("TOURNAMENT_REGISTRATION_CHANNEL_ID") or None
        )

        return cls(
            discord_token=discord_token,
            coc_email=coc_email,
            coc_password=coc_password,
            clan_tag=clan_tag,
            feeder_clan_tag=feeder_clan_tag,
            verified_role_id=verified_role_id,
            admin_log_channel_id=admin_log_channel_id,
            giveaway_channel_id=giveaway_channel_id,
            giveaway_table_name=giveaway_table_name,
            giveaway_test_mode=giveaway_test_mode,
            tournament_table_name=tournament_table_name,
            tournament_registration_channel_id=tournament_registration_channel_id,
            verification_table_name=verification_table_name,
        )


class UnifiedRuntime:
    def __init__(self, config: EnvironmentConfig) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True

        self.config = config
        self.bot = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.bot)
        self.shadow_config = read_shadow_config(default_enabled=True)
        self.shadow_reporter = ShadowReporter(self.bot, self.shadow_config)
        self.dynamodb = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "us-east-1"))
        self.coc_client: coc.Client | None = None

    def configure_features(self) -> None:
        shadow_kwargs = dict(
            shadow_enabled=self.shadow_config.enabled,
            shadow_channel_id=self.shadow_config.channel_id,
        )

        verification.configure_runtime(
            client=self.bot,
            command_tree=self.tree,
            dynamodb_resource=self.dynamodb,
            table_name=self.config.verification_table_name,
            coc_client_override=self.coc_client,
            **shadow_kwargs,
        )

        giveaway.configure_runtime(
            client=self.bot,
            command_tree=self.tree,
            dynamodb_resource=self.dynamodb,
            giveaway_table=self.config.giveaway_table_name,
            verification_table=self.config.verification_table_name,
            coc_client_override=self.coc_client,
            test_mode=self.config.giveaway_test_mode,
            **shadow_kwargs,
        )

        tournament.configure_runtime(
            client=self.bot,
            command_tree=self.tree,
            dynamodb_resource=self.dynamodb,
            table_name=self.config.tournament_table_name,
            coc_client_override=self.coc_client,
            registration_channel_id=(
                int(self.config.tournament_registration_channel_id)
                if self.config.tournament_registration_channel_id
                else None
            ),
            **shadow_kwargs,
        )

    async def run(self) -> None:
        self.configure_features()

        if not self.shadow_config.enabled:
            self.coc_client = coc.Client()
            await self.coc_client.login(self.config.coc_email, self.config.coc_password)
            # Reconfigure features with active CoC client
            self.configure_features()
        else:
            log.info("Unified bot running in SHADOW mode")

        async with self.bot:
            await self.bot.start(self.config.discord_token)

    @classmethod
    def create(cls) -> "UnifiedRuntime":
        config = EnvironmentConfig.load()
        runtime = cls(config)
        return runtime


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    runtime = UnifiedRuntime.create()
    await runtime.run()


__all__ = ["UnifiedRuntime", "main"]
