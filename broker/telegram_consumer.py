"""
Telegram-consumer: публикует результаты парсинга в ГРУППУ.

Стиль и API бота — 1:1 с yandex_referral_publisher.py:
  • AiohttpSession(proxy=(url, BasicAuth))
  • TELEGRAM_GROUP_ID (fallback — TELEGRAM_CHANNEL_ID)

Логика:
  • ВСЕ маркетплейсы проходят через overlay_maker (render_product_card).
  • Оригинал фото всегда сохраняется в published_media (для VK-клипа).
"""
import json
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import httpx
from aiohttp import BasicAuth
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from .connection import broker, QUEUE_RESULTS_TELEGRAM
from core.telegram_links import build_message_link
from services.media_storage import save_photo
from services.overlay_maker import render_product_card
from db import save_media, save_published_message
from worker_health import update_last_message


logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = (
    os.getenv("TELEGRAM_GROUP_ID")
    or os.getenv("TELEGRAM_CHANNEL_ID", "")
)
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")

# Аспект карточки
CARD_ASPECT = os.getenv("TELEGRAM_CARD_ASPECT", "3:4.5")

# Юр. тексты
LEGAL_TEXT = {
    "ozon":        "Реклама. ООО «Интернет Решения», ИНН 7704217370",
    "wildberries": "Реклама. ООО «Вайлдберриз», ИНН 7721546864",
    "yandex":      "Реклама. ООО «ЯНДЕКС МАРКЕТ», ИНН 9704254424",
    "aliexpress":  "Реклама. ООО «АЛИБАБА.КОМ (РУ)», ИНН 7715067263",
}


# ============================================================
# Telegram (1:1 с yandex_referral_publisher.py)
# ============================================================

def _make_bot() -> Bot:
    if TELEGRAM_PROXY_URL:
        parsed = urlparse(TELEGRAM_PROXY_URL)
        auth = (
            BasicAuth(login=parsed.username, password=parsed.password)
            if parsed.username and parsed.password else None
        )
        proxy_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        session = AiohttpSession(proxy=(proxy_url, auth))
    else:
        session = AiohttpSession()
    return Bot(token=TELEGRAM_BOT_TOKEN, session=session)


