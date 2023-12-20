import discord
from discord.ext import commands
from audio_processing import CustomSink, once_done
import os
import logging

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

GUILD_ID = [os.getenv('GUILD_ID')]
TOKEN = os.getenv('DISCORD_TOKEN')

# Logging setup
logging.basicConfig(level=logging.INFO)

# Bot setup
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)
bot.connections = {}

@bot.slash_command(guild_ids=GUILD_ID)
async def transcribe(ctx):
    """Starts voice transcription."""
    print("Transcribe command received")
    voice = ctx.author.voice
    if not voice:
        await ctx.respond("You must be in a voice channel to use this command.")
        return
    vc = await voice.channel.connect()
    bot.connections.update({ctx.guild.id: vc})
    sink = CustomSink(bot=bot, ctx=ctx)
    vc.start_recording(
        sink,
        once_done,
        ctx.channel,
    )
    await ctx.respond("The recording has started!")

@bot.slash_command(guild_ids=GUILD_ID)
async def stop(ctx):
    """
    Stop recording.
    """
    print("Stop command received")
    if ctx.guild.id in bot.connections:
        vc = bot.connections[ctx.guild.id]
        vc.stop_recording()
        del bot.connections[ctx.guild.id]
        await ctx.delete()
    else:
        await ctx.respond("Not recording in this guild.")

@bot.slash_command(guild_ids=GUILD_ID)
async def leave(ctx):
    """Leaves the current voice channel."""
    print("Leave command received")
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.respond("Left the voice channel.")
    else:
        await ctx.respond("Not connected to any voice channel.")

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')

bot.run(TOKEN)