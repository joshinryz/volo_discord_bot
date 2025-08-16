# minimal_test.py
import discord
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True # Or whatever else you might need for commands

bot = discord.Bot(intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.slash_command(name="join")
async def join(ctx: discord.ApplicationContext):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        try:
            await ctx.defer()
            vc = await channel.connect(timeout=30.0, reconnect=True)
            await ctx.followup.send(f"Connected to {channel.name}")
        except Exception as e:
            await ctx.followup.send(f"Error connecting: {e}")
            print(f"Error: {e}")
    else:
        await ctx.respond("You are not in a voice channel.")

bot.run(TOKEN)