import json
import aio_pika
import logging

logger = logging.getLogger(__name__)


class TranscriptPublisher:
    def __init__(self, rabbit_conn: aio_pika.Connection):
        self.connection = rabbit_conn
        self.channel = None
        self.queue = None
        self.queue_name = f"process_guild_transcripts.requests"
        self.exchange = None

    async def setup_connection(self):
        self.channel = await self.connection.channel()
        self.queue = await self.channel.declare_queue(self.queue_name)
        self.exchange = self.channel.default_exchange

    async def publish_data(self, data):
        logger.debug(f"Publishing transcript: {data}")
        await self.exchange.publish(
            aio_pika.Message(body=data.encode()),
            routing_key=self.queue_name
        )