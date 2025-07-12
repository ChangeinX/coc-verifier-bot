# Clash of Clans Verification Bot

This bot verifies Discord members are part of a specific Clash of Clans clan. It can also remove members who leave the clan.

## Setup

1. Create a Discord bot and get its token.
2. Obtain a Clash of Clans API token from [developer.clashofclans.com](https://developer.clashofclans.com/).
3. Invite the bot to your server with the `Manage Roles` and `Kick Members` permissions.
4. Create a role that will be granted to verified members and note its ID.

Set the following environment variables before running:

- `DISCORD_TOKEN` – your Discord bot token.
- `COC_API_TOKEN` – Clash of Clans API token.
- `CLAN_TAG` – tag of your clan (e.g. `#ABCD123`).
- `VERIFIED_ROLE_ID` – ID of the role to grant verified members.
- `ADMIN_LOG_CHANNEL_ID` – (optional) channel ID to log verifications.
- `DATA_FILE` – (optional) path to store verified user tags, default `verified.json`.
- `CHECK_INTERVAL` – (optional) seconds between membership checks, default `3600`.
- `KICK_ON_LEAVE` – (optional) set to `false` to disable kicking members who leave the clan.

Install dependencies and run:

```bash
pip install -U discord.py aiohttp
python bot.py
```

Use `/verify <player tag>` in your Discord server to verify a member.

