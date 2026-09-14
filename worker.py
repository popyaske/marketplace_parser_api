"""Worker — слушает очередь RabbitMQ и парсит URL."""
import asyncio
import logging
import os
import sys

from broker.consumer import run_consumer

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("worker")


async def main():
    logger.info("Запуск воркера...")
    while True:
        try:
            await run_consumer()
        except KeyboardInterrupt:
            logger.info("Остановка воркера")
            return
        except Exception as e:
            logger.exception(f"Ошибка: {e}. Перезапуск через 5 секунд...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)