"""Consumer — читает URL из очереди, парсит, публикует результат."""
import json
import logging
import asyncio
import time
from typing import Optional
from urllib.parse import urlparse
import os

import httpx
import aio_pika

from .connection import (
    broker,
    QUEUE_INCOMING,
    QUEUE_RESULTS_STORAGE,
    QUEUE_RESULTS_TELEGRAM,
    QUEUE_ERRORS,
    QUEUE_REFERRAL_MANUAL,
    EXCHANGE_RESULTS,
)
from services.marketplace import MarketplaceFactory
from core.formatters import PriceFormatter, TextGenerator
from db import product_exists

logger = logging.getLogger(__name__)


# Домены-редиректы, которые надо распаковать
_SHORT_URL_PATTERNS = (
    "ozon.ru/t/",
    "wildberries.ru/redirect",
    "ali.click",
    "aliexpress.ru/i/",
    "market.yandex.ru/redir",
)

import os as _os

# Автоматически пускать Яндекс-ссылки в реферальный конвейер
YANDEX_AUTO_REFERRAL = _os.getenv("YANDEX_AUTO_REFERRAL", "1") == "1"


def _is_yandex_market_url(url: str) -> bool:
    """
    Яндекс-ссылка на карточку товара (в т.ч. короткая /cc/) — в реферальный
    конвейер. Поиск (/search) идёт обычным путём.
    """
    if not YANDEX_AUTO_REFERRAL:
        return False
    low = url.lower()
    if "market.yandex.ru" not in low:
        return False
    if "/search" in low:
        return False
    return True


