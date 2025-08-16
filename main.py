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
from src.utils.pdf_generator import pdf_generator

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
    pdf_directory = '.logs/pdfs'
    os.makedirs(log_directory, exist_ok=True) 
    os.makedirs(pdf_directory, exist_ok=True)  

    # Get the current date for the log file name
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_filename = os.path.join(log_directory, f"{current_date}-transcription.log")

    # Custom logging format (date with milliseconds, message)
    # Use %(msecs)03d to ensure proper millisecond formatting
    log_format = '%(asctime)s.%(msecs)03d %(name)s: %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

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

    # File handler for transcription logs (append mode)
    file_handler = logging.FileHandler(log_filename, mode='a')
    file_handler.setLevel(logging.INFO)
    
    # Custom formatter WITHOUT the automatic timestamp
    file_handler.setFormatter(logging.Formatter(
        '%(message)s'  # Only log the custom message, no automatic timestamp
    ))

    # Add the handler to the transcription logger
    transcription_logger.addHandler(file_handler)

if __name__ == "__main__":
    args = CommandLine.read_command_line()
    CLIArgs.update_from_args(args)

    configure_logging()
    from src.bot.volo_bot import VoloBot  
    
    bot = VoloBot()

    @bot.event
    async def on_voice_state_update(member, before, after):
        if member.id == bot.user.id:
            logger.debug(f"Voice state update for bot: before={getattr(before.channel, 'id', None)} after={getattr(after.channel, 'id', None)}")
            # If the bot left the "before" channel
            if after.channel is None and before and before.channel:
                guild_id = before.channel.guild.id
                helper = bot.guild_to_helper.get(guild_id, None)
                if helper:
                    helper.set_vc(None)
                    bot.guild_to_helper.pop(guild_id, None)

                bot._close_and_clean_sink_for_guild(guild_id)

    @bot.slash_command(name="connect", description="Add VOLO to your voice party.")
    async def connect(ctx: discord.context.ApplicationContext):
        # Defer the response. ephemeral=True makes the thinking message only visible to the user.
        await ctx.defer(ephemeral=True)

        if bot._is_ready is False:
            await ctx.followup.send("Ahem, seems even the finest quills falter. 🛑 No connection, no tale. Try again, my dear adventurer shortly.”", ephemeral=True)
            return
        author_vc = ctx.author.voice
        if not author_vc:
            await ctx.followup.send("I'm sorry adventurer, but it appears your voice has not joined a party.", ephemeral=True)
            return
        # check if we are already connected to a voice channel
        if bot.guild_to_helper.get(ctx.guild_id, None):
            await ctx.followup.send("I'm sorry adventurer, but it appears I'm already in a party. 🤺", ephemeral=True)
            return
        
        async def fresh_disconnect():
            try:
                # Proactively clear any stale voice state before attempting a fresh connect
                if ctx.guild.voice_client:
                    await ctx.guild.voice_client.disconnect(force=True)
                await ctx.guild.change_voice_state(channel=None)
            except Exception:
                pass

        try:
            guild_id = ctx.guild_id
            # Short delay can help ensure the gateway voice state updates are settled
            await asyncio.sleep(1.0)
            # Attempt to connect to the voice channel (allow reconnect semantics)
            vc = await author_vc.channel.connect(timeout=45.0, reconnect=True)
            
            helper = bot.guild_to_helper.get(guild_id, BotHelper(bot))
            helper.guild_id = guild_id
            helper.set_vc(vc)
            bot.guild_to_helper[guild_id] = helper
            
            # Send a public success message
            await ctx.followup.send(f"Ah, splendid! The lore shall now flow as freely as the finest ale. 🍺 Prepare to immortalize brilliance!", ephemeral=False)
            # Avoid issuing an immediate voice state update here; it can interfere with the
            # connection handshake on some library versions and cause reconnect loops.
        except discord.errors.ConnectionClosed as e:
            logger.warning(f"Initial voice connect failed with {e}. Attempting one clean retry.")
            # One clean retry after forcing a state reset
            await fresh_disconnect()
            try:
                await asyncio.sleep(1.0)
                vc = await author_vc.channel.connect(timeout=45.0, reconnect=True)
                helper = bot.guild_to_helper.get(guild_id, BotHelper(bot))
                helper.guild_id = guild_id
                helper.set_vc(vc)
                bot.guild_to_helper[guild_id] = helper
                await ctx.followup.send("Reconnected successfully. Ready to scribe!", ephemeral=False)
            except Exception as inner:
                logger.error(f"Retry voice connection failed: {inner}", exc_info=True)
                await ctx.followup.send(f"Failed to connect to voice channel: {inner}.", ephemeral=True)
        except Exception as e:
            logger.error(f"An unexpected error occurred in 'connect' command: {e}", exc_info=True)
            await ctx.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)

    @bot.slash_command(name="scribe", description="Ink the Saga of this adventure.")
    async def ink(ctx: discord.context.ApplicationContext):
        await ctx.trigger_typing()
        connect_command = next((cmd for cmd in ctx.bot.application_commands if cmd.name == "connect"), None)
        if not connect_command:
            connect_text = "`/connect`"
        else:
            connect_text = f"</connect:{connect_command.id}>"
        if not bot.guild_to_helper.get(ctx.guild_id, None):
            await ctx.respond(f"Well, that's akward. I dont seem to be in your party. How about I join? {connect_text}", ephemeral=True)
            return
        # check if we are already scribing
        if bot.guild_is_recording.get(ctx.guild_id, False):
            await ctx.respond("I'm sorry my liege, I can only write so fast.. 😥 ✒️", ephemeral=True)
            return
        bot.start_recording(ctx)
        await ctx.respond("Your words are now inscribed in the annals of history! ✍️ Fear not, for V.O.L.O leaves nothing unwritten", ephemeral=False)
    
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
            await ctx.respond("Well, that’s awkward. 😐 Was I suppose to be writing?", ephemeral=True)
            return

        await ctx.trigger_typing()
        
        if bot.guild_is_recording.get(guild_id, False):
            await bot.get_transcription(ctx)
            bot.stop_recording(ctx)
            bot.guild_is_recording[guild_id] = False
            await ctx.respond("The quill rests. 🖋️ A pause, but not the end. Awaiting your next grand tale, of course!", ephemeral=False)
            #await bot.get_transcription(ctx)
            bot.cleanup_sink(ctx)
        
    @bot.slash_command(name="disconnect", description="VOLO leaves your party. Goodbye, friend.")
    async def disconnect(ctx: discord.context.ApplicationContext):
        guild_id = ctx.guild_id
        id_exists = bot.guild_to_helper.get(guild_id, None)
        if not id_exists:
            await ctx.respond("Well, that's akward. I dont seem to be in your party... Should I just go?", ephemeral=True)
            return
        
        helper = bot.guild_to_helper[guild_id]    
        bot_vc = helper.vc
        
        if not bot_vc:
            await ctx.respond("Huh, weird.. where am I? Maybe we should party back up.", ephemeral=True)
            return
        
        await ctx.trigger_typing()
        await bot_vc.disconnect()
        helper.guild_id = None
        helper.set_vc(None)
        bot.guild_to_helper.pop(guild_id, None)

        await ctx.respond("The tome is sealed! 📖 Another chapter well-told, another adventure preserved. You have my gratitude!", ephemeral=False)

    @bot.slash_command(name="generate_pdf", description="Generate a PDF of the transcriptions.")
    async def generate_pdf(ctx: discord.context.ApplicationContext):
        guild_id = ctx.guild_id
        helper = bot.guild_to_helper.get(guild_id, None)
        if not helper:
            await ctx.respond("Well, that's akward. I dont seem to be in your party.", ephemeral=True)
            return
        transcription = await bot.get_transcription(ctx)
        if not transcription:
            await ctx.respond("I'm sorry, but it appears I have no transcriptions to write into the tome.", ephemeral=True)
            return
        pdf_file_path = await pdf_generator(transcription)
        # Send the PDF as an attachment
        if os.path.exists(pdf_file_path):
            try:
                with open(pdf_file_path, "rb") as f:
                    discord_file = discord.File(f, filename=f"session_transcription.pdf")
                    await ctx.respond("Here is the transcription from this session:", file=discord_file)
            finally:
                os.remove(pdf_file_path)
        else:
            await ctx.respond("No transcription file could be generated.", ephemeral=True)


        
    @bot.slash_command(name="help", description="Show the help message.")
    async def help(ctx: discord.context.ApplicationContext):
        embed_fields = [
            discord.EmbedField(
                name="/connect", value="Connect to your voice channel.", inline=True),
            discord.EmbedField(
                name="/disconnect", value="Disconnect from your voice channel.", inline=True),
            discord.EmbedField(
                name="/scribe", value="Transcribe the voice channel.", inline=True),
            discord.EmbedField(
                name="/stop", value="Stop the transcription.", inline=True),
            discord.EmbedField(
                name="/generate_pdf", value="Generate a PDF of the transcriptions.", inline=True),
            discord.EmbedField(
                name="/help", value="Show the help message.", inline=True),
        ]

        embed = discord.Embed(title="Volo Help 📖",
                              description="""Summon the Lorekeeper’s Wisdom 🔉 ➡️ 📃""",
                              color=discord.Color.blue(),
                              fields=embed_fields)

        await ctx.respond(embed=embed, ephemeral=True)



    try:
        bot.run(DISCORD_BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("^C received, shutting down...")
    finally:
        # Best-effort cleanup inside bot's loop via on_close
        pass