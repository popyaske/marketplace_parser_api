"""Подключение к RabbitMQ."""
import os
import aio_pika
from typing import Optional

RABBITMQ_URL = os.getenv(
    "RABBITMQ_URL",
    "amqp://guest:guest@rabbitmq:5672/",
)

# Имена очередей
QUEUE_INCOMING = "parser.incoming"      # входящие URL
QUEUE_RESULTS_STORAGE = "parser.results.storage"     # ← storage
QUEUE_RESULTS_TELEGRAM = "parser.results.telegram"   # ← telegram
QUEUE_ERRORS = "parser.errors"          # ошибки
EXCHANGE_NAME = "parser"
EXCHANGE_RESULTS = "parser.results.fanout"           # ← fanout exchange


class Broker:
    """Обёртка над RabbitMQ."""

    def __init__(self, url: str = RABBITMQ_URL):
        self.url = url
        self.connection: Optional[aio_pika.RobustConnection] = None
        self.channel: Optional[aio_pika.RobustChannel] = None
        self.exchange: Optional[aio_pika.RobustExchange] = None

    async def connect(self, max_retries: int = 15, retry_delay: float = 3.0):
        """Подключиться к RabbitMQ с retry."""
        import asyncio
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                self.connection = await aio_pika.connect_robust(self.url)
                self.channel = await self.connection.channel()

                # Основной exchange
                self.exchange = await self.channel.declare_exchange(
                    EXCHANGE_NAME,
                    aio_pika.ExchangeType.DIRECT,
                    durable=True,
                )

                # Fanout exchange для результатов (storage + telegram)
                results_exchange = await self.channel.declare_exchange(
                    EXCHANGE_RESULTS,
                    aio_pika.ExchangeType.FANOUT,
                    durable=True,
                )

                # Очереди: incoming и errors (direct)
                for queue_name in (QUEUE_INCOMING, QUEUE_ERRORS):
                    queue = await self.channel.declare_queue(queue_name, durable=True)
                    await queue.bind(self.exchange, routing_key=queue_name)

                # Очереди результатов (fanout)
                storage_q = await self.channel.declare_queue(
                    QUEUE_RESULTS_STORAGE, durable=True
                )
                telegram_q = await self.channel.declare_queue(
                    QUEUE_RESULTS_TELEGRAM, durable=True
                )
                await storage_q.bind(results_exchange)
                await telegram_q.bind(results_exchange)

                return self
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)

        raise RuntimeError(f"RabbitMQ недоступен после {max_retries} попыток: {last_error}")

    async def publish(self, queue_name: str, payload: dict):
        """Опубликовать сообщение в очередь."""
        if self.exchange is None:
            raise RuntimeError("Broker не подключён. Вызовите connect()")

        import json
        message = aio_pika.Message(
            body=json.dumps(payload, ensure_ascii=False).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await self.exchange.publish(message, routing_key=queue_name)

    async def close(self):
        """Закрыть соединение."""
        if self.connection:
            await self.connection.close()


# Глобальный инстанс
broker = Broker()