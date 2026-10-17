"""
Раз в N секунд публикует свежие товары из реферальной программы Яндекс.Маркета.

Источник: https://market.yandex.ru/page/referral_products?tabId=...

ВАЖНО:
  * LDJSON ItemList отдаёт ровно 12 штук — не используем.
  * DOM productSnippet — их больше за счёт InfinityLoader (прокрутка).
  * Прокрутка — human-like (wheel, случайные шаги, паузы), чтобы антифрод
    не срабатывал.
  * Ротация по 12 табам (TOP_REWARD, CONSUMER_ELECTRONICS, ...) — даёт
    ~2800 уникальных товаров вместо ~239 на одной странице.
  * Скролл останавливается как только набрали `REFERRAL_TARGET_FRESH`
    НЕопубликованных товаров в DOM, а не когда страница кончилась.
  * Публикуем `REFERRAL_PUBLISH_LIMIT` свежих за цикл (по умолчанию 10),
    остальные — в следующий цикл.

Защита от дублей: `published_referral_products` в БД.
"""
import asyncio
import html as html_lib
import json
import logging
import os
import random
import re
import time
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote, quote

import httpx
import httpx as _httpx
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

# ============================================================
# Константы
# ============================================================

REFERRAL_TABS = [
    "TOP_REWARD",
    "CONSUMER_ELECTRONICS",
    "FMCG",
    "HOME",
    "BEAUTY",
    "DIY",
    "KIDS",
    "SPORT",
    "FASHION",
    "AUTO_AND_MACHINERY",
    "FURNITURE",
    "PHARMA",
]

# Сколько СВЕЖИХ товаров набрать в DOM перед остановкой скролла
REFERRAL_TARGET_FRESH = int(os.getenv("YANDEX_REFERRAL_TARGET_FRESH", "12"))

# Сколько товаров максимум публиковать за один цикл
REFERRAL_PUBLISH_LIMIT = int(os.getenv("YANDEX_REFERRAL_PUBLISH_LIMIT", "10"))

# Задержка между постами в Telegram
# Рекомендуется 4-6 сек: 10 постов + скролл ≈ 1 минута
REFERRAL_PUBLISH_DELAY = float(os.getenv("YANDEX_REFERRAL_PUBLISH_DELAY", "6.0"))

# Интервал между циклами
PUBLISH_INTERVAL = int(os.getenv("YANDEX_REFERRAL_INTERVAL", "60"))

