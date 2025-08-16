import asyncio
import json
import logging
import os

import discord

from collections import defaultdict
from src.sinks.whisper_sink import WhisperSink

logger = logging.getLogger(__name__)

DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
TRANSCRIPTION_METHOD = os.getenv("TRANSCRIPTION_METHOD")

class VoloBot(discord.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True  # Required for voice state updates
        intents.guilds = True        # Required for guild information

        super().__init__(command_prefix="!",
                         activity=discord.CustomActivity(name='Transcribing Audio to Text'),
                         intents=intents)
        self.guild_to_helper = {}
        self.guild_is_recording = {}
        self.guild_whisper_sinks = {}
        self.guild_whisper_message_tasks = {}
        self._is_ready = False
        # Resolve transcriber type with precedence: CLI > ENV > default
        from src.config.cliargs import CLIArgs
        if CLIArgs.transcriber_type is not None:
            self.transcriber_type = CLIArgs.transcriber_type
        elif TRANSCRIPTION_METHOD == "openai":
            self.transcriber_type = "openai"
        else:
            self.transcriber_type = "local"
    

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} to Discord.")
        self._is_ready = True


    async def close_consumers(self):
        # Gracefully handle absence of a consumer manager
        consumer_manager = getattr(self, "consumer_manager", None)
        if consumer_manager and hasattr(consumer_manager, "close"):
            await consumer_manager.close()

    def _close_and_clean_sink_for_guild(self, guild_id: int):
        whisper_sink: WhisperSink | None = self.guild_whisper_sinks.get(
            guild_id, None)

        if whisper_sink:
            logger.debug(f"Stopping whisper sink, requested by {guild_id}.")
            whisper_sink.stop_voice_thread()
            del self.guild_whisper_sinks[guild_id]
            whisper_sink.close()

    
    def start_recording(self, ctx: discord.context.ApplicationContext):
        """
        Start recording audio from the voice channel. Create a whisper sink
        and start sending transcripts to the queue.

        Since this is a critical function, this is where we should handle
        subscription checks and limits.
        """
        try:
            self.start_whisper_sink(ctx)
            self.guild_is_recording[ctx.guild_id] = True
        except Exception as e:
            logger.error(f"Error starting whisper sink: {e}")

    def start_whisper_sink(self, ctx: discord.context.ApplicationContext):
        guild_voice_sink = self.guild_whisper_sinks.get(ctx.guild_id, None)
        if guild_voice_sink:
            logger.debug(
                f"Sink is already active for guild {ctx.guild_id}.")
            return

        async def on_stop_record_callback(sink: WhisperSink, ctx):
            logger.debug(
                f"{ctx.channel.guild.id} -> on_stop_record_callback")
            self._close_and_clean_sink_for_guild(ctx.guild_id)

        transcript_queue = asyncio.Queue()

        whisper_sink = WhisperSink(
            transcript_queue,
            self.loop,
            data_length=50000,
            max_speakers=10,
            transcriber_type=self.transcriber_type
        )

        self.guild_to_helper[ctx.guild_id].vc.start_recording(
            whisper_sink, on_stop_record_callback, ctx)

        def on_thread_exception(e):
            logger.warning(
                f"Whisper sink thread exception for guild {ctx.guild_id}. Retry in 5 seconds...\n{e}")
            self._close_and_clean_sink_for_guild(ctx.guild_id)

            # retry in 5 seconds
            self.loop.call_later(5, self.start_recording, ctx)

        whisper_sink.start_voice_thread(on_exception=on_thread_exception)

        self.guild_whisper_sinks[ctx.guild_id] = whisper_sink

    def stop_recording(self, ctx: discord.context.ApplicationContext):
        vc = ctx.guild.voice_client
        if vc:
            self.guild_is_recording[ctx.guild_id] = False
            vc.stop_recording()
        guild_id = ctx.guild_id
        whisper_message_task = self.guild_whisper_message_tasks.get(
            guild_id, None)
        if whisper_message_task:
            logger.debug("Cancelling whisper message task.")
            whisper_message_task.cancel()
            del self.guild_whisper_message_tasks[guild_id]

    def cleanup_sink(self, ctx: discord.context.ApplicationContext):
        guild_id = ctx.guild_id
        self._close_and_clean_sink_for_guild(guild_id)

    async def get_transcription(self, ctx: discord.context.ApplicationContext):
        # Get the transcription queue
        if not (self.guild_whisper_sinks.get(ctx.guild_id)):
            return
        whisper_sink = self.guild_whisper_sinks[ctx.guild_id]
        transcriptions = []
        if whisper_sink is None:
            return
    
        transcriptions_queue = whisper_sink.transcription_output_queue
        while not transcriptions_queue.empty():
            transcriptions.append(await transcriptions_queue.get())
        return transcriptions

    async def stop_and_cleanup(self):
        try:
            for sink in self.guild_whisper_sinks.values():
                sink.close()
                sink.stop_voice_thread()
                logger.debug(
                    f"Stopped whisper sink for guild {sink.vc.channel.guild.id} in cleanup.")
            self.guild_whisper_sinks.clear()
        except Exception as e:
            logger.error(f"Error stopping whisper sinks: {e}")
        finally:
            logger.info("Cleanup completed.")
