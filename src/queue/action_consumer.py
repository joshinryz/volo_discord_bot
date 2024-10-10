import json
import logging
import aio_pika

logger = logging.getLogger(__name__)


class ActionConsumer:
    def __init__(self, connection: aio_pika.Connection, loop, queue_name, action_queue, queue_args):
        self.connection = connection
        self.loop = loop
        self.queue_name = queue_name
        self.action_queue = action_queue
        self.queue_args = queue_args

    async def on_message(self, message: aio_pika.IncomingMessage):
        async with message.process():
            action = json.loads(message.body)
            await self.action_queue.put(action)

    async def start_consuming(self):
        if not self.connection:
            logger.error("No connection to RabbitMQ.")
            return

        channel = await self.connection.channel()
        queue = await channel.declare_queue(self.queue_name, arguments=self.queue_args)

        await queue.consume(self.on_message)

    async def close_connection(self):
        if self.connection:
            await self.connection.close()