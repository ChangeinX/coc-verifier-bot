import boto3
import coc
import discord
from discord import app_commands
import openai

from .config import AWS_REGION, DDB_TABLE_NAME, OPENAI_API_KEY

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DDB_TABLE_NAME) if DDB_TABLE_NAME else None

coc_client = coc.Client()

openai.api_key = OPENAI_API_KEY
