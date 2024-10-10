import logging
import aio_pika

from src.queue.action_consumer import ActionConsumer
logger = logging.getLogger(__name__)


class ConsumerManager:
    def __init__(self, rabbit_conn, loop):
        self.loop = loop
        self.connection = rabbit_conn
        self.consumers = []

    async def create_consumer(self, queue_name, action_queue, queue_args=None):
        consumer = ActionConsumer(
            self.connection, self.loop, queue_name, action_queue, queue_args)
        self.consumers.append(consumer)
        await consumer.start_consuming()

    async def close(self):
        if self.connection:
            await self.connection.close()