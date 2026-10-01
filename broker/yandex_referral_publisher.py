"""
Раз в 10 минут публикует 1 товар из реферальной программы Яндекс.Маркета.

Источник: https://market.yandex.ru/page/referral_products?tabId=TOP_REWARD
Ссылка: получается через CDP при клике на кнопку «Поделиться» на карточке.

Защита от дублей: product_id в таблице published_referral_products.
"""
import asyncio
import logging
import os
import random
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
from db import get_published_referral_ids, mark_referral_published, save_product
from parsers.yandex import (
    YandexParser,
    parse_search,
    parse_card,
    ParseStatus,
)

logger = logging.getLogger(__name__)

REFERRAL_URL = (
    "https://market.yandex.ru/page/referral_products"
    "?tabId=TOP_REWARD"
    "&generalContext=t%3DcprPage%3Bcpk%3Dreferral_products%3B"
    "&how=explicit_order"
)

PUBLISH_INTERVAL = int(os.getenv("YANDEX_REFERRAL_INTERVAL", "600"))  # сек
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID") or os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")


# ============================================================
# Планировщик
# ============================================================

async def run_yandex_referral_publisher():
    """Раз в PUBLISH_INTERVAL секунд публикует 1 новый товар."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_ID:
        logger.warning("Yandex referral: нет TELEGRAM_BOT_TOKEN/TELEGRAM_GROUP_ID, выключаюсь")
        return

    logger.info(
        "Yandex referral publisher запущен: интервал %d сек (%.1f мин)",
        PUBLISH_INTERVAL, PUBLISH_INTERVAL / 60.0,
    )

    # Первый запуск — сразу, чтобы не ждать 10 минут
    while True:
        try:
            await _publish_one()
        except asyncio.CancelledError:
            logger.info("Yandex referral publisher остановлен")
            raise
        except Exception as e:
            logger.exception("Yandex referral publisher ошибка: %s", e)

        await asyncio.sleep(PUBLISH_INTERVAL)


# ============================================================
# Одна публикация
# ============================================================

async def _publish_one() -> None:
    parser = YandexParser()
    try:
        html = await asyncio.to_thread(parser._cdp_fetch_html, REFERRAL_URL)
        if not html:
            logger.warning("Yandex referral: CDP не вернул HTML")
            return

        items = _parse_referral_page(html)
        if not items:
            logger.warning("Yandex referral: на странице нет товаров")
            return

        logger.info("Yandex referral: на странице %d товаров", len(items))

        published = await get_published_referral_ids()
        fresh = [
            it for it in items
            if it.get("product_id")
               and str(it["product_id"]) not in published
               and it.get("image")
        ]
        if not fresh:
            logger.info("Yandex referral: новых товаров нет")
            return

        random.shuffle(fresh)              # перемешиваем — пробуем в разном порядке
        max_attempts = min(len(fresh), 5)  # не больше 5 попыток за один цикл

        for attempt, candidate in enumerate(fresh[:max_attempts], 1):
            product_id = str(candidate["product_id"])
            product_url = (
                candidate.get("url")
                or f"https://market.yandex.ru/product/{product_id}"
            )
            logger.info(
                "Yandex referral: попытка %d/%d — товар %s — %r",
                attempt, max_attempts, product_id,
                (candidate.get("title") or "")[:60],
            )

            # 1. Ссылка + HTML карточки за один рейс CDP
            result = await asyncio.to_thread(
                parser._cdp_get_referral_link, product_url
            )
            if not result:
                logger.warning(
                    "Yandex referral: у товара %s нет реф. ссылки, пропускаю",
                    product_id,
                )
                continue

            referral_link, card_html = result

            # 2. Обогащаем candidate данными из HTML карточки
            if card_html:
                candidate = _card_from_html(card_html, candidate)
                logger.info(
                    "Yandex referral: цены с карточки — руб=%s, плюс=%s, старое=%s",
                    candidate.get("price_rub"),
                    candidate.get("price_with_plus"),
                    candidate.get("price_old_rub"),
                )
            else:
                logger.warning(
                    "Yandex referral: HTML карточки пуст, публикую с данными рефералки"
                )

            # 2.5. Сохраняем в products (чтобы товар был виден в дашборде)
            try:
                await save_product({
                    "url": referral_link,
                    "marketplace": "yandex",
                    "sku": str(candidate.get("product_id") or ""),
                    "title": candidate.get("title"),
                    "price_rub": candidate.get("price_rub"),
                    "price_with_plus": candidate.get("price_with_plus"),
                    "price_original_rub": candidate.get("price_old_rub"),
                    "seller": candidate.get("seller") or "",
                    "brand": "",
                    "rating": candidate.get("rating"),
                    "rating_count": candidate.get("rating_count"),
                    "in_stock": True,
                    "image_url": candidate.get("image"),
                    "tier_used": "yandex_referral",
                    "post_text": _build_post_text(candidate, referral_link),
                    "success": True,
                    "error": None,
                })
                logger.info("Yandex referral: товар сохранён в products")
            except Exception as e:
                logger.warning("Yandex referral: не удалось сохранить в products: %s", e)

            # 3. Публикуем
            await _send_to_telegram(candidate, referral_link)
            await mark_referral_published(
                product_id=product_id,
                title=candidate.get("title"),
                referral_link=referral_link,
            )
            logger.info("Yandex referral: товар %s опубликован ✅", product_id)
            return

    finally:
        parser.close()


# ============================================================
# Парсинг страницы referral_products
# ============================================================

def _parse_referral_page(html: str) -> list[dict]:
    """
    Парсит HTML страницы реферальных товаров.

    Пробуем два пути:
      1. parse_search() — если структура как у обычного поиска Яндекса.
      2. Собираем product_id из ссылок на карточки (regex-fallback).
    """
    # Попытка 1: переиспользуем SSR-парсер поиска
    try:
        parsed = parse_search(html)
        if parsed.get("status") not in (ParseStatus.CAPTCHA, ParseStatus.EMPTY):
            items = parsed.get("items") or []
            if items:
                logger.info("Yandex referral: parse_search дал %d товаров", len(items))
                return items
    except Exception as e:
        logger.warning("Yandex referral: parse_search упал: %s", e)

    # Попытка 2: regex по HTML (если SPA-версия)
    import re
    seen: set[str] = set()
    items: list[dict] = []
    for m in re.finditer(
            r'/(?:product(?:--[^/"]+)?|card/[^/"]+)/(\d{6,})',
            html,
    ):
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        items.append({
            "product_id": pid,
            "url": f"https://market.yandex.ru/product/{pid}",
            "title": None,
            "image": None,
            "price_rub": None,
            "price_with_plus": None,
        })

    logger.info("Yandex referral: regex-fallback дал %d товаров", len(items))
    return items


def _card_from_html(html: str, fallback_item: dict) -> dict:
    """
    Парсит HTML карточки через parse_card() и возвращает dict полей,
    совместимый с candidate из _parse_referral_page().

    Пустые поля из карточки не затирают данные referral-страницы —
    используется fallback_item.
    """
    try:
        card = parse_card(html)
    except Exception as e:
        logger.warning("Yandex referral: parse_card упал: %s", e)
        return fallback_item

    if card.get("status") == ParseStatus.CAPTCHA:
        logger.warning("Yandex referral: на карточке капча, беру данные рефералки")
        return fallback_item

    def pick(new, old):
        return new if new not in (None, "", 0) else old

    return {
        **fallback_item,
        "title": pick(card.get("title"), fallback_item.get("title")),
        "image": pick(card.get("image"), fallback_item.get("image")),
        "price_rub": pick(card.get("price_rub"), fallback_item.get("price_rub")),
        "price_with_plus": pick(
            card.get("price_with_plus"), fallback_item.get("price_with_plus")
        ),
        "price_old_rub": pick(
            card.get("price_before_discount_rub"),
            fallback_item.get("price_old_rub"),
        ),
        "rating": pick(card.get("rating"), fallback_item.get("rating")),
        "rating_count": pick(card.get("rating_count"), fallback_item.get("rating_count")),
        "seller": pick(card.get("seller"), fallback_item.get("seller")),
        "sku_id": card.get("sku_id") or fallback_item.get("product_id"),
    }


# ============================================================
# Telegram (в стиле telegram_consumer.py)
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
        logger.warning("Yandex referral: не удалось скачать фото %s: %s", url, e)
        return None


def _build_post_text(item: dict, referral_link: str) -> str:
    """
    Пост для реферального товара через общий TextGenerator.

    Маппинг:
      price_rub          ← Pay-цена (главная для показа), fallback — обычная
      price_original_rub ← зачёркнутая (или обычная, если она больше главной)
      price_with_plus    ← None (чтобы TG не подставил «Стало: плюс»)
      url                ← реферальная ссылка
    """
    pay = item.get("price_with_plus")      # цена с Яндекс Пэй
    regular = item.get("price_rub")        # обычная цена
    old = item.get("price_old_rub")        # зачёркнутая

    primary = pay if pay else regular

    # «Было» — только если реально больше главной
    old_for_post = None
    if old and primary and old > primary:
        old_for_post = old
    elif regular and primary and regular > primary:
        old_for_post = regular

    product = ProductData(
        title=item.get("title") or "Товар Яндекс.Маркета",
        price_rub=primary,
        price_with_plus=None,
        price_original_rub=old_for_post,
        url=referral_link,
        marketplace="yandex",
        sku=str(item.get("product_id") or ""),
        image_url=item.get("image"),
        success=True,
        tier_used="yandex_referral",
    )
    return TextGenerator.generate(product)


async def _send_to_telegram(item: dict, referral_link: str) -> None:
    bot = _make_bot()
    try:
        text = _build_post_text(item, referral_link)
        image_url = item.get("image")

        if image_url:
            img = await _download_image(image_url)
            if img:
                try:
                    await bot.send_photo(
                        chat_id=TELEGRAM_GROUP_ID,
                        photo=BufferedInputFile(img, filename="product.jpg"),
                        caption=text[:1024],
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return
                except Exception as e:
                    logger.warning("Yandex referral: send_photo упал: %s", e)

        await bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass