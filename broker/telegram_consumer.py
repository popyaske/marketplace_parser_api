"""Telegram-consumer: постит post_text в канал."""
import json
import logging
import os

from aiohttp import BasicAuth
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram import Bot
from aiogram.enums import ParseMode

from .connection import broker, QUEUE_RESULTS_TELEGRAM

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")


async def run_telegram_consumer():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.warning("TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID не заданы, пропускаю")
        return

    await broker.connect()

    # Создаём сессию с прокси, если он задан
    if TELEGRAM_PROXY_URL:
        logger.info(f"Использую прокси для Telegram: {TELEGRAM_PROXY_URL}")
        # Парсим URL на компоненты
        from urllib.parse import urlparse
        parsed = urlparse(TELEGRAM_PROXY_URL)
        auth = None
        if parsed.username and parsed.password:
            auth = BasicAuth(login=parsed.username, password=parsed.password)

        proxy_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        session = AiohttpSession(proxy=(proxy_url, auth))  # Передаём кортеж с auth[reference:1]
    else:
        session = AiohttpSession()

    bot = Bot(token=TELEGRAM_BOT_TOKEN, session=session)

    channel = await broker.connection.channel()
    await channel.set_qos(prefetch_count=1)
    queue = await channel.declare_queue(QUEUE_RESULTS_TELEGRAM, durable=True)

    logger.info(f"Telegram consumer запущен, слушает {QUEUE_RESULTS_TELEGRAM}")

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            async with message.process():
                try:
                    data = json.loads(message.body.decode())
                    post_text = data.get("post_text")
                    if not data.get("success") or not post_text:
                        continue

                    await bot.send_message(
                        chat_id=TELEGRAM_CHANNEL_ID,
                        text=post_text,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    logger.info(f"Пост отправлен: {data.get('url')}")
                except Exception as e:
                    logger.exception(f"Ошибка отправки: {e}")