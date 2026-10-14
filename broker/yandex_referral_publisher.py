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
import time
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote
import httpx as _httpx

import httpx
from aiohttp import BasicAuth
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from core.formatters import TextGenerator
from core.models import ProductData
from core.telegram_links import build_message_link
from db import (
    get_published_referral_ids,
    mark_referral_published,
    save_product,
    save_media,
    cleanup_old_referral_publications,
    product_exists,
    save_published_message,
)
from parsers.yandex import (
    YandexParser,
    parse_search,
    parse_card,
    ParseStatus,
)
from services.media_storage import save_photo
from services.overlay_maker import render_product_card
from worker_health import update_last_message


logger = logging.getLogger(__name__)

REFERRAL_BASE = (
    "https://market.yandex.ru/page/referral_products"
    "?tabId=TOP_REWARD"
    "&generalContext=t%3DcprPage%3Bcpk%3Dreferral_products%3B"
    "&how=explicit_order"
)

REFERRAL_MAX_PAGES = int(os.getenv("YANDEX_REFERRAL_MAX_PAGES", "100"))
REFERRAL_EMPTY_STREAK_LIMIT = int(os.getenv("YANDEX_REFERRAL_EMPTY_STREAK", "3"))
REFERRAL_PUBLISH_DELAY = float(os.getenv("YANDEX_REFERRAL_PUBLISH_DELAY", "12.0"))
PUBLISH_INTERVAL = int(os.getenv("YANDEX_REFERRAL_INTERVAL", "3600"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID") or os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")

_cdp_lock = asyncio.Lock()

def _resolve_yandex_cc(url: str, timeout: float = 10.0) -> str:
    if "/cc/" not in url.lower():
        return url

    headers = {
        "User-Agent": "Mozilla/5.0 ... Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }

    def _clean(loc: str) -> Optional[str]:
        if not loc:
            return None
        if loc.startswith("/"):
            loc = "https://market.yandex.ru" + loc
        return loc if ("/card/" in loc or "/product" in loc) else None

    # шаг 1: прямой запрос без следования редиректу
    try:
        r = _httpx.get(url, timeout=timeout, follow_redirects=False, headers=headers)
        loc = _clean(r.headers.get("location", ""))
        if loc:
            logger.info("referral: /cc/ развёрнут (1 шаг) — %s", loc[:120])
            return loc
    except Exception as e:
        logger.warning("referral: httpx не развернул /cc/: %s", e)

    # шаг 2: retpath из showcaptcha
    try:
        r = _httpx.get(url, timeout=timeout, follow_redirects=False, headers=headers)
        loc = r.headers.get("location", "")
        if "showcaptcha" in loc:
            qs = parse_qs(urlparse(loc).query)
            retpath = (qs.get("retpath") or [None])[0]
            if retpath:
                decoded = _decode_retpath(retpath)
                if decoded:
                    logger.info("referral: /cc/ развёрнут (retpath) — %s", decoded[:120])
                    return decoded
    except Exception as e:
        logger.warning("referral: retpath-фолбэк упал: %s", e)

    logger.error(
        "referral: НЕ удалось развернуть /cc/ — формат ответа изменился? url=%s",
        url,
    )
    return url


def _decode_retpath(retpath: str) -> Optional[str]:
    """Yandex отдаёт retpath как URL-encoded base64, иногда с `,,<sig>`."""
    import base64
    from urllib.parse import unquote

    clean = unquote(retpath).split(",")[0]
    pad = "=" * (-len(clean) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(clean + pad).decode("utf-8", "ignore")
        except Exception:
            continue
        if "/card/" in decoded or "/product" in decoded:
            return decoded
    logger.warning("referral: не удалось декодировать retpath=%s", retpath[:80])
    return None


def _build_referral_url(page: int) -> str:
    if page <= 1:
        return REFERRAL_BASE
    return f"{REFERRAL_BASE}&page={page}"


async def _publish_candidate(parser: YandexParser, candidate: dict) -> bool:
    """
    Публикует один товар-кандидат (dict с product_id, url, title, ...).
    Возвращает True при успехе.

    Использует тот же _cdp_lock, что и _publish_one_url,
    поэтому параллельный запуск не сломает вкладку market.yandex.ru.
    """
    product_id = str(candidate.get("product_id") or "")
    if not product_id:
        logger.warning("Yandex referral: кандидат без product_id — пропускаю")
        return False

    product_url = (
        candidate.get("url")
        or f"https://market.yandex.ru/product/{product_id}"
    )

    # 1. Ссылка + HTML карточки за один рейс CDP
    async with _cdp_lock:
        result = await asyncio.to_thread(
            parser._cdp_get_referral_link, product_url
        )
    if not result:
        logger.warning(
            "Yandex referral: у товара %s нет реф. ссылки, помечаю и пропускаю",
            product_id,
        )
        # Помечаем — чтобы больше не пытаться
        await mark_referral_published(
            product_id=product_id,
            title=candidate.get("title"),
            referral_link=None,
        )
        return False

    referral_link, card_html = result

    # 2. Обогащаем candidate данными из HTML карточки
    if card_html:
        candidate = _card_from_html(card_html, candidate)
        logger.info(
            "Yandex referral: цены — руб=%s, плюс=%s, старое=%s (id=%s)",
            candidate.get("price_rub"),
            candidate.get("price_with_plus"),
            candidate.get("price_old_rub"),
            product_id,
        )

    # 3. save_product — товар виден в дашборде
    try:
        await save_product({
            "url": referral_link,
            "marketplace": "yandex",
            "sku": product_id,
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
    except Exception as e:
        logger.warning("Yandex referral: save_product упал: %s", e)

    # 4. Публикуем и сохраняем ссылку на пост
    sent = await _send_to_telegram(candidate, referral_link)

    if sent is not None:
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
                source_url=product_url,
                marketplace="yandex",
                title=candidate.get("title"),
            )
            logger.info(
                "Yandex referral: ссылка на пост сохранена — %s", link,
            )
        except Exception as e:
            logger.warning("Yandex referral: ссылка не сохранена: %s", e)

    # 5. Отмечаем как опубликованное
    await mark_referral_published(
        product_id=product_id,
        title=candidate.get("title"),
        referral_link=referral_link,
    )
    logger.info(
        "Yandex referral: товар %s опубликован ✅ (реф: %s)",
        product_id, referral_link,
    )
    return True

# ============================================================
# Планировщик
# ============================================================

async def run_yandex_referral_publisher():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_ID:
        logger.warning("Yandex referral: нет TELEGRAM_BOT_TOKEN/TELEGRAM_GROUP_ID, выключаюсь")
        return

    logger.info(
        "Yandex referral publisher запущен: цикл каждые %d сек (%.1f мин), "
        "MAX_PAGES=%d, DELAY=%.1f сек, TTL=30 дней",
        PUBLISH_INTERVAL, PUBLISH_INTERVAL / 60.0,
        REFERRAL_MAX_PAGES, REFERRAL_PUBLISH_DELAY,
    )

    last_cleanup = 0.0

    while True:
        cycle_start = time.monotonic()
        logger.info("Yandex referral: ——— новый цикл ———")

        try:
            # === TTL-очистка: раз в сутки удаляем записи старше 30 дней ===
            now = time.monotonic()
            if now - last_cleanup > 86400:
                deleted = await cleanup_old_referral_publications(days=30)
                if deleted:
                    logger.info(
                        "Yandex referral: TTL-очистка — удалено %d записей "
                        "старше 30 дней (товары снова доступны для публикации)",
                        deleted,
                    )
                last_cleanup = now

            await _publish_one()
        except asyncio.CancelledError:
            logger.info("Yandex referral publisher остановлен")
            raise
        except Exception as e:
            logger.exception("Yandex referral publisher ошибка: %s", e)

        elapsed = time.monotonic() - cycle_start
        wait = max(0.0, PUBLISH_INTERVAL - elapsed)

        logger.info(
            "Yandex referral: цикл занял %.1f сек, следующий через %.1f сек",
            elapsed, wait,
        )

        await asyncio.sleep(wait)


# ============================================================
# Одна публикация
# ============================================================

async def _publish_one() -> None:
    """
    Обходит страницы рефералки циклически.

    Логика:
      • page = 1..N
      • на каждой странице публикуем только те товары,
        которых нет в published_referral_products + есть картинка
      • дошли до конца пагинации → просто выходим
        (следующий цикл начнётся снова со стр.1)
      • таблицу НЕ сбрасываем — очистка только по TTL 30 дней
        в run_yandex_referral_publisher
    """
    parser = YandexParser()
    try:
        published = await get_published_referral_ids()
        logger.info(
            "Yandex referral: в таблице опубликованных — %d товаров",
            len(published),
        )

        page = 1
        empty_streak = 0
        total_published = 0
        total_scanned = 0

        while page <= REFERRAL_MAX_PAGES:
            url = _build_referral_url(page)
            html = await asyncio.to_thread(parser._cdp_fetch_html, url)

            if not html:
                logger.warning("Yandex referral: стр.%d не дала HTML — стоп", page)
                break

            items = _parse_referral_page(html)
            if not items:
                logger.info("Yandex referral: стр.%d пустая", page)
                empty_streak += 1
                if empty_streak >= REFERRAL_EMPTY_STREAK_LIMIT:
                    logger.info(
                        "Yandex referral: %d пустых страниц подряд — "
                        "конец пагинации, стоп на стр.%d",
                        empty_streak, page,
                    )
                    break
                page += 1
                continue

            empty_streak = 0
            total_scanned += len(items)

            # === Свежие на этой странице ===
            fresh_on_page = [
                it for it in items
                if str(it.get("product_id") or "") not in published
                and it.get("image")
                and it.get("product_id")
            ]

            if not fresh_on_page:
                logger.info(
                    "Yandex referral: стр.%d → %d товаров, свежих нет — идём дальше",
                    page, len(items),
                )
                page += 1
                continue

            logger.info(
                "Yandex referral: стр.%d → %d товаров, %d свежих — публикую",
                page, len(items), len(fresh_on_page),
            )

            for candidate in fresh_on_page:
                pid = str(candidate.get("product_id") or "")
                if not pid:
                    continue

                if await product_exists(pid, "yandex"):
                    logger.info(
                        "Yandex referral: товар %s уже в products — помечаю и пропускаю",
                        pid,
                    )
                    await mark_referral_published(
                        product_id=pid,
                        title=candidate.get("title"),
                        referral_link=None,
                    )
                    published.add(pid)
                    continue

                try:
                    ok = await _publish_candidate(parser, candidate)
                    if ok:
                        published.add(pid)
                        total_published += 1
                except Exception as e:
                    logger.exception(
                        "Yandex referral: ошибка публикации %s: %s", pid, e,
                    )

                # Пауза между постами
                if REFERRAL_PUBLISH_DELAY > 0:
                    await asyncio.sleep(REFERRAL_PUBLISH_DELAY)

            page += 1

        logger.info(
            "Yandex referral: цикл завершён — просканировано %d товаров, "
            "опубликовано %d, страниц пройдено %d",
            total_scanned, total_published, page - 1,
        )

    finally:
        parser.close()


async def _publish_one_url(url: str) -> dict:
    """
    Публикует конкретный товар по переданному URL.

    Использует тот же _cdp_lock, что и планировщик, поэтому
    параллельный запуск не сломает вкладку market.yandex.ru.
    """
    parser = YandexParser()
    try:
        # Если это короткая /cc/ — разворачиваем в полный card URL
        original_url = url
        if "/cc/" in url.lower():
            logger.info("referral manual: разворачиваю /cc/ → card")
            url = await asyncio.to_thread(_resolve_yandex_cc, url)
            logger.info("referral manual: развёрнуто в %s", url[:120])

        product_id = parser.extract_sku(url) or ""
        if not product_id:
            return {
                "success": False,
                "error": f"Не удалось извлечь product_id из URL: {url[:100]}",
            }

        # Проверяем, не публиковали ли уже
        published = await get_published_referral_ids()
        if product_id in published:
            return {
                "success": False,
                "error": f"Товар {product_id} уже публиковался",
                "already_published": True,
            }

        logger.info(
            "Yandex referral manual: получаю реф. ссылку для %s", url
        )

        async with _cdp_lock:
            result = await asyncio.to_thread(
                parser._cdp_get_referral_link, url
            )

        if not result:
            return {
                "success": False,
                "error": "Не удалось получить реферальную ссылку — "
                         "возможно, товар не участвует в программе",
            }

        referral_link, card_html = result

        candidate: dict = {
            "product_id": product_id,
            "url": url,
            "title": None,
            "image": None,
            "price_rub": None,
            "price_with_plus": None,
            "price_old_rub": None,
            "rating": None,
            "rating_count": None,
            "seller": None,
        }

        if card_html:
            candidate = _card_from_html(card_html, candidate)
            logger.info(
                "Yandex referral manual: цены — руб=%s, плюс=%s, старое=%s",
                candidate.get("price_rub"),
                candidate.get("price_with_plus"),
                candidate.get("price_old_rub"),
            )

        # Сохраняем в products (чтобы было видно в дашборде)
        try:
            await save_product({
                "url": referral_link,
                "marketplace": "yandex",
                "sku": product_id,
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
                "tier_used": "yandex_referral_manual",
                "post_text": _build_post_text(candidate, referral_link),
                "success": True,
                "error": None,
            })
        except Exception as e:
            logger.warning("Yandex referral manual: save_product упал: %s", e)

        # Публикуем и сохраняем ссылку на сообщение
        sent = await _send_to_telegram(candidate, referral_link)

        if sent is not None:
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
                    source_url=original_url,
                    marketplace="yandex",
                    title=candidate.get("title"),
                )
                logger.info(
                    "Yandex referral manual: ссылка на пост сохранена — %s", link,
                )
            except Exception as e:
                logger.warning(
                    "Yandex referral manual: не удалось сохранить ссылку: %s", e,
                )

        await mark_referral_published(
            product_id=product_id,
            title=candidate.get("title"),
            referral_link=referral_link,
        )

        logger.info(
            "Yandex referral manual: товар %s опубликован — %s",
            product_id, referral_link,
        )

        return {
            "success": True,
            "product_id": product_id,
            "title": candidate.get("title"),
            "referral_link": referral_link,
        }

    except Exception as e:
        logger.exception("Yandex referral manual: ошибка для %s", url)
        return {"success": False, "error": str(e)[:300]}
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
      promo_code         ← item["promo_code"]      (например, "HOME15C")
      promo_percent      ← item["promo_percent"]   (например, 15)

    TextGenerator сам вставит строку «🎟 Промокод: `HOME15C` (–15%)»
    сразу после «✅ Стало: …».
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
        price_rub=regular,
        price_with_plus=pay if (pay and regular and pay < regular) else None,
        price_original_rub=old if (old and regular and old > regular) else None,
        url=referral_link,
        marketplace="yandex",
        sku=str(item.get("product_id") or ""),
        image_url=item.get("image"),
        success=True,
        tier_used="yandex_referral",
        promo_code=item.get("promo_code"),
        promo_percent=item.get("promo_percent"),
    )

    return TextGenerator.generate(product)


async def _send_to_telegram(item: dict, referral_link: str):
    bot = _make_bot()
    try:
        text = _build_post_text(item, referral_link)
        image_url = item.get("image")

        if image_url:
            img = await _download_image(image_url)
            if img:
                # === 1. Рендерим карточку ===
                try:
                    styled = render_product_card(
                        img,
                        marketplace="yandex",
                        title=item.get("title") or "",
                        brand=item.get("brand") or "",
                        price_rub=item.get("price_rub"),
                        price_with_plus=item.get("price_with_plus"),
                        price_original_rub=item.get("price_old_rub"),
                        rating=item.get("rating"),
                        rating_count=item.get("rating_count"),
                        promo_badge="",
                        aspect="3:4.5",
                    )
                    if styled:
                        img = styled
                        logger.info(
                            "Yandex referral: карточка отрендерена (промокод=%s)",
                            item.get("promo_code"),
                        )
                except Exception as e:
                    logger.warning("Yandex referral: рендер карточки упал: %s", e)

                # === 2. Сохраняем отрендеренную карточку ===
                try:
                    local_path = save_photo(img, image_url, source="yandex_referral")
                    if local_path:
                        await save_media(
                            source="yandex_referral",
                            image_path=local_path,
                            marketplace="yandex",
                            title=item.get("title"),
                            brand=item.get("brand") or "",
                            price_rub=item.get("price_rub"),
                            price_with_plus=item.get("price_with_plus"),
                            price_original_rub=item.get("price_old_rub"),
                            rating=item.get("rating"),
                            rating_count=item.get("rating_count"),
                            legal_text="Реклама. ООО «ЯНДЕКС МАРКЕТ», ИНН: 9704254424",
                            promo_code=item.get("promo_code"),
                            promo_percent=item.get("promo_percent"),
                            post_text=text,
                            image_url=image_url,
                        )
                except Exception as e:
                    logger.warning("Yandex referral: не удалось сохранить фото: %s", e)

                # === 3. Отправляем в Telegram ===
                try:
                    sent = await bot.send_photo(
                        chat_id=TELEGRAM_GROUP_ID,
                        photo=BufferedInputFile(img, filename="product.jpg"),
                        caption=text[:1024],
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return sent
                except Exception as e:
                    logger.warning("Yandex referral: send_photo упал: %s", e)

        sent = await bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )
        return sent
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


async def run_referral_manual_consumer():
    """Слушает parser.referral.manual и публикует переданные URL."""
    from broker.connection import broker, QUEUE_REFERRAL_MANUAL
    import json as _json

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_ID:
        logger.warning(
            "Yandex referral manual: нет TELEGRAM_BOT_TOKEN/TELEGRAM_GROUP_ID, "
            "консьюмер выключен"
        )
        return

    await broker.connect()
    channel = await broker.connection.channel()
    await channel.set_qos(prefetch_count=1)

    queue = await channel.declare_queue(QUEUE_REFERRAL_MANUAL, durable=True)

    logger.info(
        "Yandex referral manual consumer запущен, слушает %s",
        QUEUE_REFERRAL_MANUAL,
    )

    async with queue.iterator() as it:
        async for message in it:
            async with message.process():
                try:
                    update_last_message()
                    payload = _json.loads(message.body.decode())
                except Exception as e:
                    logger.error("referral manual: invalid JSON: %s", e)
                    continue

                url = payload.get("url")
                if not url:
                    logger.error("referral manual: нет url в сообщении")
                    continue

                logger.info("referral manual: получен URL %s", url)
                try:
                    result = await _publish_one_url(url)
                    if result.get("success"):
                        logger.info(
                            "referral manual: ✅ %s → %s",
                            url, result.get("referral_link"),
                        )
                    else:
                        logger.warning(
                            "referral manual: ❌ %s → %s",
                            url, result.get("error"),
                        )
                except Exception as e:
                    logger.exception("referral manual: ошибка: %s", e)