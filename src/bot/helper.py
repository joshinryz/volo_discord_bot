import asyncio
import io
import logging
from base64 import b64decode

import discord

BOT_NAME = "VOLO 💤"
BOT_AWAKE_NAME = "VOLO 💬"
BOT_PROCESSING_NAME = "VOLO 💡"

logger = logging.getLogger(__name__)


class BotHelper:
    def __init__(self, bot):
        self.bot = bot
        self.guild_id = None

        self.tts_queue = None
        self.current_music_source = None
        self.current_music_source_url = None
        self.current_sfx_source = None
        self.user_music_volume = 0.5

        self.voice = None

        self.vc = None

    def set_vc(self, voice_client):
        self.vc = voice_client
        if voice_client is None:
            self.tts_queue = None
            self.current_music_source = None
            self.current_sfx_source = None
            logger.debug(
                "Voice client set to None. Clearing tts queue and current music source.")
            return

    async def send_message(self, channel_id, content, embed=None, tts=False):
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send(content=content, embed=embed, tts=tts)
        else:
            logger.error(f"Channel with ID {channel_id} not found.")


    async def _handle_post_node(self, node, discord_channel_id):
        await self.send_message(discord_channel_id, node["data"]["text"])



    async def _handle_request_status_update(self, update):
        if self.guild_id is None:
            return

        try:
            status = update["status"]
            if status == "awake":
                await self.bot.get_guild(self.guild_id).get_member(self.bot.user.id).edit(nick=BOT_AWAKE_NAME)
            elif status == "processing":
                await self.bot.get_guild(self.guild_id).get_member(self.bot.user.id).edit(nick=BOT_PROCESSING_NAME)
            elif status == "completed":
                await self.bot.get_guild(self.guild_id).get_member(self.bot.user.id).edit(nick=BOT_NAME)
        except Exception as e:
            logger.error(f"Error updating status: {e}")
            logger.error(f"Data: {update}")