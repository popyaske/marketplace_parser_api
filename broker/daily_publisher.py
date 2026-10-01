"""
Ежедневная публикация 1 товара из каталога Такпродам в Telegram-группу.

Использование:
  - раз в сутки в PUBLISH_HOUR:00 (в таймзоне PUBLISH_TZ_OFFSET) запрашиваем
    каталог товаров через API takprodam.ru;
  - фильтруем уже опубликованные (таблица published_products);
  - выбираем 1 случайный новый товар;
  - публикуем в Telegram-группу (фото + текст);
  - отмечаем факт публикации в БД (защита от повторов).

Формат поста — общий TextGenerator из core.formatters (тот же, что
использует broker/telegram_consumer.py). Логика отправки (send_photo
с BufferedInputFile, fallback send_message, прокси) — тоже из
telegram_consumer.py.

Дубликаты исключены на уровне БД: UNIQUE(published_products.takprodam_id).
"""
import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from aiohttp import BasicAuth
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from core.formatters import TextGenerator
from core.models import ProductData
from db import get_published_ids, mark_published
from services.takprodam import TakProDamClient

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID") or os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")

TAKPRODAM_API_TOKEN = os.getenv("TAKPRODAM_API_TOKEN", "")
TAKPRODAM_SOURCE_ID = int(os.getenv("TAKPRODAM_SOURCE_ID", "0") or 0)
TAKPRODAM_MARKETPLACE = os.getenv("TAKPRODAM_MARKETPLACE", "")  # Ozon / Wildberries / Avito / Aliexpress
TAKPRODAM_SUBID = os.getenv("TAKPRODAM_SUBID", "tg_daily")

PUBLISH_HOUR = int(os.getenv("PUBLISH_HOUR", "20"))            # час по локальному времени
PUBLISH_TZ_OFFSET = int(os.getenv("PUBLISH_TZ_OFFSET", "3"))    # смещение таймзоны (МСК = +3)


# ============================================================
# Такпродам → ProductData (чтобы использовать общий TextGenerator)
# ============================================================

