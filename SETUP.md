# Discord Setup Guide

This project ships multiple Discord bots:

- `bot.py` - verification bot that links Discord users to their Clash of Clans profile.
- `giveawaybot.py` - giveaway orchestration with fairness controls.
- `tournamentbot.py` - tournament registration workflow replacing the old Google Form process.

The sections below explain how to configure the applications and the environment for local development or container deployments.

## 1. Create Discord applications
1. Visit <https://discord.com/developers/applications> and create an application for each bot you plan to run (verification, giveaway, tournament).
2. In the **Bot** tab, add a bot user and copy the **Bot Token**.
3. Enable the **Server Members Intent** for the verification bot and the tournament bot.

## 2. Create required roles
1. In your Discord server, create the role that verified members receive (for example **Verified**). Copy the role ID (enable **Developer Mode** in Discord, then right-click the role).
2. Ensure staff responsible for tournaments have administrator permissions so they can run `/setup`.

## 3. Invite the bots
1. In the **OAuth2 → URL Generator**, select `bot` and `applications.commands` scopes.
2. Give each bot the permissions it needs:
   - Verification bot: manage roles, kick members, read messages.
   - Tournament bot: send messages, manage slash commands.
   - Giveaway bot: send messages, manage messages (optional).
3. Use the generated links to invite the bots to your server.

## 4. Environment variables
Configure the environment separately for each bot. All bots require AWS credentials with DynamoDB and CloudWatch Logs access.

### Verification bot (`bot.py`)
- `DISCORD_TOKEN` - verification bot token.
- `COC_EMAIL` / `COC_PASSWORD` - Clash of Clans credentials used for API access.
- `CLAN_TAG` - clan to validate membership against.
- `FEEDER_CLAN_TAG` (optional) - additional clan allowed for verification.
- `VERIFIED_ROLE_ID` - ID of the verified role created earlier.
- `DDB_TABLE_NAME` - DynamoDB table storing verification state.
- `ADMIN_LOG_CHANNEL_ID` (optional) - channel for audit logging.
- `AWS_REGION` (optional, defaults to `us-east-1`).

### Giveaway bot (`giveawaybot.py`)
- `DISCORD_TOKEN` - giveaway bot token.
- `GIVEAWAY_CHANNEL_ID` - channel hosting giveaway posts.
- `GIVEAWAY_CREATE_CHANNEL_ID` (optional) - channel dedicated to manual giveaways.
- `GIVEAWAY_TABLE_NAME` - DynamoDB table for giveaway entries.
- `COC_EMAIL`, `COC_PASSWORD`, `CLAN_TAG`, `FEEDER_CLAN_TAG` - reused for fairness checks.
- `DDB_TABLE_NAME` - verification table, used to confirm member eligibility.
- `GIVEAWAY_TEST` (optional) - `true` to shorten draw timers for testing.
- `USE_FAIRNESS_SYSTEM` (optional) - toggle for fairness adjustments.
- `AWS_REGION` (optional).

### Tournament bot (`tournamentbot.py`)
- `DISCORD_TOKEN` - tournament bot token.
- `COC_EMAIL` / `COC_PASSWORD` - Clash of Clans credentials for validating player tags.
- `TOURNAMENT_TABLE_NAME` - DynamoDB table for tournament configuration and registrations.
- `AWS_REGION` (optional, defaults to `us-east-1`).

## 5. Running locally
Install dependencies and execute the entry point you need:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# Verification bot
DISCORD_TOKEN=... python bot.py

# Tournament bot
DISCORD_TOKEN=... TOURNAMENT_TABLE_NAME=... python tournamentbot.py
```

To run inside Docker, build the appropriate image (`Dockerfile`, `Dockerfile.giveaway`, or `Dockerfile.tournament`) and pass the same environment variables with `docker run`.

## 6. Tournament commands
- `/setup` (admin only) - set team size (increments of 5), allowed Town Hall levels, and the maximum number of teams (increments of 2).
- `/registerteam` - players supply their Clash tags (space/comma separated). The bot validates each player through the Clash of Clans API and stores the registration in DynamoDB. Successful registrations are broadcast in the channel as `discord user | player name | player tag` lines.

## 7. Verification commands
- `/verifyclan <player_tag>` - link a Discord user to their Clash of Clans account.
- `/whois @member` - show the in-game name for a Discord user (visible only to the requester).

The verification bot also runs periodic membership checks and can remove members that leave the clan after an approval workflow.
