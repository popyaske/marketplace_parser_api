"""
Публикация товаров из каталога Такпродам в Telegram-группу каждые
TAKPRODAM_PUBLISH_INTERVAL секунд (по умолчанию 120 = 2 минуты).

Источники данных:
  • ЦЕНЫ (price_rub, price_with_plus) — всегда из Такпродама.
    Там актуальные цены от продавца.
  • ЗАЧЁРКНУТАЯ (price_original_rub) + рейтинг + бренд — из CDP
    (карточка маркетплейса, по external_link; SKU — из поля item["sku"] API).
    Такпродам их не отдаёт, и без них карточка рендерится без скидки.

Логика:
  • раз в N секунд запрашиваем каталог через API takprodam.ru;
  • фильтруем уже опубликованные;
  • выбираем 1 случайный новый товар;
  • обогащаем: price_original / rating / rating_count / brand — из CDP;
  • рендерим карточку через render_product_card;
  • если рендер упал — пост НЕ публикуем (только отрендеренные фото);
  • публикуем в Telegram-группу (фото + HTML-caption);
  • юр.текст — <blockquote> в том же сообщении (HTML-режим);
  • отмечаем факт публикации в БД.

Дубликаты исключены на уровне БД: UNIQUE(published_products.takprodam_id).
"""
import asyncio
import html as html_lib
import logging
import os
import random
import time
import json
from typing import Optional
from urllib.parse import urlparse

import httpx
from aiohttp import BasicAuth
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile, Message
from sqlalchemy import select, desc

from core.formatters import TextGenerator
from core.models import ProductData
from db import (
    get_published_ids, mark_published, save_media, save_product,
    get_session_maker,
)
from services.marketplace import MarketplaceFactory
from services.takprodam import TakProDamClient
from services.media_storage import save_photo
from services.overlay_maker import render_product_card

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID") or os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")

# Аспект карточки (тот же, что у telegram_consumer)
CARD_ASPECT = os.getenv("TELEGRAM_CARD_ASPECT", "3:4.5")

TAKPRODAM_API_TOKEN = os.getenv("TAKPRODAM_API_TOKEN", "")
TAKPRODAM_SOURCE_ID = int(os.getenv("TAKPRODAM_SOURCE_ID", "0") or 0)
TAKPRODAM_MARKETPLACE = os.getenv("TAKPRODAM_MARKETPLACE", "")
TAKPRODAM_SUBID = os.getenv("TAKPRODAM_SUBID", "tg_daily")

# Интервал публикации (сек). 120 = каждые 2 минуты.
TAKPRODAM_PUBLISH_INTERVAL = int(os.getenv("TAKPRODAM_PUBLISH_INTERVAL", "120"))

# Если новых товаров нет — пауза подольше (сек).
TAKPRODAM_EMPTY_INTERVAL = int(os.getenv("TAKPRODAM_EMPTY_INTERVAL", "600"))

# Сколько страниц каталога смотреть при поиске свежего (limit=100 на страницу).
TAKPRODAM_MAX_PAGES = int(os.getenv("TAKPRODAM_MAX_PAGES", "20"))

# Маркетплейсы, для которых render_product_card умеет рисовать брендинг.
SUPPORTED_MARKETPLACES = {"ozon", "wildberries", "yandex", "aliexpress"}


# ============================================================
# Telegram-лимиты
# ============================================================

TELEGRAM_CAPTION_MAX = 1024
TELEGRAM_MESSAGE_MAX = 4096
TITLE_MAX = 200


def _truncate_title(title: Optional[str], max_len: int = TITLE_MAX) -> str:
    if not title:
        return "Не найдено"
    title = title.strip()
    if len(title) <= max_len:
        return title
    cut = title[:max_len].rstrip()
    space = cut.rfind(" ")
    if space > max_len - 30:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def _safe_caption(text: str, max_len: int = TELEGRAM_CAPTION_MAX) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len]

    block_break = cut.rfind("\n\n")
    if block_break > max_len - 400:
        return cut[:block_break].rstrip()

    space = cut.rfind(" ")
    if space > max_len - 100:
        return cut[:space].rstrip()

    return cut


# ============================================================
# Утилиты
# ============================================================

def _normalize_marketplace(title: Optional[str]) -> str:
    if not title:
        return "unknown"
    return title.strip().lower().replace(" ", "")


