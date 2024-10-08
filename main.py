import asyncio
import logging
import os
from datetime import datetime
import time
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
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('faster_whisper').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    # Ensure the directory exists
    log_directory = '.logs/transcripts'
    os.makedirs(log_directory, exist_ok=True)  # Create the directory if it doesn't exist

    # Get the current date for the log file name
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_filename = os.path.join(log_directory, f"{current_date}-transcription.log")

    # Custom logging format (date with milliseconds, message)
    log_format = '%(asctime)s %(name)s: %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S.%f'[:-3]  # Trim to milliseconds

    if CLIArgs.verbose:
        logger.setLevel(logging.DEBUG)
        logging.basicConfig(level=logging.DEBUG,
                            format=log_format,
                            datefmt=date_format)
    else:
        logger.setLevel(logging.INFO)
        logging.basicConfig(level=logging.INFO,
                            format=log_format,
                            datefmt=date_format)
    
    # Set up the transcription logger
    transcription_logger = logging.getLogger('transcription')
    transcription_logger.setLevel(logging.INFO)

    # File handler for transcription logs (with appending to existing file)
    file_handler = logging.FileHandler(log_filename, mode='a')  # Append mode
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S.%f'[:-3]
    ))

    # Add the handler to the transcription logger
    transcription_logger.addHandler(file_handler)

if __name__ == "__main__":
    args = CommandLine.read_command_line()
    CLIArgs.update_from_args(args)

    configure_logging()

    loop = asyncio.get_event_loop()

    from src.bot.volo_bot import VoloBot


    bot = VoloBot(loop)

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

    @bot.slash_command(name="connect", description="Add VOLO to your voice party.")
    async def connect(ctx: discord.context.ApplicationContext):
        if bot._is_ready is False:
            await ctx.respond("Ahem, seems even the finest quills falter. 🛑 No connection, no tale. Try again, my dear adventurer.”", ephemeral=True)
            return
        author_vc = ctx.author.voice
        if not author_vc:
            await ctx.respond("I'm sorry adventurer, but it appears your voice has not joined a party.", ephemeral=True)
            return

        await ctx.trigger_typing()
        try:
            guild_id = ctx.guild_id
            vc = await author_vc.channel.connect()
            helper = bot.guild_to_helper.get(guild_id, BotHelper(bot))
            helper.guild_id = guild_id
            helper.set_vc(vc)
            bot.guild_to_helper[guild_id] = helper
            await ctx.respond(f"Ah, splendid! The lore shall now flow as freely as the finest ale. 🍺 Prepare to immortalize brilliance!", ephemeral=True)
            await ctx.guild.change_voice_state(channel=author_vc.channel, self_mute=True)
        except Exception as e:
            await ctx.respond(f"{e}", ephemeral=True)
        #bot.start_recording(ctx)

    @bot.slash_command(name="scribe", description="Ink the Saga of this adventure.")
    async def ink(ctx: discord.context.ApplicationContext):
        await ctx.trigger_typing()
        bot.start_recording(ctx)
        await ctx.respond("Your words are now inscribed in the annals of history! ✍️ Fear not, for V.O.L.O leaves nothing unwritten", ephemeral=True)
    
    @bot.slash_command(name="stop", description="Close the Tome on this adventure.")
    async def stop(ctx: discord.context.ApplicationContext):
        guild_id = ctx.guild_id
        helper = bot.guild_to_helper.get(guild_id, None)
        if not helper:
            await ctx.respond("Well, that's akward. I dont seem to be in your party.", ephemeral=True)
            return

        bot_vc = helper.vc
        if not bot_vc:
            await ctx.respond("Well, that's akward. I dont seem to be in your party.", ephemeral=True)
            return

        if not bot.guild_is_recording.get(guild_id, False):
            await ctx.respond("Well, that’s awkward. 😐 The quill refuses to still itself... Shall I try again, or will you?", ephemeral=True)
            return

        await ctx.trigger_typing()
        if bot.guild_is_recording.get(guild_id, False):
            bot.stop_recording(ctx)
            await ctx.respond("The quill rests. 🖋️ A pause, but not the end. Awaiting your next grand tale, of course!", ephemeral=True)
        
    @bot.slash_command(name="disconnect", description="VOLO leaves your party. Goodbye, friend.")
    async def disconnect(ctx: discord.context.ApplicationContext):
        guild_id = ctx.guild_id
        helper = bot.guild_to_helper[guild_id]
        bot_vc = helper.vc
        if not bot_vc:
            await ctx.respond("Well, that's akward. I dont seem to be in your party.", ephemeral=True)
            return

        await ctx.trigger_typing()
        await bot_vc.disconnect()
        helper.guild_id = None
        helper.set_vc(None)
        bot.guild_to_helper.pop(guild_id, None)

        await ctx.respond("The tome is sealed! 📖 Another chapter well-told, another adventure preserved. You have my gratitude!", ephemeral=True)


    @bot.slash_command(name="help", description="Show the help message.")
    async def help(ctx: discord.context.ApplicationContext):
        embed_fields = [
            discord.EmbedField(
                name="/connect", value="Connect to your voice channel.", inline=True),
            discord.EmbedField(
                name="/disconnect", value="Disconnect from your voice channel.", inline=True),
            discord.EmbedField(
                name="/transcribe", value="Transcribe the voice channel.", inline=True),
            discord.EmbedField(
                name="/stop", value="Stop the transcription.", inline=True),
            discord.EmbedField(
                name="/help", value="Show the help message.", inline=True),
        ]

        embed = discord.Embed(title="Volo Help 📖",
                              description="""Summon the Lorekeeper’s Wisdom 🔉 ➡️ 📃""",
                              color=discord.Color.blue(),
                              fields=embed_fields)

        await ctx.respond(embed=embed, ephemeral=True)



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