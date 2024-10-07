import asyncio
import logging
import os

import discord
from dotenv import load_dotenv

from src.config.cliargs import CLIArgs
from src.utils.commandline import CommandLine
from src.bot.helper import BotHelper

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

logger = logging.getLogger()  # root logger


def configure_logging():
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('aiormq').setLevel(logging.ERROR)
    logging.getLogger('aio_pika').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('faster_whisper').setLevel(logging.WARNING)
    logging.getLogger('stripe').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    if CLIArgs.verbose:
        logger.setLevel(logging.DEBUG)
        logging.basicConfig(level=logging.DEBUG,
                            format='%(name)s: %(message)s')

    else:
        logger.setLevel(logging.INFO)
        logging.basicConfig(level=logging.INFO,
                            format='%(name)s: %(message)s')


if __name__ == "__main__":
    args = CommandLine.read_command_line()
    CLIArgs.update_from_args(args)

    configure_logging()

    loop = asyncio.get_event_loop()

    from src.bot.coolname_bot import CoolNameBot


    bot = CoolNameBot(loop)

    if not discord.opus.is_loaded():
        try:
            discord.opus.load_opus("opus")
        except OSError:
            # make this OS independenet
            discord.opus.load_opus("/opt/homebrew/lib/libopus.dylib")
        except Exception as e:
            logger.error(f"Error loading opus library: {e}")
            raise e

    @bot.event
    async def on_voice_state_update(member, before, after):
        if member.id == bot.user.id:
            # If the bot left the "before" channel
            if after.channel is None:
                guild_id = before.channel.guild.id
                helper = bot.guild_to_helper.get(guild_id, None)
                if helper:
                    helper.set_vc(None)
                    bot.guild_to_helper.pop(guild_id, None)

                bot._close_and_clean_sink_for_guild(guild_id)

    @bot.slash_command(name="connect", description="Connect to your voice channel.")
    async def connect(ctx: discord.context.ApplicationContext):
        if bot._is_ready is False:
            await ctx.respond("I am not ready yet. Try again later.", ephemeral=True)
            return

        author_vc = ctx.author.voice
        if not author_vc:
            await ctx.respond("You are not in a voice channel.", ephemeral=True)
            return

        await ctx.trigger_typing()
        try:
            guild_id = ctx.guild_id
            vc = await author_vc.channel.connect()
            helper = bot.guild_to_helper.get(guild_id, BotHelper(bot))
            helper.guild_id = guild_id
            helper.set_vc(vc)
            bot.guild_to_helper[guild_id] = helper
            await ctx.respond(f"Connected to {author_vc.channel.name}.", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"{e}", ephemeral=True)

        bot.start_recording(ctx)

    @bot.slash_command(name="disconnect", description="Disconnect from your voice channel.")
    async def disconnect(ctx: discord.context.ApplicationContext):
        guild_id = ctx.guild_id
        helper = bot.guild_to_helper[guild_id]
        bot_vc = helper.vc
        if not bot_vc:
            await ctx.respond("I am not in your voice channel.", ephemeral=True)
            return

        await ctx.trigger_typing()
        if bot.guild_is_recording.get(guild_id, False):
            bot.stop_recording(ctx)

        await bot_vc.disconnect()
        helper.guild_id = None
        helper.set_vc(None)
        bot.guild_to_helper.pop(guild_id, None)

        await ctx.respond("Disconnected from VC.", ephemeral=True)

    @bot.slash_command(name="resume", description="Resume music playback.")
    async def resume(ctx: discord.context.ApplicationContext):
        helper = bot.guild_to_helper.get(ctx.guild_id, None)
        if helper and helper.resume_music():
            await ctx.respond("Resuming music.", ephemeral=True)
        else:
            await ctx.respond("No music to resume.", ephemeral=True)

    @bot.slash_command(name="pause", description="Pause music playback.")
    async def pause(ctx: discord.context.ApplicationContext):
        helper = bot.guild_to_helper.get(ctx.guild_id, None)
        if helper and helper.pause_music():
            await ctx.respond("Pausing music.", ephemeral=True)
        else:
            await ctx.respond("No music is playing.", ephemeral=True)

    @bot.slash_command(name="stop", description="Stop music playback.")
    async def stop(ctx: discord.context.ApplicationContext):
        helper = bot.guild_to_helper.get(ctx.guild_id, None)
        if helper and helper.stop_music():
            await ctx.respond("Stopping music.", ephemeral=True)
        else:
            await ctx.respond("No music is playing.", ephemeral=True)

    @bot.slash_command(name="volume", description="Set the volume.")
    async def volume(ctx: discord.context.ApplicationContext, value: discord.Option(int)):
        helper = bot.guild_to_helper.get(ctx.guild_id, None)
        helper.set_volume(value)
        await ctx.respond(f"Volume set to {value}.", ephemeral=True)

    @bot.slash_command(name="play", description="Play a song (Supports YouTube and other audio URLs).")
    async def play(ctx: discord.context.ApplicationContext, url: str):
        helper = bot.guild_to_helper.get(ctx.guild_id, None)
        try:
            await helper.play_youtube(url)
            await ctx.respond(f"Playing {url}.", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"Could not play {url}.", ephemeral=True)
            logger.error(f"Error playing {url}: {e}")

    @bot.slash_command(name="help", description="Show the help message.")
    async def help(ctx: discord.context.ApplicationContext):
        embed_fields = [
            discord.EmbedField(
                name="/connect", value="Connect to your voice channel.", inline=True),
            discord.EmbedField(
                name="Hey Billy, tell me a long story about a cat with my name.", value="Billy can come up with fun stories for you."),
            discord.EmbedField(
                name="Hey Billy, how's the weather in Tokyo?", value="Billy can fetch real-time data.\nAsk Billy about sports, stocks, currency conversions and more!"),
            discord.EmbedField(
                name="Okay Billy, post a good morning GIF.", value="Billy can post all kinds of GIFs.\nYou can also ask him to post images and videos."),
            discord.EmbedField(
                name="Yo Billy, post Blank Space by Taylor Swift.", value="Billy can post *and* play music for you.\nOptional: Use the `/play` command for your own URLs."),
            discord.EmbedField(
                name="Yo Billy, play cricket sound effects.", value="Billy can play sound effects without interrupting the music."
            )
        ]

        embed = discord.Embed(title="HeyBilly Help",
                              description="""HeyBilly is Discord's most advanced voice assistant. Say "Hey Billy, play some smooth jazz" and he will play some smooth jazz. He can post news stories, play sound effects, and much more! Here are some commands to get you started.""",
                              color=discord.Color.blue(),
                              fields=embed_fields)

        await ctx.respond(embed=embed, ephemeral=True)

    @bot.slash_command(name="voice_get", description="Get the current voice used for TTS.")
    async def voice_get(ctx: discord.context.ApplicationContext):
        res = bot.supabase.from_('guild_settings').select(
            'voice').eq('guild_id', ctx.guild_id).execute()

        stored_voice = res.data[0].get('voice', None)
        if stored_voice:
            await ctx.respond(f"Current voice: {get_voice_name(stored_voice)}.", ephemeral=True)
        else:
            helper = bot.guild_to_helper.get(ctx.guild_id, None)
            if helper:
                await ctx.respond(f"Current voice: {helper.voice}.", ephemeral=True)
            else:
                await ctx.respond("HeyBilly must be in a voice channel first.", ephemeral=True)

    @bot.slash_command(name="voice_set", description="Set the current voice used for TTS.")
    @discord.default_permissions(manage_messages=True)
    async def voice_set(ctx: discord.context.ApplicationContext, voice: discord.Option(str, choices=[
        discord.OptionChoice(name=name, value=voice) for voice, name in TTS_VOICE_MAP.items()
    ])):
        try:
            helper = bot.guild_to_helper.get(ctx.guild_id, None)

            if helper:
                helper.set_voice(voice)
                await ctx.respond(f"Voice set to {get_voice_name(voice)}.", ephemeral=True)
            else:
                await ctx.respond("HeyBilly must be in a voice channel first.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error setting voice: {e}")
            await ctx.respond(f"Error setting voice. Try again later.", ephemeral=True)

    @bot.slash_command(name="playing", description="Show what's currently playing.")
    async def playing(ctx: discord.context.ApplicationContext):
        helper = bot.guild_to_helper.get(ctx.guild_id, None)
        if helper:
            music_source_url = helper.current_music_source_url
            if music_source_url:
                await ctx.respond(f"Currently playing: {music_source_url}", ephemeral=True)
            else:
                await ctx.respond("No music is playing.", ephemeral=True)
        else:
            await ctx.respond("HeyBilly is not in a voice channel.", ephemeral=True)

    try:
        loop.run_until_complete(bot.start(DISCORD_BOT_TOKEN))
    except KeyboardInterrupt:
        logger.info("^C received, shutting down...")
        asyncio.run(bot.stop_and_cleanup())
    finally:
        # Close all connections
        loop.run_until_complete(bot.close_consumers())

        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in tasks:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

        # Close the loop
        loop.run_until_complete(bot.close())
        loop.close()