def _to_float(v) -> Optional[float]:
    """
    Парсит число из API/парсера. Устойчиво к '1 999 ₽' и '1999,00'.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            n = float(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None
    try:
        s = str(v).replace("\xa0", "").replace(" ", "").replace(",", ".").replace("₽", "")
        n = float(s)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _extract_legal_text(item: dict) -> str:
    """Юр.текст с erid из ответа API Такпродам."""
    ready = (item.get("legal_text") or "").strip()
    if ready:
        return ready

    erid = (item.get("erid") or "").strip()
    entity = (item.get("legal_entity") or item.get("advertiser") or "").strip()
    inn = (item.get("inn") or "").strip()

    if entity and inn and erid:
        return f'Реклама. "{entity}" ИНН {inn} erid {erid}'
    if entity and erid:
        return f'Реклама. "{entity}" erid {erid}'
    if erid:
        return f"Реклама. erid {erid}"

    logger.warning(
        "Daily publisher: у товара takprodam_id=%s нет ни legal_text, ни erid — "
        "пост уйдёт без юр.текста",
        item.get("id"),
    )
    return ""


# ============================================================
# Обогащение из CDP
# ============================================================
# ВАЖНО: цена (price_rub) и plus-цена (price_with_plus) ОСТАЮТСЯ из API
# Такпродама. CDP даёт только то, чего там нет:
#   • price_original_rub — зачёркнутая цена (для рендера скидки);
#   • rating / rating_count — звёзды и число отзывов;
#   • brand — для брендинга на карточке.
# Все "чужие" поля из парсера (его price_rub, price_with_plus) ИГНОРИРУЮТСЯ.


async def _lookup_product_in_db(sku: str, marketplace: str) -> Optional[dict]:
    """Если товар уже парсился main-pipeline — достаём из products."""
    try:
        from db import Product
        session_maker = get_session_maker()
        async with session_maker() as session:
            row = await session.execute(
                select(Product)
                .where(Product.sku == sku)
                .where(Product.marketplace == marketplace)
                .order_by(desc(Product.created_at))
                .limit(1)
            )
            p = row.scalar_one_or_none()
            if p:
                return {
                    "price_original_rub": p.price_original_rub,
                    "rating": p.rating,
                    "rating_count": p.rating_count,
                    "brand": p.brand,
                }
    except Exception as e:
        logger.warning("Daily publisher: DB lookup упал: %s", e)
    return None


async def _enrich_with_marketplace_data(item: dict) -> dict:
    """
    Обогащает item недостающими полями из CDP-парсера маркетплейса.

    Логика:
      1. Смотрим, какие поля отсутствуют в item от Такпродама.
      2. Если ничего не отсутствует — CDP не нужен, выходим сразу.
      3. Быстро проверяем БД (без сети).
      4. Для оставшихся недостающих полей идём в CDP-парсер.
      5. Заполняем только те поля, которых ещё нет; существующие не трогаем.
      6. Если CDP упал (parser_drift и т.п.) — не блокируем публикацию:
         пост уйдёт с тем, что отдал Такпродам, без обогащённых полей.

    Цены из API (item["price"]) НЕ ТРОГАЕМ.
    """
    url = item.get("external_link") or item.get("tracking_link")
    if not url:
        logger.info(
            "Daily publisher: нет external_link/tracking_link у takprodam_id=%s — "
            "обогащение пропущено",
            item.get("id"),
        )
        return item

    # Какие поля пытаемся получить из CDP.
    # title/price/image НЕ включаем: они всегда есть в ответе Такпродама
    # (см. _takprodam_to_product — берёт item["title"], item["price"] и т.д.).
    # CDP нужен только для полей, которых у Такпродама принципиально нет.
    WANTED_FIELDS = ["rating", "rating_count", "price_original", "brand"]
    missing = [f for f in WANTED_FIELDS if not item.get(f)]

    if not missing:
        logger.info(
            "Daily publisher: все поля уже есть — CDP не нужен (takprodam_id=%s)",
            item.get("id"),
        )
        return item

    logger.info(
        "Daily publisher: недостающие поля %s — пробую CDP (takprodam_id=%s)",
        missing, item.get("id"),
    )

    try:
        factory = MarketplaceFactory()
        _, parser = factory.get_parser(url)
    except Exception as e:
        logger.warning("Daily publisher: MarketplaceFactory упал: %s", e)
        return item

    if parser is None:
        logger.info(
            "Daily publisher: парсер для %r не найден — обогащение пропущено",
            item.get("marketplace_title"),
        )
        return item

    # SKU — сначала из ответа Такпродама, потом из URL парсером
    sku = item.get("sku")
    if not sku:
        try:
            sku = parser.extract_sku(url)
        except Exception as e:
            logger.warning("Daily publisher: extract_sku failed: %s", e)
            sku = None

    mp = getattr(parser, "marketplace", "unknown")

    # ── Шаг 1. БД (быстро, без сети) ──────────────────────────────
    if sku:
        cached = await _lookup_product_in_db(sku, mp)
        if cached:
            filled_from_db = []
            if "rating" in missing and cached.get("rating") is not None:
                item["rating"] = cached["rating"]; filled_from_db.append("rating")
            if "rating_count" in missing and cached.get("rating_count") is not None:
                item["rating_count"] = cached["rating_count"]; filled_from_db.append("rating_count")
            if "price_original" in missing and cached.get("price_original_rub") is not None:
                item["price_original"] = cached["price_original_rub"]; filled_from_db.append("price_original")
            if "brand" in missing and cached.get("brand"):
                item["brand"] = cached["brand"]; filled_from_db.append("brand")

            if filled_from_db:
                item["marketplace_parsed"] = mp
                item["parsed_sku"] = sku
                missing = [f for f in missing if not item.get(f)]
                logger.info(
                    "Daily publisher: из БД (%s sku=%s) заполнено %s, остаток: %s",
                    mp, sku, filled_from_db, missing,
                )
                if not missing:
                    return item

    # ── Шаг 2. CDP-парсер (только для оставшихся) ────────────────
    try:
        data = await asyncio.to_thread(parser.fetch_cached, url)
    except Exception as e:
        logger.warning(
            "Daily publisher: парсер %s упал для %s: %s",
            mp, url[:80], e,
        )
        return item

    if not data.success:
        error_lower = (data.error or "").lower()
        is_parser_drift = "parser_drift" in error_lower

        # ozon.py при parser_drift выбрасывает частичный результат _parse_widgets
        # и возвращает пустой ProductData. Делаем salvage: свой CDP-fetch +
        # _parse_widgets из services.ozon — забираем rating/rating_count и
        # price_original, не трогая title (он уже есть из Такпродама).
        if is_parser_drift and mp == "ozon" and sku:
            logger.info(
                "Daily publisher: parser_drift для sku=%s — salvage через CDP",
                sku,
            )

            # 1) Зачёркнутая цена — читаем из DOM (без title-check)
            if "price_original" in missing and hasattr(parser, "_cdp_read_dom_prices"):
                try:
                    dom = await asyncio.to_thread(parser._cdp_read_dom_prices, sku)
                    if dom and dom.get("price_original"):
                        item["price_original"] = dom["price_original"]
                        missing = [f for f in missing if not item.get(f)]
                        logger.info(
                            "Daily publisher: salvage → price_original=%s (DOM)",
                            dom["price_original"],
                        )
                except Exception as e:
                    logger.warning(
                        "Daily publisher: salvage DOM prices failed: %s", e,
                    )

            # 2) Рейтинг / отзывы — повторный CDP-fetch + свой парсинг
            if any(f in missing for f in ("rating", "rating_count")):
                try:
                    from services.ozon import _parse_widgets as _ozon_parse_widgets
                except ImportError:
                    _ozon_parse_widgets = None

                if _ozon_parse_widgets is not None and hasattr(parser, "_cdp_fetch"):
                    api_url = (
                        "https://www.ozon.ru/api/composer-api.bx/page/json/v2?"
                        f"url=/product/{sku}/"
                    )
                    try:
                        fetch_result = await asyncio.to_thread(
                            parser._cdp_fetch, api_url,
                        )
                    except Exception as e:
                        logger.warning(
                            "Daily publisher: salvage CDP fetch failed: %s", e,
                        )
                        fetch_result = None

                    if fetch_result:
                        status, body = fetch_result
                        if status == 200 and body:
                            try:
                                payload = json.loads(body)
                                partial = _ozon_parse_widgets(payload)

                                if "rating" in missing and partial.get("rating_score") is not None:
                                    item["rating"] = partial["rating_score"]
                                    missing = [f for f in missing if not item.get(f)]
                                    logger.info(
                                        "Daily publisher: salvage → rating=%s",
                                        partial["rating_score"],
                                    )

                                if "rating_count" in missing and partial.get("rating_count") is not None:
                                    item["rating_count"] = partial["rating_count"]
                                    missing = [f for f in missing if not item.get(f)]
                                    logger.info(
                                        "Daily publisher: salvage → rating_count=%s",
                                        partial["rating_count"],
                                    )
                            except (json.JSONDecodeError, TypeError) as e:
                                logger.warning(
                                    "Daily publisher: salvage JSON parse failed: %s", e,
                                )

            if "brand" in missing and hasattr(parser, "_cdp_read_dom_prices"):
                # brand через _parse_widgets не извлекается — оставляем пустым,
                # если хотите добавить, нужно расширять _parse_widgets в ozon.py.
                pass

        logger.warning(
            "Daily publisher: парсер %s success=False (%s) — "
            "недостающие поля после salvage: %s (url=%s)",
            mp, data.error, missing, url[:80],
        )
        return item

    # ── Шаг 3. Заполняем только недостающие, существующие не трогаем ─
    filled_from_cdp = []
    if "rating" in missing and data.rating is not None:
        item["rating"] = data.rating; filled_from_cdp.append("rating")
    if "rating_count" in missing and data.rating_count is not None:
        item["rating_count"] = data.rating_count; filled_from_cdp.append("rating_count")
    if "price_original" in missing and data.price_original_rub is not None:
        item["price_original"] = data.price_original_rub; filled_from_cdp.append("price_original")
    if "brand" in missing and data.brand:
        item["brand"] = data.brand; filled_from_cdp.append("brand")

    if filled_from_cdp:
        item["marketplace_parsed"] = mp
        item["parsed_sku"] = data.sku or sku
        logger.info(
            "Daily publisher: из CDP %s (sku=%s) заполнено %s, остаток: %s",
            mp, data.sku or sku, filled_from_cdp,
            [f for f in missing if not item.get(f)],
        )
    else:
        logger.info(
            "Daily publisher: CDP %s не дал новых полей для %s (success=True, "
            "но пустые значения)",
            mp, missing,
        )

    return item


async def _save_to_products(item: dict) -> None:
    """
    Пишем товар в таблицу `products` — из неё читает дашборд
    (/api/v1/results).

    Без этого вызова товар, опубликованный daily_publisher, виден в Telegram
    и в published_products (дедуп), но НЕ виден во фронте.

    Используем `parsed_sku` из `_enrich_with_marketplace_data`, чтобы
    UNIQUE(sku, marketplace) совпадал с обычным парсингом. Если парсер
    SKU не дал — fallback на takprodam `product_id`.
    """
    product = _takprodam_to_product(item)

    # SKU: приоритет парсеру — иначе дубли с обычным парсингом
    sku = (
        item.get("parsed_sku")
        or str(item.get("product_id") or item.get("product_sku") or "")
    )

    # post_text — для превью в дашборде. Используем тот же TextGenerator,
    # что и остальные консьюмеры (Markdown), а не HTML — дашборд
    # показывает его как plain text в <pre>.
    try:
        post_text = TextGenerator.generate(product)
    except Exception:
        post_text = ""

    try:
        product_id = await save_product({
            "url": item.get("tracking_link") or item.get("external_link") or "",
            "marketplace": product.marketplace,
            "sku": sku,
            "title": item.get("title"),
            "price_rub": product.price_rub,
            "price_with_plus": product.price_with_plus,
            "price_original_rub": product.price_original_rub,
            "seller": item.get("store_title") or "",
            "brand": item.get("brand") or "",
            "rating": item.get("rating"),
            "rating_count": item.get("rating_count"),
            "in_stock": True,
            "image_url": item.get("image_url"),
            "tier_used": "takprodam",
            "post_text": post_text,
            "success": True,
            "error": None,
        })
        logger.info(
            "Daily publisher: сохранён в products (id=%s, sku=%s, mp=%s)",
            product_id, sku, product.marketplace,
        )
    except Exception as e:
        logger.exception(
            "Daily publisher: save_product упал (takprodam_id=%s): %s",
            item.get("id"), e,
        )


# ============================================================
# Такпродам → ProductData
# ============================================================

def _takprodam_to_product(item: dict) -> ProductData:
    """
    Цена (price_rub) и plus (price_with_plus) — всегда из API Такпродама.
    Зачёркнутая (price_original) — из CDP, если она больше текущей.

    Если plus >= rub — это не скидка, а «плюсовая» цена: меняем местами,
    чтобы рендер нарисовал правильную иерархию (обычная больше plus).
    """
    marketplace = _normalize_marketplace(item.get("marketplace_title"))
    url = item.get("tracking_link") or item.get("external_link") or ""

    price_rub = _to_float(item.get("price"))
    price_plus = _to_float(item.get("price_discount"))

    # Плюсовая цена: если она >= обычной — меняем местами
    if price_rub and price_plus and price_plus >= price_rub:
        price_rub, price_plus = price_plus, price_rub
    elif price_rub and not price_plus:
        # У Такпродама иногда price_discount — это и есть акционная,
        # а price — «было». Если так — API уже отдал правильные поля.
        pass

    # Зачёркнутая: берём из CDP, но только если она больше текущей.
    # Иначе это мусор (устаревшие данные, дубль regular-цены и т.п.).
    price_original = item.get("price_original")
    if price_original is not None:
        try:
            po = float(price_original)
            base = price_plus if price_plus else price_rub
            if base is None or po <= base:
                # Не больше главной цены — не скидка, отбрасываем
                if po:
                    logger.info(
                        "Daily publisher: orig=%s <= main=%s — отбрасываю "
                        "зачёркнутую (takprodam_id=%s)",
                        po, base, item.get("id"),
                    )
                price_original = None
            else:
                price_original = po
        except (TypeError, ValueError):
            price_original = None

    return ProductData(
        title=_truncate_title(item.get("title")),
        price_rub=price_rub,
        price_with_plus=price_plus,
        price_original_rub=price_original,
        url=url,
        marketplace=marketplace,
        sku=str(item.get("sku") or item.get("product_id") or ""),
        seller=item.get("store_title") or "",
        brand=item.get("brand") or "",
        rating=item.get("rating"),
        rating_count=item.get("rating_count"),
        image_url=item.get("image_url"),
        success=True,
        tier_used="takprodam",
    )


# ============================================================
# HTML-пост
# ============================================================

def _fmt_price(price: Optional[float]) -> Optional[str]:
    if price is None:
        return None
    try:
        return f"{int(price):,}".replace(",", " ") + "₽"
    except (TypeError, ValueError):
        return None


def _hashtag(marketplace: str) -> str:
    return TextGenerator.HASHTAGS.get((marketplace or "").lower(), "")


def _build_post_html(item: dict) -> str:
    """HTML-пост. Логика цены — 1:1 с TextGenerator.generate()."""
    p = _takprodam_to_product(item)

    price_rub = _fmt_price(p.price_rub)
    price_plus = _fmt_price(p.price_with_plus)
    price_orig = _fmt_price(p.price_original_rub)

    if price_orig and price_rub and price_plus:
        old_line = f"❌ Было: {price_orig}\n"
        final = price_plus
    elif price_orig and price_rub:
        old_line = f"❌ Было: {price_orig}\n"
        final = price_rub
    elif price_rub and price_plus:
        old_line = f"❌ Было: {price_rub}\n"
        final = price_plus
    elif price_rub:
        old_line = ""
        final = price_rub
    elif price_plus:
        old_line = ""
        final = price_plus
    else:
        old_line = ""
        final = "Не найдено"

    title = html_lib.escape(p.title or "Не найдено")
    url = html_lib.escape(p.url or "", quote=True)
    tag = _hashtag(p.marketplace)

    parts = [f"🚨 {title}", ""]
    if old_line:
        parts.append(old_line.rstrip())
    parts += [
        f"✅ Стало: {final}",
        "",
        "📌 Итоговая цена зависит от аккаунта",
        "",
        f'🛒 <a href="{url}">Забрать товар</a>',
        "",
        '💬 <a href="https://t.me/PromoMP1_chat">Чат для обсуждений</a>',
    ]
    if tag:
        parts += ["", tag]

    legal = _extract_legal_text(item)
    if legal:
        parts += ["", f"<blockquote>{html_lib.escape(legal)}</blockquote>"]

    return "\n".join(parts)


# ============================================================
# Планировщик
# ============================================================

async def run_daily_publisher():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_ID:
        logger.warning("Daily publisher: нет TELEGRAM_BOT_TOKEN/TELEGRAM_GROUP_ID, выключаюсь")
        return
    if not TAKPRODAM_API_TOKEN or not TAKPRODAM_SOURCE_ID:
        logger.warning("Daily publisher: нет TAKPRODAM_API_TOKEN/TAKPRODAM_SOURCE_ID, выключаюсь")
        return

    logger.info(
        "Daily publisher запущен: публикация каждые %d сек (%.1f мин), "
        "при пустоте — %d сек, source_id=%d, MAX_PAGES=%d",
        TAKPRODAM_PUBLISH_INTERVAL, TAKPRODAM_PUBLISH_INTERVAL / 60.0,
        TAKPRODAM_EMPTY_INTERVAL, TAKPRODAM_SOURCE_ID, TAKPRODAM_MAX_PAGES,
    )

    while True:
        cycle_start = time.monotonic()
        published_ok = False

        try:
            published_ok = await _publish_one()
        except asyncio.CancelledError:
            logger.info("Daily publisher остановлен")
            raise
        except Exception as e:
            logger.exception("Daily publisher ошибка: %s", e)

        interval = TAKPRODAM_PUBLISH_INTERVAL if published_ok else TAKPRODAM_EMPTY_INTERVAL
        elapsed = time.monotonic() - cycle_start
        wait = max(0.0, interval - elapsed)

        logger.info(
            "Daily publisher: цикл занял %.1f сек (%s), следующий через %.1f сек",
            elapsed,
            "опубликовано" if published_ok else "пусто",
            wait,
        )

        await asyncio.sleep(wait)


# ============================================================
# Одна публикация
# ============================================================

async def _publish_one() -> bool:
    """
    Найти 1 новый товар, отрендерить карточку, опубликовать.
    Возвращает True, если публикация состоялась.
    """
    published_ids = await get_published_ids()
    logger.info("Daily publisher: уже опубликовано %d товаров", len(published_ids))

    client = TakProDamClient(TAKPRODAM_API_TOKEN)
    candidate: Optional[dict] = None
    try:
        page = 1
        while page <= TAKPRODAM_MAX_PAGES and candidate is None:
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

            # Отсеиваем: опубликованные + неподдерживаемые маркетплейсы
            fresh = [
                it for it in items
                if str(it.get("id")) not in published_ids
                and _normalize_marketplace(it.get("marketplace_title")) in SUPPORTED_MARKETPLACES
            ]
            if fresh:
                candidate = random.choice(fresh)
                break
            page += 1

        if candidate is None:
            logger.warning(
                "Daily publisher: новых товаров нет (всё опубликовано или "
                "маркетплейс не поддерживается, просмотрено страниц: %d)",
                page - 1,
            )
            return False

        # === Обогащение (зачёркнутая + рейтинг + бренд из CDP) ===
        candidate = await _enrich_with_marketplace_data(candidate)

        sent = await _send_to_telegram(candidate)

        if not sent:
            logger.warning(
                "Daily publisher: публикация не удалась (takprodam_id=%s), "
                "НЕ помечаю — попробуем в следующий раз",
                candidate.get("id"),
            )
            return False

        await _save_to_products(candidate)

        await mark_published(candidate)
        logger.info(
            "Daily publisher: опубликован товар takprodam_id=%s, title=%r",
            candidate.get("id"), (candidate.get("title") or "")[:80],
        )
        return True
    finally:
        client.close()


# ============================================================
# Telegram
# ============================================================

def _make_bot() -> Bot:
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
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.warning("Daily publisher: не удалось скачать картинку %s: %s", image_url, e)
        return None


def _image_filename(image_url: str) -> str:
    url_low = image_url.lower()
    if ".png" in url_low:
        return "product.png"
    if ".webp" in url_low:
        return "product.webp"
    return "product.jpg"


async def _send_to_telegram(item: dict) -> Optional[Message]:
    """
    Скачивает картинку, рендерит карточку, публикует.

    Если рендер упал или картинка не скачалась — возвращает None
    (пост НЕ публикуется, товар НЕ помечается — попробуем в след. раз).
    """
    product = _takprodam_to_product(item)
    marketplace = product.marketplace
    legal = _extract_legal_text(item)

    image_url = item.get("image_url")
    if not image_url:
        logger.warning(
            "Daily publisher: нет image_url у takprodam_id=%s — пропускаю",
            item.get("id"),
        )
        return None

    img_bytes = await _download_image(image_url)
    if not img_bytes:
        logger.warning(
            "Daily publisher: картинка не скачалась у takprodam_id=%s — пропускаю",
            item.get("id"),
        )
        return None

    try:
        styled = render_product_card(
            img_bytes,
            marketplace=marketplace,
            title=item.get("title") or "",
            brand=item.get("brand") or item.get("store_title") or "",
            price_rub=product.price_rub,
            price_with_plus=product.price_with_plus,
            price_original_rub=product.price_original_rub,
            rating=item.get("rating"),
            rating_count=item.get("rating_count"),
            legal_text=legal,
            promo_badge="",
            aspect=CARD_ASPECT,
        )
    except Exception as e:
        logger.exception(
            "Daily publisher: render_product_card упал для takprodam_id=%s: %s",
            item.get("id"), e,
        )
        styled = None

    if not styled:
        logger.warning(
            "Daily publisher: render_product_card вернул None для takprodam_id=%s\n"
            "  marketplace   = %r\n"
            "  price_rub     = %r\n"
            "  price_plus    = %r\n"
            "  price_orig    = %r\n"
            "  rating        = %r (%r)\n"
            "  title_len     = %d\n"
            "  image_bytes   = %d\n"
            "  image_head    = %r\n"
            "  aspect        = %r",
            item.get("id"),
            marketplace,
            product.price_rub, product.price_with_plus, product.price_original_rub,
            item.get("rating"), item.get("rating_count"),
            len(item.get("title") or ""),
            len(img_bytes),
            img_bytes[:16],
            CARD_ASPECT,
        )
        return None

    # Сохраняем отрендеренную карточку для VK-клипа
    try:
        local_path = save_photo(styled, image_url, source="daily_publisher")
        if local_path:
            await save_media(
                source="daily_publisher",
                image_path=local_path,
                marketplace=marketplace,
                title=item.get("title"),
                brand=item.get("brand") or item.get("store_title") or "",
                price_rub=product.price_rub,
                price_with_plus=product.price_with_plus,
                price_original_rub=product.price_original_rub,
                rating=item.get("rating"),
                rating_count=item.get("rating_count"),
                legal_text=legal,
                post_text=_build_post_html(item),
                image_url=image_url,
            )
    except Exception as e:
        logger.warning("Daily publisher: не удалось сохранить карточку: %s", e)

    post_html = _build_post_html(item)
    caption = _safe_caption(post_html, TELEGRAM_CAPTION_MAX)

    if len(post_html) > TELEGRAM_CAPTION_MAX:
        logger.info(
            "Daily publisher: caption длиннее лимита (%d > %d), обрезан",
            len(post_html), TELEGRAM_CAPTION_MAX,
        )

    bot = _make_bot()
    try:
        try:
            sent = await bot.send_photo(
                chat_id=TELEGRAM_GROUP_ID,
                photo=BufferedInputFile(
                    styled,
                    filename=_image_filename(image_url),
                ),
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            logger.info(
                "Daily publisher: пост отправлен (message_id=%d, caption=%d симв., "
                "orig=%s, rating=%s)",
                sent.message_id, len(caption),
                product.price_original_rub, item.get("rating"),
            )
            return sent
        except Exception as e:
            logger.warning(
                "Daily publisher: send_photo упал (%s) — fallback на текст", e,
            )

        try:
            sent = await bot.send_message(
                chat_id=TELEGRAM_GROUP_ID,
                text=_safe_caption(post_html, TELEGRAM_MESSAGE_MAX),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
            logger.info(
                "Daily publisher: пост без фото отправлен (message_id=%d)",
                sent.message_id,
            )
            return sent
        except Exception as e:
            logger.exception("Daily publisher: send_message упал: %s", e)
            return None
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass