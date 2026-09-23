"""Consumer, который сохраняет результаты парсинга в PostgreSQL."""
import json
import logging

import aio_pika

from .connection import broker, QUEUE_RESULTS_STORAGE
from db import save_product

logger = logging.getLogger(__name__)


async def run_storage_consumer():
    """Читает parser.results и сохраняет в БД."""
    await broker.connect()

    channel = await broker.connection.channel()
    await channel.set_qos(prefetch_count=1)

    queue = await channel.declare_queue(QUEUE_RESULTS_STORAGE, durable=True)

    logger.info(f"Storage consumer запущен, слушает {QUEUE_RESULTS_STORAGE}")

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            async with message.process():
                try:
                    data = json.loads(message.body.decode())
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {e}")
                    continue

                try:
                    product_id = await save_product(data)
                    logger.info(
                        f"Сохранено в БД: id={product_id}, "
                        f"{data.get('marketplace')}, "
                        f"{data.get('title', '')[:40]}"
                    )
                except Exception as e:
                    logger.exception(f"Ошибка сохранения в БД: {e}")