def _normalize_marketplace(title: Optional[str]) -> str:
    """'Wildberries' → 'wildberries', 'AliExpress' → 'aliexpress'."""
    if not title:
        return "unknown"
    return title.strip().lower().replace(" ", "")


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        n = float(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _takprodam_to_product(item: dict) -> ProductData:
    """
    Конвертация товара из каталога Такпродам в ProductData.

    Маппинг:
      title              → title
      price              → price_rub
      price_discount     → price_with_plus (есть только у акционных товаров)
      tracking_link      → url (партнёрская ссылка; fallback — external_link)
      marketplace_title  → marketplace (в lowercase, для хештега)
      store_title        → seller
      image_url          → image_url
    """
    marketplace = _normalize_marketplace(item.get("marketplace_title"))
    url = item.get("tracking_link") or item.get("external_link") or ""

    return ProductData(
        title=item.get("title") or "Не найдено",
        price_rub=_to_float(item.get("price")),
        price_with_plus=_to_float(item.get("price_discount")),
        price_original_rub=None,
        url=url,
        marketplace=marketplace,
        sku=str(item.get("product_id") or item.get("product_sku") or ""),
        seller=item.get("store_title") or "",
        brand="",
        image_url=item.get("image_url"),
        success=True,
        tier_used="takprodam",
    )


def _build_post_text(item: dict) -> str:
    """
    Собрать текст поста через общий TextGenerator + маркировку рекламы.

    Формат получается ровно такой же, как у telegram_consumer.py:
        🚨 **{title}**

        ❌ Было: X₽           (если есть)
        ✅ Стало: **Y₽**

        📌 Итоговая цена зависит от аккаунта

        🛒 [Забрать товар](tracking_link)

        💬 [Чат для обсуждений](...)

        🔵 #Ozon
    """
    product = _takprodam_to_product(item)
    text = TextGenerator.generate(product)

    # Маркировка рекламы — обязательна по закону
    legal = (item.get("legal_text") or "").strip()
    if legal:
        text = f"{text}\n\n{legal}"

    return text


# ============================================================
# Планировщик
# ============================================================

def _next_run_at(now_utc: datetime) -> datetime:
    """Следующий момент PUBLISH_HOUR:00 по локальному времени."""
    tz = timezone(timedelta(hours=PUBLISH_TZ_OFFSET))
    now_local = now_utc.astimezone(tz)
    target = now_local.replace(hour=PUBLISH_HOUR, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc)


async def run_daily_publisher():
    """Основной цикл. Никогда не падает — ловит все исключения."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_ID:
        logger.warning("Daily publisher: нет TELEGRAM_BOT_TOKEN/TELEGRAM_GROUP_ID, выключаюсь")
        return
    if not TAKPRODAM_API_TOKEN or not TAKPRODAM_SOURCE_ID:
        logger.warning("Daily publisher: нет TAKPRODAM_API_TOKEN/TAKPRODAM_SOURCE_ID, выключаюсь")
        return

    logger.info(
        "Daily publisher запущен: публикация каждый день в %02d:00 (UTC+%d), source_id=%d",
        PUBLISH_HOUR, PUBLISH_TZ_OFFSET, TAKPRODAM_SOURCE_ID,
    )

    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = _next_run_at(now)
            wait = (next_run - now).total_seconds()
            logger.info(
                "Daily publisher: следующий запуск через %.1f ч (%s UTC)",
                wait / 3600.0, next_run.strftime("%Y-%m-%d %H:%M"),
            )
            await asyncio.sleep(wait)
            await _publish_one()
        except asyncio.CancelledError:
            logger.info("Daily publisher остановлен")
            raise
        except Exception as e:
            logger.exception("Daily publisher ошибка: %s", e)
            await asyncio.sleep(300)  # повтор через 5 минут


# ============================================================
# Одна публикация
# ============================================================

async def _publish_one() -> None:
    """Найти 1 новый товар и опубликовать его в группу."""
    published_ids = await get_published_ids()
    logger.info("Daily publisher: уже опубликовано %d товаров", len(published_ids))

    client = TakProDamClient(TAKPRODAM_API_TOKEN)
    candidate: Optional[dict] = None
    try:
        page = 1
        while page <= 20 and candidate is None:
            data = client.get_products(
                source_id=TAKPRODAM_SOURCE_ID,
                marketplace=TAKPRODAM_MARKETPLACE or None,
                subid=TAKPRODAM_SUBID,
                page=page,
                limit=100,
            )
            items = data.get("items") or []
            if not items:
                break

            # Отсеиваем всё, что уже публиковалось
            fresh = [it for it in items if str(it.get("id")) not in published_ids]
            if fresh:
                candidate = random.choice(fresh)
                break
            page += 1

        if candidate is None:
            logger.warning("Daily publisher: новых товаров нет (всё уже опубликовано)")
            return

        # Публикуем → только потом помечаем (чтобы при сбое можно было повторить)
        await _send_to_telegram(candidate)
        await mark_published(candidate)
        logger.info(
            "Daily publisher: опубликован товар takprodam_id=%s, title=%r",
            candidate.get("id"), (candidate.get("title") or "")[:80],
        )
    finally:
        client.close()


# ============================================================
# Telegram (1:1 с telegram_consumer.py)
# ============================================================

def _make_bot() -> Bot:
    """Bot с опциональным прокси — как в telegram_consumer.py."""
    if TELEGRAM_PROXY_URL:
        logger.info("Daily publisher: использую прокси для Telegram: %s", TELEGRAM_PROXY_URL)
        parsed = urlparse(TELEGRAM_PROXY_URL)
        auth = None
        if parsed.username and parsed.password:
            auth = BasicAuth(login=parsed.username, password=parsed.password)
        proxy_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        session = AiohttpSession(proxy=(proxy_url, auth))
    else:
        session = AiohttpSession()
    return Bot(token=TELEGRAM_BOT_TOKEN, session=session)


async def _download_image(image_url: str) -> Optional[bytes]:
    """Скачать картинку — как в telegram_consumer.py."""
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.warning("Daily publisher: не удалось скачать картинку %s: %s", image_url, e)
        return None


def _image_filename(image_url: str) -> str:
    """Определить имя файла по URL — как в telegram_consumer.py."""
    url_low = image_url.lower()
    if ".jpg" in url_low or ".jpeg" in url_low:
        return "product.jpg"
    if ".png" in url_low:
        return "product.png"
    if ".webp" in url_low:
        return "product.webp"
    return "product.jpg"


async def _send_to_telegram(item: dict) -> None:
    """
    Отправка в Telegram — повторяет логику telegram_consumer.py:
      1. text — через общий TextGenerator (core.formatters);
      2. если есть image_url → send_photo с BufferedInputFile;
      3. fallback → send_message только с текстом.
    """
    post_text = _build_post_text(item)
    image_url = item.get("image_url")

    bot = _make_bot()
    try:
        if image_url:
            img_bytes = await _download_image(image_url)
            if img_bytes:
                try:
                    await bot.send_photo(
                        chat_id=TELEGRAM_GROUP_ID,
                        photo=BufferedInputFile(
                            img_bytes,
                            filename=_image_filename(image_url),
                        ),
                        caption=post_text[:1024],
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    logger.info("Daily publisher: пост с фото отправлен")
                    return
                except Exception as e:
                    logger.warning(
                        "Daily publisher: send_photo упал: %s. Отправляю только текст.", e
                    )
            else:
                logger.warning("Daily publisher: картинка не скачалась, отправляю только текст")

        # Fallback — только текст
        await bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text=post_text,
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info("Daily publisher: пост отправлен (без фото)")
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass