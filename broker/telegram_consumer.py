"""Telegram-consumer: постит post_text + фото в канал."""
import json
import logging
import os
from urllib.parse import urlparse

import httpx
from aiohttp import BasicAuth
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from .connection import broker, QUEUE_RESULTS_TELEGRAM

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")


async def _download_image(image_url: str) -> bytes | None:
    """
    Скачивает картинку по URL.
    Возвращает байты или None при ошибке.
    """
    try:
        # Для Telegram-прокси (SOCKS5) httpx не подходит напрямую,
        # но картинки маркетплейсов доступны и без прокси.
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.warning(f"Не удалось скачать картинку {image_url}: {e}")
        return None


def _image_filename(image_url: str) -> str:
    """Определяет имя файла по URL."""
    url_low = image_url.lower()
    if ".jpg" in url_low or ".jpeg" in url_low:
        return "product.jpg"
    if ".png" in url_low:
        return "product.png"
    if ".webp" in url_low:
        return "product.webp"
    return "product.jpg"


async def run_telegram_consumer():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.warning("TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID не заданы, пропускаю")
        return

    await broker.connect()

    # === Настройка прокси для Telegram ===
    if TELEGRAM_PROXY_URL:
        logger.info(f"Использую прокси для Telegram: {TELEGRAM_PROXY_URL}")
        parsed = urlparse(TELEGRAM_PROXY_URL)
        auth = None
        if parsed.username and parsed.password:
            auth = BasicAuth(login=parsed.username, password=parsed.password)
        proxy_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        session = AiohttpSession(proxy=(proxy_url, auth))
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
                    url = data.get("url", "?")

                    # === 1. Проверка успеха парсинга ===
                    if not data.get("success"):
                        logger.info(f"Пропускаю (success=false): {url}")
                        continue

                    # === 2. Проверка цены ===
                    price_rub = data.get("price_rub")
                    price_plus = data.get("price_with_plus")
                    if price_rub is None and price_plus is None:
                        logger.warning(f"Пропускаю: цена не найдена для {url}")
                        continue

                    # === 3. Проверка наличия ===
                    in_stock = data.get("in_stock")
                    if in_stock is False:
                        logger.warning(f"Пропускаю: товара нет в наличии — {url}")
                        continue

                    # === 4. Проверка текста ===
                    post_text = data.get("post_text")
                    if not post_text:
                        logger.warning(f"Пропускаю: нет post_text — {url}")
                        continue

                    # === 5. Отправка в Telegram ===
                    image_url = data.get("image_url")

                    if image_url:
                        # Скачиваем картинку и отправляем как файл
                        img_bytes = await _download_image(image_url)
                        if img_bytes:
                            try:
                                await bot.send_photo(
                                    chat_id=TELEGRAM_CHANNEL_ID,
                                    photo=BufferedInputFile(
                                        img_bytes,
                                        filename=_image_filename(image_url),
                                    ),
                                    caption=post_text[:1024],
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                                logger.info(f"Пост с фото отправлен: {url}")
                                continue
                            except Exception as e:
                                logger.warning(f"send_photo упал: {e}. Отправляю только текст.")
                        else:
                            logger.warning(f"Картинка не скачалась, отправляю только текст: {url}")

                    # Fallback — только текст
                    await bot.send_message(
                        chat_id=TELEGRAM_CHANNEL_ID,
                        text=post_text,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    logger.info(f"Пост отправлен (без фото): {url}")

                except Exception as e:
                    logger.exception(f"Ошибка обработки сообщения: {e}")