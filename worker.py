"""Worker — слушает RabbitMQ: парсит URL и сохраняет результаты в БД."""
import asyncio
import logging
import os
import sys

from broker.consumer import run_consumer
from broker.daily_publisher import run_daily_publisher
from broker.yandex_referral_publisher import run_yandex_referral_publisher
from broker.storage_consumer import run_storage_consumer
from db import create_tables


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("worker")


async def main():
    logger.info("Запуск воркера...")
    
    # Создаём таблицы (если не существуют)
    try:
        await create_tables()
        logger.info("Таблицы БД готовы")
    except Exception as e:
        logger.exception(f"Не удалось создать таблицы: {e}")
        await asyncio.sleep(5)
        await create_tables()

    from worker_health import run_health_server
    from broker.telegram_consumer import run_telegram_consumer

    while True:
        try:
            await asyncio.gather(
                run_consumer(),
                run_storage_consumer(),
                run_telegram_consumer(),
                run_health_server(),
                #run_daily_publisher(),
                run_yandex_referral_publisher(),
            )
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