async def _download_image(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.warning("telegram_consumer: не удалось скачать фото %s: %s", url, e)
        return None


async def _send_to_telegram(data: dict) -> None:
    """Отправляет пост в группу с фото (карточка или оригинал) + текст."""
    url = data.get("url", "?")
    image_url = data.get("image_url")
    post_text = data.get("post_text") or ""
    marketplace = (data.get("marketplace") or "").lower()

    if not post_text:
        logger.warning("telegram_consumer: нет post_text — %s", url)
        return

    bot = _make_bot()
    try:
        if image_url:
            img = await _download_image(image_url)
            if img:
                # === 1. Сначала рендерим карточку ===
                try:
                    styled = render_product_card(
                        img,
                        marketplace=marketplace,
                        title=data.get("title") or "",
                        brand=data.get("brand") or data.get("seller") or "",
                        price_rub=data.get("price_rub"),
                        price_with_plus=data.get("price_with_plus"),
                        price_original_rub=data.get("price_original_rub"),
                        rating=data.get("rating"),
                        rating_count=data.get("rating_count"),
                        legal_text=LEGAL_TEXT.get(marketplace, ""),
                        promo_badge="",
                        aspect=CARD_ASPECT,
                    )
                    if styled:
                        img = styled
                        logger.info(
                            "telegram_consumer: карточка отрендерена (%s)",
                            CARD_ASPECT,
                        )
                except Exception as e:
                    logger.warning(
                        "telegram_consumer: рендер карточки упал: %s", e
                    )

                # === 2. Сохраняем УЖЕ ОТРЕНДЕРЕННУЮ карточку ===
                try:
                    local_path = save_photo(
                        img, image_url, source="telegram_consumer"
                    )
                    if local_path:
                        await save_media(
                            source="telegram_consumer",
                            image_path=local_path,
                            marketplace=marketplace,
                            title=data.get("title"),
                            brand=data.get("brand") or data.get("seller") or "",
                            price_rub=data.get("price_rub"),
                            price_with_plus=data.get("price_with_plus"),
                            price_original_rub=data.get("price_original_rub"),
                            rating=data.get("rating"),
                            rating_count=data.get("rating_count"),
                            legal_text=LEGAL_TEXT.get(marketplace, ""),
                            post_text=post_text,
                            image_url=image_url,
                        )
                except Exception as e:
                    logger.warning(
                        "telegram_consumer: не удалось сохранить карточку: %s", e
                    )

                # === 3. Отправляем в Telegram ===
                try:
                    sent = await bot.send_photo(
                        chat_id=TELEGRAM_GROUP_ID,
                        photo=BufferedInputFile(img, filename="product.jpg"),
                        caption=post_text[:1024],
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    logger.info(
                        "telegram_consumer: пост с фото отправлен — %s", url
                    )

                    # === Сохраняем ссылку на сообщение ===
                    try:
                        link = build_message_link(
                            chat_id=sent.chat.id,
                            message_id=sent.message_id,
                            chat_username=getattr(sent.chat, "username", None),
                        )
                        await save_published_message(
                            chat_id=str(sent.chat.id),
                            message_id=sent.message_id,
                            message_link=link,
                            source_url=data.get("url"),
                            marketplace=marketplace,
                            title=data.get("title"),
                        )
                        logger.info("telegram_consumer: ссылка сохранена — %s", link)
                    except Exception as e:
                        logger.warning("telegram_consumer: не удалось сохранить ссылку: %s", e)

                    return
                except Exception as e:
                    logger.warning(
                        "telegram_consumer: send_photo упал: %s. "
                        "Шлю только текст.", e
                    )

        # Fallback — только текст
        sent = await bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text=post_text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )
        logger.info("telegram_consumer: пост (без фото) отправлен — %s", url)

        # === Сохраняем ссылку на сообщение ===
        try:
            link = build_message_link(
                chat_id=sent.chat.id,
                message_id=sent.message_id,
                chat_username=getattr(sent.chat, "username", None),
            )
            await save_published_message(
                chat_id=str(sent.chat.id),
                message_id=sent.message_id,
                message_link=link,
                source_url=data.get("url"),
                marketplace=marketplace,
                title=data.get("title"),
            )
            logger.info("telegram_consumer: ссылка сохранена — %s", link)
        except Exception as e:
            logger.warning("telegram_consumer: не удалось сохранить ссылку: %s", e)

    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


# ============================================================
# Consumer
# ============================================================

async def run_telegram_consumer():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_ID:
        logger.warning(
            "TELEGRAM_BOT_TOKEN или TELEGRAM_GROUP_ID не заданы, "
            "пропускаю telegram_consumer"
        )
        return

    await broker.connect()

    channel = await broker.connection.channel()
    await channel.set_qos(prefetch_count=1)
    queue = await channel.declare_queue(QUEUE_RESULTS_TELEGRAM, durable=True)

    logger.info(
        "Telegram consumer запущен → группа %s, слушает %s",
        TELEGRAM_GROUP_ID, QUEUE_RESULTS_TELEGRAM,
    )

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            async with message.process():
                try:
                    update_last_message()
                    data = json.loads(message.body.decode())
                    url = data.get("url", "?")

                    # === Фильтры ===
                    if not data.get("success"):
                        logger.info(f"Пропускаю (success=false): {url}")
                        continue

                    # === Товар уже в БД — не публикуем повторно ===
                    if data.get("already_exists"):
                        logger.info(
                            f"Пропускаю (товар уже в БД): {url}"
                        )
                        continue

                    price_rub = data.get("price_rub")
                    price_plus = data.get("price_with_plus")
                    if price_rub is None and price_plus is None:
                        logger.warning(f"Пропускаю: цена не найдена — {url}")
                        continue

                    in_stock = data.get("in_stock")
                    if in_stock is False:
                        logger.warning(
                            f"Пропускаю: товара нет в наличии — {url}"
                        )
                        continue

                    # === Публикация ===
                    await _send_to_telegram(data)

                except Exception as e:
                    logger.exception(f"Ошибка обработки сообщения: {e}")