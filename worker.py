"""Worker — слушает RabbitMQ: парсит URL и сохраняет результаты в БД."""
import asyncio
import logging
import os
import sys

from core.logging_setup import setup_logging
from broker.consumer import run_consumer
from broker.daily_publisher import run_daily_publisher
from broker.vk_digest_publisher import run_vk_digest_publisher
from broker.yandex_referral_publisher import run_yandex_referral_publisher, run_referral_manual_consumer
from broker.storage_consumer import run_storage_consumer
from db import init_engine
from sqlalchemy import text as _sql_text


setup_logging()
logger = logging.getLogger("worker")


async def _check_db():
    engine = init_engine()
    async with engine.connect() as conn:
        await conn.execute(_sql_text("SELECT 1"))


async def main():
    logger.info("Запуск воркера...")

    try:
        await _check_db()
        logger.info("PostgreSQL доступен")
    except Exception as e:
        logger.exception("PostgreSQL недоступен: %s", e)
        raise

    from worker_health import run_health_server
    from broker.telegram_consumer import run_telegram_consumer

    TASKS = (
        ("consumer", run_consumer),
        ("storage_consumer", run_storage_consumer),
        ("telegram_consumer", run_telegram_consumer),
        ("health", run_health_server),
        ("yandex_referral", run_yandex_referral_publisher),
        ("referral_manual", run_referral_manual_consumer),
        ("vk_digest", run_vk_digest_publisher),
        ("daily_publisher", run_daily_publisher),
    )

    while True:
        tasks = {name: asyncio.create_task(fn(), name=name) for name, fn in TASKS}
        try:
            done, pending = await asyncio.wait(
                tasks.values(),
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for t in done:
                exc = t.exception()
                if exc is not None:
                    logger.error("Task %s умер: %s", t.get_name(), exc, exc_info=exc)
                else:
                    logger.warning("Task %s завершился сам", t.get_name())
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        except KeyboardInterrupt:
            for t in tasks.values():
                t.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            logger.info("Остановка воркера")
            return

        logger.info("Перезапуск всех консьюмеров через 5 секунд...")
        await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)