# Ограничители скролла
REFERRAL_SCROLL_MAX_SECONDS = float(os.getenv("YANDEX_REFERRAL_SCROLL_MAX_SECONDS", "120.0"))
REFERRAL_SCROLL_NO_GROWTH = int(os.getenv("YANDEX_REFERRAL_SCROLL_NO_GROWTH", "10"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID") or os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")

_cdp_lock = asyncio.Lock()

# Ротация табов между циклами (персист в памяти процесса)
_current_tab_idx = 0


# ============================================================
# Извлечение товаров из DOM-сниппетов
# ============================================================

_SNIPPET_OPEN_RE = re.compile(
    r'<div[^>]*\bdata-zone-name="productSnippet"[^>]*>',
    re.S,
)


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        n = float(str(v).replace("\xa0", "").replace(" ", "").replace(",", "."))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    n = _num(v)
    return int(n) if n is not None else None


def extract_product_snippets_from_html(html: str) -> list[dict]:
    """
    Достаёт все товары из DOM-сниппетов.

    URL строится из РЕАЛЬНОГО href в DOM (/card/{slug}/{oskuId}),
    а не из /product/{oskuId}. Это критично:
      • /product/{oskuId} для многих товаров не резолвится
        и показывает «Удалённый товар»;
      • /card/{slug}/{oskuId} — канонический URL, работает всегда.
    """
    starts = list(_SNIPPET_OPEN_RE.finditer(html))
    out: list[dict] = []
    seen: set[str] = set()

    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(html)
        block = html[m.start():end]

        dm = re.search(r'\bdata-zone-data="([^"]*)"', m.group(0))
        if not dm:
            dm = re.search(r'\bdata-zone-data="([^"]*)"', block)
        if not dm:
            continue

        try:
            d = json.loads(html_lib.unescape(dm.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue

        pid = str(d.get("oskuId") or d.get("marketSku") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)

        # === URL: приоритет — реальный href из DOM ===
        url = None
        hm = re.search(r'href="(/card/[^"?]+)', block)
        if hm:
            url = "https://market.yandex.ru" + html_lib.unescape(hm.group(1))
        else:
            # Fallback: modelId → marketSku → oskuId
            for key in ("modelId", "marketSku"):
                v = d.get(key)
                if v:
                    url = f"https://market.yandex.ru/product/{v}"
                    break
            if not url:
                url = f"https://market.yandex.ru/product/{pid}"

        img = re.search(r'<img[^>]+src="([^"]+)"', block)
        image = html_lib.unescape(img.group(1)) if img else None

        plus = None
        for p in d.get("additionalPrices") or []:
            if isinstance(p, dict) and p.get("priceType") == "yaBank":
                plus = _num(p.get("priceValue"))
                break

        rating = d.get("rating") or {}

        out.append({
            "product_id": pid,
            "url": url,
            "title": d.get("title"),
            "image": image,
            "price_rub": _num(d.get("price")),
            "price_with_plus": plus,
            "price_old_rub": None,
            "rating": _num(rating.get("rating")),
            "rating_count": _to_int(rating.get("gradesCount")),
            "seller": None,
        })

    return out


# ============================================================
# Human-like скролл
# ============================================================

def _human_scroll_step(send, evaluate) -> None:
    """
    Один шаг человеческой прокрутки:
      • 60% — mouseWheel (CDP Input.dispatchMouseEvent) — как настоящее колесо;
      • 40% — программный window.scrollBy;
      • в 15% случаев шаг вверх (имитация «вернулся посмотреть»);
      • deltaY небольшой (120–380 px) — как реальное колесо мыши.
    """
    r = random.random()

    if r < 0.60:
        x = random.randint(200, 1100)
        y = random.randint(150, 800)

        if random.random() < 0.15:
            delta_y = -random.randint(60, 200)
        else:
            delta_y = random.randint(120, 380)

        try:
            send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": x,
                    "y": y,
                    "deltaX": 0,
                    "deltaY": delta_y,
                },
                timeout_s=2.0,
            )
            return
        except Exception:
            pass

    delta = random.randint(150, 400)
    if random.random() < 0.15:
        delta = -random.randint(60, 200)

    try:
        evaluate(f"window.scrollBy(0, {delta})", timeout_s=2.0)
    except Exception:
        pass


def _human_pause() -> None:
    """
    Пауза между шагами скролла — как будто человек листает глазами.
      10% — задержался на чём-то (3–6 сек)
      30% — нормальный осмотр (1–2.5 сек)
      60% — короткий шаг (0.4–1.0 сек)
    Средняя ~1.2 сек.
    """
    r = random.random()
    if r < 0.10:
        time.sleep(random.uniform(3.0, 6.0))
    elif r < 0.40:
        time.sleep(random.uniform(1.0, 2.5))
    else:
        time.sleep(random.uniform(0.4, 1.0))


def _count_fresh_in_dom(evaluate, published_ids: set[str]) -> int:
    """
    Считает количество уникальных `oskuId`, которых нет в `published_ids`,
    среди видимых productSnippet. Работает через dataset.zoneData —
    быстро (один проход по DOM, без парсинга HTML на стороне Python).
    """
    raw = evaluate(
        r"""
        (() => {
            const ids = new Set();
            for (const el of document.querySelectorAll('[data-zone-name="productSnippet"]')) {
                try {
                    const raw = el.dataset.zoneData;
                    if (!raw) continue;
                    const d = JSON.parse(raw);
                    const pid = d.oskuId || d.marketSku;
                    if (pid) ids.add(String(pid));
                } catch (e) {}
            }
            return JSON.stringify([...ids]);
        })()
        """,
        timeout_s=5.0,
    )
    if not raw:
        return 0
    try:
        ids = set(json.loads(raw))
    except Exception:
        return 0
    return len(ids - published_ids)


# ============================================================
# CDP: навигация на таб + human-like скролл до N свежих
# ============================================================

def _cdp_fetch_referral_page(
    parser: YandexParser,
    url: str,
    published_ids: Optional[set[str]] = None,
    target_fresh: int = REFERRAL_TARGET_FRESH,
    initial_timeout: float = 15.0,
    max_seconds: float = REFERRAL_SCROLL_MAX_SECONDS,
    max_no_growth_steps: int = REFERRAL_SCROLL_NO_GROWTH,
) -> Optional[str]:
    """
    Открывает таб referral_products в CDP, ждёт первые сниппеты,
    затем делает human-like прокрутку, пока не наберёт `target_fresh`
    НЕопубликованных товаров в DOM (или пока не упрётся в лимиты).

    Возвращает outerHTML.
    """
    try:
        from websocket import create_connection
    except ImportError:
        logger.error("websocket-client не установлен")
        return None

    cdp_host = getattr(parser, "cdp_host", "127.0.0.1")
    cdp_port = getattr(parser, "cdp_port", 9222)
    timeout = getattr(parser, "timeout", 40.0)
    yandex_domain = "market.yandex.ru"

    ws = None
    try:
        tabs_resp = _httpx.get(
            f"http://{cdp_host}:{cdp_port}/json/list", timeout=5.0,
        )
        if tabs_resp.status_code != 200:
            logger.error("CDP /json/list вернул HTTP %s", tabs_resp.status_code)
            return None
        tabs = tabs_resp.json()

        tab = None
        for t in tabs:
            if t.get("type") != "page":
                continue
            tu = t.get("url") or ""
            if yandex_domain not in tu:
                continue
            if any(x in tu for x in ("/showcaptcha", "/captcha", "about:blank")):
                continue
            tab = t
            break

        if not tab:
            logger.info("CDP referral: вкладки market.yandex.ru нет, создаю новую")
            new_tab = _httpx.put(
                f"http://{cdp_host}:{cdp_port}/json/new?{quote(url, safe='')}",
                timeout=10.0,
            )
            if new_tab.status_code != 200:
                return None
            tab = new_tab.json()
            time.sleep(5)

        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            return None

        ws = create_connection(ws_url, timeout=timeout)
        msg_id = [0]

        def send(method: str, params: Optional[dict] = None,
                 timeout_s: float = 30.0) -> dict:
            msg_id[0] += 1
            current_id = msg_id[0]
            payload = {"id": current_id, "method": method}
            if params:
                payload["params"] = params
            ws.send(json.dumps(payload))

            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                ws.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    raw = ws.recv()
                except Exception:
                    return {}
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("id") == current_id:
                    return data
            return {}

        def evaluate(expression: str, timeout_s: float = 30.0):
            res = send(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
                timeout_s=timeout_s,
            )
            return (res.get("result") or {}).get("result", {}).get("value")

        send("Runtime.enable")
        send("Page.enable")

        try:
            send("Network.enable", timeout_s=5.0)
            send("Network.setCacheDisabled", {"cacheDisabled": True}, timeout_s=5.0)
        except Exception:
            pass

        send("Page.navigate", {"url": url}, timeout_s=10.0)

        # 1. Ждём первые сниппеты
        logger.info("CDP referral: жду productSnippet…")
        start = time.monotonic()
        initial_count = 0
        while time.monotonic() - start < initial_timeout:
            raw = evaluate(
                "document.querySelectorAll('[data-zone-name=\"productSnippet\"]').length",
                timeout_s=3.0,
            )
            try:
                initial_count = int(raw or 0)
            except (TypeError, ValueError):
                initial_count = 0
            if initial_count >= 12:
                break
            time.sleep(0.25)

        if initial_count < 12:
            title = evaluate("document.title", timeout_s=3.0) or ""
            if "captcha" in title.lower() or "Подтвердите" in title:
                logger.warning("CDP referral: капча (title=%r)", title[:100])
                return None
            logger.warning(
                "CDP referral: первые сниппеты не догрузились за %.0fs (count=%d)",
                initial_timeout, initial_count,
            )
        else:
            logger.info("CDP referral: загружено %d productSnippet", initial_count)

        # 2. Warm-up: пара шагов + осмотр
        logger.info("CDP referral: warm-up")
        _human_scroll_step(send, evaluate)
        time.sleep(random.uniform(0.8, 2.0))
        _human_scroll_step(send, evaluate)
        time.sleep(random.uniform(0.4, 1.2))

        # 3. Основной human-like скролл
        scroll_start = time.monotonic()
        no_growth_steps = 0
        last_fresh = 0
        step = 0

        while time.monotonic() - scroll_start < max_seconds:
            step += 1
            _human_scroll_step(send, evaluate)
            _human_pause()

            # Проверяем DOM только каждые 2 шага — иначе дёргаем Runtime.evaluate
            # слишком часто и ломаем тайминг «человечности».
            if step % 2 != 0:
                continue

            fresh = 0
            if published_ids is not None:
                fresh = _count_fresh_in_dom(evaluate, published_ids)

            # Цель по свежим достигнута
            if published_ids is not None and fresh >= target_fresh:
                logger.info(
                    "CDP referral: набрали %d свежих за %.1fс (%d шагов) — стоп",
                    fresh, time.monotonic() - scroll_start, step,
                )
                break

            if fresh == last_fresh:
                no_growth_steps += 1
                if no_growth_steps >= max_no_growth_steps:
                    logger.info(
                        "CDP referral: нет прироста свежих %d проверок подряд — стоп "
                        "(fresh=%d, время=%.1fс)",
                        no_growth_steps, fresh,
                        time.monotonic() - scroll_start,
                    )
                    break
            else:
                no_growth_steps = 0

            last_fresh = fresh

        logger.info(
            "CDP referral: финиш скролла - fresh=%d, шагов=%d, время=%.1fс",
            last_fresh, step, time.monotonic() - scroll_start,
        )

        # 4. Финальная пауза «читаем» перед захватом HTML
        time.sleep(random.uniform(0.4, 1.2))

        html = evaluate("document.documentElement.outerHTML", timeout_s=15.0)
        if not html:
            logger.error("CDP referral: пустой outerHTML")
            return None

        logger.info("CDP referral: HTML size=%d", len(html))
        return html

    except Exception as e:
        logger.exception("CDP referral: ошибка: %s", e)
        return None
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass


# ============================================================
# /cc/ resolver, retpath decoder, URL builder
# ============================================================

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

    try:
        r = _httpx.get(url, timeout=timeout, follow_redirects=False, headers=headers)
        loc = _clean(r.headers.get("location", ""))
        if loc:
            logger.info("referral: /cc/ развёрнут (1 шаг) — %s", loc[:120])
            return loc
    except Exception as e:
        logger.warning("referral: httpx не развернул /cc/: %s", e)

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

    logger.error("referral: НЕ удалось развернуть /cc/: %s", url)
    return url


def _decode_retpath(retpath: str) -> Optional[str]:
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


def _build_referral_url(tab_id: str) -> str:
    return (
        "https://market.yandex.ru/page/referral_products"
        f"?tabId={tab_id}"
        "&generalContext=t%3DcprPage%3Bcpk%3Dreferral_products%3B"
        "&how=explicit_order"
    )


# ============================================================
# Публикация одного кандидата
# ============================================================

async def _publish_candidate(parser: YandexParser, candidate: dict) -> bool:
    product_id = str(candidate.get("product_id") or "")
    if not product_id:
        logger.warning("Yandex referral: кандидат без product_id — пропускаю")
        return False

    product_url = (
        candidate.get("url")
        or f"https://market.yandex.ru/product/{product_id}"
    )

    # Имитация «остановился на товаре, посмотрел, потом кликнул».
    # 1.2–2.5 сек — именно столько уходит у человека на решение.
    pre_click_pause = random.uniform(1.2, 2.5)
    logger.info(
        "Yandex referral: пауза %.1fс перед переходом в карточку %s",
        pre_click_pause, product_id,
    )
    await asyncio.sleep(pre_click_pause)

    async with _cdp_lock:
        result = await asyncio.to_thread(
            parser._cdp_get_referral_link, product_url
        )
    if not result:
        logger.warning(
            "Yandex referral: у товара %s нет реф. ссылки, помечаю и пропускаю",
            product_id,
        )
        await mark_referral_published(
            product_id=product_id,
            title=candidate.get("title"),
            referral_link=None,
        )
        return False

    referral_link, card_html = result

    if card_html:
        candidate = _card_from_html(card_html, candidate)
        logger.info(
            "Yandex referral: цены — руб=%s, плюс=%s, старое=%s (id=%s)",
            candidate.get("price_rub"),
            candidate.get("price_with_plus"),
            candidate.get("price_old_rub"),
            product_id,
        )

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
            logger.info("Yandex referral: ссылка на пост сохранена — %s", link)
        except Exception as e:
            logger.warning("Yandex referral: ссылка не сохранена: %s", e)

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
        "Yandex referral publisher запущен: цикл каждые %d сек, "
        "TARGET_FRESH=%d, PUBLISH_LIMIT=%d, DELAY=%.1f, "
        "SCROLL_MAX=%.1fs, TABS=%d, TTL=30 дней",
        PUBLISH_INTERVAL, REFERRAL_TARGET_FRESH, REFERRAL_PUBLISH_LIMIT,
        REFERRAL_PUBLISH_DELAY, REFERRAL_SCROLL_MAX_SECONDS,
        len(REFERRAL_TABS),
    )

    last_cleanup = 0.0

    while True:
        cycle_start = time.monotonic()
        logger.info("Yandex referral: ——— новый цикл ———")

        try:
            now = time.monotonic()
            if now - last_cleanup > 86400:
                deleted = await cleanup_old_referral_publications(days=30)
                if deleted:
                    logger.info(
                        "Yandex referral: TTL-очистка — удалено %d записей "
                        "старше 30 дней",
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
# Одна итерация: таб-ротация + публикация N свежих
# ============================================================

async def _publish_one() -> None:
    """
    1 цикл = 1 таб:
      • Берём следующий таб по ротации.
      • Human-like скролл до TARGET_FRESH свежих (или таймаут).
      • Публикуем первые PUBLISH_LIMIT свежих.
      • Сдвигаем ротацию на следующий таб.
    """
    global _current_tab_idx

    parser = YandexParser()
    try:
        published = await get_published_referral_ids()
        logger.info(
            "Yandex referral: в таблице опубликованных — %d товаров",
            len(published),
        )

        tab_id = REFERRAL_TABS[_current_tab_idx % len(REFERRAL_TABS)]
        url = _build_referral_url(tab_id)

        logger.info(
            "Yandex referral: таб %s (idx=%d/%d)",
            tab_id,
            _current_tab_idx % len(REFERRAL_TABS),
            len(REFERRAL_TABS),
        )

        html = await asyncio.to_thread(
            _cdp_fetch_referral_page,
            parser,
            url,
            published,
            REFERRAL_TARGET_FRESH,
        )

        if not html:
            logger.warning(
                "Yandex referral: таб %s не дал HTML — сдвигаю ротацию",
                tab_id,
            )
            _current_tab_idx += 1
            return

        items = _parse_referral_page(html)
        if not items:
            logger.info("Yandex referral: таб %s пустой", tab_id)
            _current_tab_idx += 1
            return

        logger.info(
            "Yandex referral: таб %s → %d товаров",
            tab_id, len(items),
        )

        fresh_on_tab = [
            it for it in items
            if str(it.get("product_id") or "") not in published
            and it.get("image")
            and it.get("product_id")
        ]

        if not fresh_on_tab:
            logger.info(
                "Yandex referral: таб %s → %d товаров, свежих нет — "
                "следующий таб",
                tab_id, len(items),
            )
            _current_tab_idx += 1
            return

        # Публикуем первые N свежих (порядок DOM = приоритет Яндекса)
        to_publish = fresh_on_tab[:REFERRAL_PUBLISH_LIMIT]

        logger.info(
            "Yandex referral: таб %s → %d товаров, %d свежих → публикую %d",
            tab_id, len(items), len(fresh_on_tab), len(to_publish),
        )

        # Пауза «закончил листать, окинул взглядом список, выбрал первый товар»
        look_at_list_pause = random.uniform(2.0, 4.0)
        logger.info(
            "Yandex referral: пауза %.1fс перед публикацией первой карточки",
            look_at_list_pause,
        )
        await asyncio.sleep(look_at_list_pause)

        total_published = 0
        publish_start = time.monotonic()

        for candidate in to_publish:
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

            if REFERRAL_PUBLISH_DELAY > 0:
                await asyncio.sleep(REFERRAL_PUBLISH_DELAY)

        elapsed = time.monotonic() - publish_start
        logger.info(
            "Yandex referral: таб %s — опубликовано %d из %d за %.1fс "
            "(%.2f сек/товар)",
            tab_id, total_published, len(to_publish), elapsed,
            elapsed / max(1, total_published),
        )

        _current_tab_idx += 1
        logger.info(
            "Yandex referral: следующий таб — %s",
            REFERRAL_TABS[_current_tab_idx % len(REFERRAL_TABS)],
        )

    finally:
        parser.close()


# ============================================================
# Ручная публикация одного URL
# ============================================================

async def _publish_one_url(url: str) -> dict:
    parser = YandexParser()
    try:
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

        published = await get_published_referral_ids()
        if product_id in published:
            return {
                "success": False,
                "error": f"Товар {product_id} уже публиковался",
                "already_published": True,
            }

        logger.info("Yandex referral manual: получаю реф. ссылку для %s", url)

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
# Парсинг HTML
# ============================================================

def _parse_referral_page(html: str) -> list[dict]:
    items = extract_product_snippets_from_html(html)
    if items:
        logger.info("Yandex referral: DOM snippets дали %d товаров", len(items))
        return items

    try:
        parsed = parse_search(html)
        if parsed.get("status") not in (ParseStatus.CAPTCHA, ParseStatus.EMPTY):
            items = parsed.get("items") or []
            if items:
                logger.info("Yandex referral: parse_search дал %d товаров", len(items))
                return items
    except Exception as e:
        logger.warning("Yandex referral: parse_search упал: %s", e)

    seen: set[str] = set()
    items: list[dict] = []
    for m in re.finditer(
            r'/(?:product(?:--[^/"]+)?|card/[^/"]+)/(\d{6,})', html,
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
# Telegram
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
    pay = item.get("price_with_plus")
    regular = item.get("price_rub")
    old = item.get("price_old_rub")

    primary = pay if pay else regular

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
                except Exception as e:
                    logger.warning("Yandex referral: рендер карточки упал: %s", e)

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
    from broker.connection import broker, QUEUE_REFERRAL_MANUAL

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
                    payload = json.loads(message.body.decode())
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