def _cdp_available(cdp_host: str = "127.0.0.1", cdp_port: int = 9222) -> bool:
    """Проверяет, отвечает ли Chrome CDP."""
    try:
        r = httpx.get(f"http://{cdp_host}:{cdp_port}/json/version", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def _cdp_expand_short_url(
    url: str,
    cdp_host: str = "127.0.0.1",
    cdp_port: int = 9222,
    timeout: float = 15.0,
) -> str:
    """
    Распаковывает короткую ссылку через Chrome CDP.

    Открывает URL в новой вкладке (или переиспользует существующую),
    ждёт редиректа и возвращает финальный URL из document.location.href.
    """
    if not any(p in url for p in _SHORT_URL_PATTERNS):
        return url

    try:
        from websocket import create_connection
    except ImportError:
        logger.warning("CDP: websocket-client не установлен, пропускаю распаковку")
        return url

    if not _cdp_available(cdp_host, cdp_port):
        logger.warning("CDP: Chrome недоступен на %s:%s", cdp_host, cdp_port)
        return url

    ws = None
    try:
        # Ищем вкладку ozon.ru, если её нет — создаём новую
        tabs = httpx.get(f"http://{cdp_host}:{cdp_port}/json/list", timeout=5.0).json()
        tab = next(
            (t for t in tabs
             if t.get("type") == "page" and "ozon.ru" in (t.get("url") or "")),
            None,
        )

        if not tab:
            # Создаём новую вкладку с короткой ссылкой
            new_tab = httpx.put(
                f"http://{cdp_host}:{cdp_port}/json/new?{url}",
                timeout=10.0,
            )
            if new_tab.status_code != 200:
                logger.warning("CDP: не удалось создать вкладку для %s", url)
                return url
            tab = new_tab.json()
            time.sleep(3.0)  # даём странице загрузиться
        else:
            # Используем существующую вкладку, навигируем на короткую ссылку
            ws_url = tab.get("webSocketDebuggerUrl")
            ws = create_connection(ws_url, timeout=timeout)
            msg_id = [0]

            def send(method, params=None, timeout_s=10.0):
                msg_id[0] += 1
                cur = msg_id[0]
                payload = {"id": cur, "method": method}
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
                    if data.get("id") == cur:
                        return data
                return {}

            send("Page.enable")
            send("Page.navigate", {"url": url}, timeout_s=5.0)
            time.sleep(3.0)
            final_url = (send("Runtime.evaluate", {
                "expression": "document.location.href",
                "returnByValue": True,
            }).get("result") or {}).get("result", {}).get("value")
            if final_url and "ozon.ru/product" in final_url:
                logger.info("CDP: распаковал (сущ. вкладка) %s → %s", url, final_url)
                return final_url
            ws.close()
            ws = None

        # Если создали новую вкладку — читаем её URL
        if tab.get("webSocketDebuggerUrl"):
            ws_url = tab["webSocketDebuggerUrl"]
            ws = create_connection(ws_url, timeout=timeout)
            msg_id = [0]

            def send(method, params=None, timeout_s=10.0):
                msg_id[0] += 1
                cur = msg_id[0]
                payload = {"id": cur, "method": method}
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
                    if data.get("id") == cur:
                        return data
                return {}

            send("Runtime.enable")
            for _ in range(10):
                time.sleep(0.5)
                final_url = (send("Runtime.evaluate", {
                    "expression": "document.location.href",
                    "returnByValue": True,
                }).get("result") or {}).get("result", {}).get("value")
                if final_url and "ozon.ru/product" in final_url:
                    logger.info("CDP: распаковал (новая вкладка) %s → %s", url, final_url)
                    return final_url

    except Exception as e:
        logger.warning("CDP: ошибка распаковки %s: %s", url, e)
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    return url


async def process_url(url: str) -> dict:
    """
    Обработать один URL: выбрать парсер, спарсить, вернуть результат.

    Returns:
        dict с полями success, marketplace, title, price_rub, post_text, error
    """
    original_url = url
    url = url = await asyncio.to_thread(
        _cdp_expand_short_url,
        original_url,
        cdp_host=os.getenv("CDP_HOST", "127.0.0.1"),
        cdp_port=int(os.getenv("CDP_PORT", "9222")),
    )

    factory = MarketplaceFactory()

    try:
        key, parser = factory.get_parser(url)
    except ValueError as e:
        # Возможно после распаковки URL стал непонятным
        return {
            "success": False,
            "url": original_url,
            "error": f"{e} (после распаковки: {url})",
        }

    try:
        data = await asyncio.to_thread(parser.fetch_cached, url)
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

        # === Проверка: товар уже есть в БД? ===
    already_exists = False
    if data.sku:
        try:
            already_exists = await product_exists(str(data.sku), key)
        except Exception as e:
            logger.warning("Ошибка проверки существования товара: %s", e)

    if already_exists:
        logger.info(
            "Товар уже в БД (sku=%s, mp=%s) — не публикую, но обновляю цены",
            data.sku, key,
        )

    # Форматируем цены
    price_rub = PriceFormatter.format_price(str(data.price_rub)) if data.price_rub else None
    price_plus = PriceFormatter.format_price(str(data.price_with_plus)) if data.price_with_plus else None
    price_original = PriceFormatter.format_price(str(data.price_original_rub)) if data.price_original_rub else None

    if original_url and original_url != url:
        logger.info(
            "В посте использую исходную ссылку: %s (вместо %s)",
            original_url, data.url,
        )
    data.url = original_url

    post_text = TextGenerator.generate(data)

    return {
        "success": True,
        "url": original_url,
        "resolved_url": url,
        "source_url": original_url,
        "marketplace": key,
        "sku": data.sku,
        "already_exists": already_exists,
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

        # === Яндекс.Маркет → реферальный конвейер ===
        if _is_yandex_market_url(url):
            logger.info(
                "consumer: %s → реферальный конвейер (parser.referral.manual)",
                url,
            )
            try:
                await broker.publish(
                    QUEUE_REFERRAL_MANUAL,
                    {"url": url, "meta": meta},
                )
            except Exception as e:
                logger.exception("consumer: не удалось переложить в referral.manual")
                await broker.publish(QUEUE_ERRORS, {
                    "success": False,
                    "url": url,
                    "error": f"referral.routing: {e}",
                })
            return

        logger.info(f"Обрабатываю URL: {url}")

        result = await process_url(url)
        result["meta"] = meta

        # Публикуем результат
        if result["success"]:
            # Публикуем в fanout — попадут и storage, и telegram
            results_exchange = await broker.channel.declare_exchange(
                EXCHANGE_RESULTS,
                aio_pika.ExchangeType.FANOUT,
                durable=True,
            )
            import json as _json
            message = aio_pika.Message(
                body=_json.dumps(result, ensure_ascii=False).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            await results_exchange.publish(message, routing_key="")
        else:
            await broker.publish(QUEUE_ERRORS, result)


async def run_consumer():
    """Запустить consumer — бесконечный цикл обработки сообщений."""
    await broker.connect()

    # Создаём ОТДЕЛЬНЫЙ канал для этого consumer'а
    channel = await broker.connection.channel()
    await channel.set_qos(prefetch_count=1)

    queue = await channel.declare_queue(QUEUE_INCOMING, durable=True)

    logger.info(f"Consumer запущен, слушает {QUEUE_INCOMING}")

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            try:
                await handle_message(message)
            except Exception as e:
                logger.exception(f"Ошибка обработки сообщения: {e}")