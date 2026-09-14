"""Consumer — читает URL из очереди, парсит, публикует результат."""
import json
import logging
import asyncio
from typing import Optional

import aio_pika

from .connection import (
    broker,
    QUEUE_INCOMING,
    QUEUE_RESULTS,
    QUEUE_ERRORS,
)
from services.marketplace import MarketplaceFactory
from core.formatters import PriceFormatter, TextGenerator

logger = logging.getLogger(__name__)


async def process_url(url: str) -> dict:
    """
    Обработать один URL: выбрать парсер, спарсить, вернуть результат.

    Returns:
        dict с полями success, marketplace, title, price_rub, post_text, error
    """
    factory = MarketplaceFactory()

    try:
        key, parser = factory.get_parser(url)
    except ValueError as e:
        return {
            "success": False,
            "url": url,
            "error": str(e),
        }

    try:
        data = parser.fetch(url)
    except Exception as e:
        logger.exception(f"Ошибка парсинга {url}")
        return {
            "success": False,
            "url": url,
            "marketplace": key,
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        try:
            parser.close()
        except Exception:
            pass

    if not data.success:
        return {
            "success": False,
            "url": url,
            "marketplace": key,
            "error": data.error or "Не удалось получить данные",
        }

    # Форматируем цены
    price_rub = PriceFormatter.format_price(str(data.price_rub)) if data.price_rub else None
    price_plus = PriceFormatter.format_price(str(data.price_with_plus)) if data.price_with_plus else None
    price_original = PriceFormatter.format_price(str(data.price_original_rub)) if data.price_original_rub else None

    post_text = TextGenerator.generate(data)

    return {
        "success": True,
        "url": url,
        "marketplace": key,
        "sku": data.sku,
        "title": data.title,
        "price_rub": data.price_rub,
        "price_with_plus": data.price_with_plus,
        "price_original_rub": data.price_original_rub,
        "price_rub_formatted": price_rub,
        "price_with_plus_formatted": price_plus,
        "price_original_formatted": price_original,
        "seller": data.seller,
        "brand": data.brand,
        "rating": data.rating,
        "rating_count": data.rating_count,
        "in_stock": data.in_stock,
        "image_url": data.image_url,
        "tier_used": data.tier_used,
        "post_text": post_text,
    }


async def handle_message(message: aio_pika.IncomingMessage):
    """Обработать одно сообщение из очереди."""
    async with message.process():
        try:
            payload = json.loads(message.body.decode())
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            await broker.publish(QUEUE_ERRORS, {
                "error": f"Invalid JSON: {e}",
                "raw": message.body.decode()[:500],
            })
            return

        url = payload.get("url")
        if not url:
            logger.error("Нет URL в сообщении")
            return

        meta = payload.get("meta", {})
        logger.info(f"Обрабатываю URL: {url}")

        result = await process_url(url)
        result["meta"] = meta

        # Публикуем результат
        queue = QUEUE_RESULTS if result["success"] else QUEUE_ERRORS
        await broker.publish(queue, result)
        logger.info(f"Результат опубликован: {queue} — {url}")


async def run_consumer():
    """Запустить consumer — бесконечный цикл обработки сообщений."""
    await broker.connect()  # уже с retry из Патча 1

    queue = await broker.channel.declare_queue(QUEUE_INCOMING, durable=True)
    await broker.channel.set_qos(prefetch_count=1)

    logger.info(f"Consumer запущен, слушает {QUEUE_INCOMING}")

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            try:
                await handle_message(message)
            except Exception as e:
                logger.exception(f"Ошибка обработки сообщения: {e}")