# Discord Setup Guide

A single **unified Discord bot** now provides verification, giveaway, and tournament
functionality. Legacy entry points (`bot.py`, `giveawaybot.py`, `tournamentbot.py`)
are still available for tests and troubleshooting but new deployments should run
`python -m bots.unified`.

## 1. Discord application and permissions
1. Visit <https://discord.com/developers/applications> and create **one** application for
   the unified bot.
2. In the **Bot** tab, add a bot user, copy the **Bot Token**, and enable
   **Server Members Intent**.
3. Invite the bot with `bot` and `applications.commands` scopes and grant:
   - Manage Roles, Kick Members (verification workflows)
   - Send Messages / Manage Messages (giveaway announcements)
   - Administrator or equivalent for tournament staff running `/setup`.

## 2. Required server configuration
- Create the role granted to verified members (for example **Verified**) and record
  its ID (enable *Developer Mode* → right click → Copy ID).
- Decide which channel should receive giveaway announcements and (optionally) the
  shadow-mode mirror of actions so admins can review activity before enabling
  production writes.

## 3. Environment variables for the unified bot
Set the following environment variables before starting the bot container or
local process. All values are strings unless noted.

| Variable | Purpose |
| --- | --- |
| `DISCORD_TOKEN` | Token for the unified Discord application. |
| `COC_EMAIL`, `COC_PASSWORD` | Clash of Clans API credentials shared by all features. |
| `CLAN_TAG` | Primary clan tag validated by verification checks. |
| `FEEDER_CLAN_TAG` | Optional feeder clan tag permitted for verification. |
| `VERIFIED_ROLE_ID` | Discord role ID granted after successful verification. |
| `ADMIN_LOG_CHANNEL_ID` | Optional channel used by the verification bot for audit logs. |
| `DDB_TABLE_NAME` | DynamoDB table for verification records. |
| `GIVEAWAY_CHANNEL_ID` | Channel where giveaway announcements are posted. |
| `GIVEAWAY_TABLE_NAME` | DynamoDB table for giveaway metadata and entries. |
| `GIVEAWAY_TEST` | `true` to shorten giveaway timers (useful in staging). |
| `TOURNAMENT_TABLE_NAME` | DynamoDB table storing tournament configuration and registrations. |
| `TOURNAMENT_REGISTRATION_CHANNEL_ID` | Channel receiving tournament registration announcements. |
| `AWS_REGION` | AWS region for DynamoDB and CloudWatch (defaults to `us-east-1`). |
| `SHADOW_MODE` | When `true` (default), actions are mirrored to a log channel without mutating guild state. |
| `SHADOW_CHANNEL_ID` | Channel receiving shadow-mode reports. If unset, messages are logged. |

The unified runtime intentionally defaults to `SHADOW_MODE=true` so changes can be
observed in the log channel before enabling live mutations. Set `SHADOW_MODE=false`
when you are ready to replace the legacy services.

## 4. Running locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# Unified runtime (recommended)
DISCORD_TOKEN=... COC_EMAIL=... COC_PASSWORD=... \
GIVEAWAY_CHANNEL_ID=... GIVEAWAY_TABLE_NAME=... \
TOURNAMENT_TABLE_NAME=... TOURNAMENT_REGISTRATION_CHANNEL_ID=... \
python -m bots.unified

# Legacy shims remain available for targeted testing
DISCORD_TOKEN=... python bot.py
DISCORD_TOKEN=... python giveawaybot.py
DISCORD_TOKEN=... python tournamentbot.py
```

To run inside Docker, build the consolidated image and pass the same environment
variables (or provide an `.env` file):

```bash
docker build -t coc-unified-bot .
docker run --env-file local.env coc-unified-bot
```

## 5. Feature overview
- **Verification**
  - `/verify <player_tag>` – verify a Discord member against the configured clan(s).
  - `/whois @member` – look up the stored in-game name for a member.
  - `/recruited` – post a recruit announcement with player tag and source.
  - Automatic membership checks remove former members after an approval workflow.

- **Giveaway**
  - Persistent giveaway buttons with fairness tracking and daily maintenance.
  - Scheduled gold pass and weekly gift card giveaways (configurable in code).

- **Tournament**
  - `/setup`, `/registerteam`, `/create-bracket`, `/select-round-winner`, and
    `/simulate-tourney` cover the full tournament workflow.

When `SHADOW_MODE=true`, each feature reports the actions it *would* take in
`SHADOW_CHANNEL_ID` (falling back to structured logs). Disable shadow mode once
behavior looks correct to promote the unified bot to production.
