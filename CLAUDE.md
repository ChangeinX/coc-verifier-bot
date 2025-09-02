# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a multi-bot Discord application system for Clash of Clans communities with two main bots:

1. **Verification Bot** (`bot.py`) - Links Discord users to their Clash of Clans accounts
2. **Giveaway Bot** (`giveawaybot.py`) - Manages automated giveaways with CoC integration

## Architecture

### Core Components
- **Discord Integration**: Uses discord.py v2.5+ with slash commands and intents
- **Clash of Clans API**: Uses coc.py v3.9+ for player/clan verification
- **Data Storage**: AWS DynamoDB for persistent data (verifications, giveaways)
- **Infrastructure**: Containerized deployment on AWS ECS via Terraform
- **CI/CD**: GitHub Actions with OpenTofu for infrastructure management

### Data Flow
- Bots authenticate users via CoC player tags and clan membership
- User verifications stored in DynamoDB with Discord-CoC mappings
- Giveaway entries tracked with eligibility validation against clan roster
- Automated member removal when players leave the clan

## Development Commands

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run individual bots locally
python bot.py          # Verification bot
python giveawaybot.py  # Giveaway bot
```

### Docker Development
```bash
# Build containers
docker build -t coc-verifier-bot .
docker build -t coc-giveaway-bot -f Dockerfile.giveaway .

# Run containers with environment variables
docker run --env-file .env coc-verifier-bot
```

### Infrastructure
```bash
# Deploy infrastructure (from infra/ directory)
cd infra
tofu init
tofu plan
tofu apply
```

## Environment Configuration

### Required Variables (All Bots)
- `DISCORD_TOKEN` - Bot token from Discord Developer Portal
- `COC_EMAIL` / `COC_PASSWORD` - Clash of Clans credentials
- `CLAN_TAG` - Target clan tag (e.g. `#ABCD123`)
- `DDB_TABLE_NAME` - DynamoDB table for verifications
- `AWS_REGION` - AWS region (default: `us-east-1`)

### Bot-Specific Variables
**Verification Bot:**
- `VERIFIED_ROLE_ID` - Discord role ID for verified members
- `ADMIN_LOG_CHANNEL_ID` (optional) - Channel for verification logs

**Giveaway Bot:**
- `GIVEAWAY_CHANNEL_ID` - Channel for giveaway announcements
- `GIVEAWAY_TABLE_NAME` - DynamoDB table for giveaway data
- `GIVEAWAY_TEST` - Enable test mode (`true`/`false`)


## Key Implementation Details

### Discord Command Structure
- Uses `app_commands.CommandTree` for slash command registration
- Commands auto-sync on bot startup
- Error handling with user-friendly messages

### CoC Integration Pattern
```python
# Standard CoC client initialization
coc_client = coc.Client()
await coc_client.login(COC_EMAIL, COC_PASSWORD)

# Clan member validation
clan = await coc_client.get_clan(CLAN_TAG)
player = await coc_client.get_player(player_tag)
is_member = player.tag in [p.tag for p in clan.members]
```

### DynamoDB Schema
**Verifications Table:**
- `discord_id` (string) - Primary key
- `player_tag` (string) - CoC player tag
- `player_name` (string) - In-game name

**Giveaways Table:**
- `giveaway_id` (string) - Primary key
- `entries` (list) - User entry data
- `status` (string) - `active`/`ended`

### Deployment Architecture
- AWS ECS Fargate for container hosting
- ECR for container registry
- CloudWatch for logging (7-day retention)
- GitHub Actions triggers deployment on main branch pushes

## Common Development Patterns

### Error Handling
All bots follow consistent error handling:
```python
try:
    # Bot operation
except Exception as e:
    log.error(f"Operation failed: {e}")
    await interaction.followup.send("An error occurred.", ephemeral=True)
```

### Async Task Management
Background tasks use `@tasks.loop()` decorator:
```python
@tasks.loop(hours=1)
async def periodic_task():
    # Implementation
    
@periodic_task.before_loop
async def before_task():
    await bot.wait_until_ready()
```

### Bot Deployment Dependencies
- Discord bot requires Server Members Intent enabled
- CoC API credentials must have access to target clan
- AWS credentials configured for DynamoDB access
- Proper Discord permissions: Manage Roles, Send Messages, Use Slash Commands