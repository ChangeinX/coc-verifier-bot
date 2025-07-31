# Discord Setup Guide

This project provides a verification bot that links Discord users to their Clash of Clans accounts.
Follow the steps below to configure the bot in your server.

## 1. Create a Discord application
1. Go to <https://discord.com/developers/applications> and create a new application.
2. In the **Bot** tab, add a bot user and copy the **Bot Token**.
3. Enable the **Server Members Intent**.

## 2. Create the "Verified" role
1. In your Discord server, create a role that will be given to verified members (e.g. **Verified**).
2. Note the role ID. In Discord, you can copy a role's ID by enabling **Developer Mode** in user settings and right‑clicking the role.

## 3. Invite the bot
1. In the **OAuth2** section of the Developer Portal, generate an invite link with at least the `bot` scope and the `applications.commands` scope.
2. Grant the bot permissions to read messages, manage roles, and kick members.
3. Use the link to invite the bot to your server.

## 4. Set environment variables
The bot requires the following environment variables:

- `DISCORD_TOKEN` – the token from the Developer Portal.
- `COC_EMAIL` – Clash of Clans email address used for login.
- `COC_PASSWORD` – Clash of Clans password.
- `CLAN_TAG` – the tag of your clan (e.g. `#ABCD123`).
- `VERIFIED_ROLE_ID` – ID of the role created above.
- `DDB_TABLE_NAME` – DynamoDB table name to store verifications.
- `ADMIN_LOG_CHANNEL_ID` (optional) – channel ID for verification logs.
- `AWS_REGION` (optional) – defaults to `us-east-1`.

AWS credentials must also be configured so the bot can access DynamoDB.

## 5. Run the bot
Install dependencies from `requirements.txt` and execute `python bot.py`, or run the provided Docker container with the same environment variables.

Once running, the bot exposes the following commands:

- `/verify <player_tag>` – verify a member by Clash of Clans tag.
- `/whois @member` – show the in‑game name for a Discord user (only visible to you).

The bot will also automatically remove members from the server if they leave the clan.
