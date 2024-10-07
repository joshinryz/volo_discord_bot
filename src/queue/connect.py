import aio_pika
import logging

logger = logging.getLogger(__name__)


class RabbitConnection:
    @staticmethod
    async def connect(host, loop):
        connection = await aio_pika.connect_robust(host=host, loop=loop)
        if connection:
            logger.info("RabbitMQ connected successfully.")
            return connection
        else:
            logger.error("Failed to connect to RabbitMQ.")
            raise ConnectionError("Failed to connect to RabbitMQ.")