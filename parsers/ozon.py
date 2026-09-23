"""
Парсер Ozon через composer-api.bx.

Двухуровневая стратегия:
  Tier 1: httpx с реалистичными заголовками (работает с резидентного IP)
  Tier 2: Chrome CDP — fetch изнутри залогиненной сессии ozon.ru

CDP требует запущенного Chrome с --remote-debugging-port=9222.
Запустить: start-chrome-cdp.bat или вручную.
"""

import json
import logging
import re
import time
import urllib.parse
from typing import Any, Optional, Dict

import httpx

from core.models import ProductData
from .base import BaseParser

logger = logging.getLogger("ozon")

OZON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    "Referer": "https://www.ozon.ru/",
    "x-o3-app-name": "dweb_client",
    "x-o3-app-version": "release_2-7-1",
    "x-o3-page-type": "detail",
}


def _coerce_price(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    if isinstance(v, str):
        digits = re.sub(r"[^\d]", "", v)
        if not digits:
            return None
        try:
            n = float(digits)
            return n if n > 0 else None
        except ValueError:
            return None
    return None


def _parse_widgets(payload: dict) -> Dict[str, Any]:
    """Разобрать widgetStates в плоский словарь."""
    widgets = payload.get("widgetStates") or {}
    out: Dict[str, Any] = {}

    for key, val in widgets.items():
        try:
            data = json.loads(val) if isinstance(val, str) else val
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue

        if "webPrice" in key:
            if "price" not in out and (data.get("price") or data.get("cardPrice")):
                out["price"] = _coerce_price(data.get("price"))
                out["card_price"] = _coerce_price(data.get("cardPrice"))
                out["price_original"] = _coerce_price(data.get("originalPrice"))
                out["is_available"] = data.get("isAvailable")

        elif "webSale" in key:
            cti = data.get("cellTrackingInfo")
            tracking = cti.get("product") if isinstance(cti, dict) else None
            if not isinstance(tracking, dict):
                tracking = {}
            if tracking and "price" not in out:
                out["price"] = _coerce_price(tracking.get("finalPrice") or tracking.get("price"))
                out["price_original"] = _coerce_price(tracking.get("price"))
                offer = data.get("offer")
                out["is_available"] = offer.get("isAvailable") if isinstance(offer, dict) else None

        elif "webReviewProductScore" in key:
            out["rating_score"] = data.get("totalScore")
            out["rating_count"] = data.get("reviewsCount")

        elif "webStickyProducts" in key or "webProductHeading" in key:
            if "title" not in out and data.get("title"):
                out["title"] = data["title"]
            seller = data.get("seller") or {}
            if isinstance(seller, dict) and seller and "seller" not in out:
                out["seller"] = seller.get("name")
            # Fallback: картинка из sticky (уменьшенная, с /c50/ в URL)
            if "image_url" not in out:
                cover = data.get("coverImage")
                if isinstance(cover, str) and cover.startswith("http"):
                    out["image_url"] = cover

        # === webGallery — основной источник картинки ===
        elif "webGallery" in key:
            # Приоритет 1: coverImage (полноразмерная)
            cover = data.get("coverImage")
            if isinstance(cover, str) and cover.startswith("http"):
                out["image_url"] = cover
                out["image_url_thumb"] = cover  # fallback на тот же URL

            # Приоритет 2: images[0].src
            images = data.get("images")
            if isinstance(images, list) and images:
                first = images[0]
                if isinstance(first, dict):
                    src = first.get("src") or first.get("image")
                    if isinstance(src, str) and src.startswith("http"):
                        if "image_url" not in out:
                            out["image_url"] = src
                        # Дополнительно — все картинки
                        out["images"] = [
                            img.get("src") for img in images
                            if isinstance(img, dict) and isinstance(img.get("src"), str)
                        ]
                elif isinstance(first, str) and first.startswith("http"):
                    if "image_url" not in out:
                        out["image_url"] = first

    return out


class OzonParser(BaseParser):
    marketplace = "ozon"
    base_url = "https://www.ozon.ru"

    def __init__(self, timeout: float = 30.0, min_gap: float = 2.5,
                 proxy: Optional[str] = None, cdp_host: str = "127.0.0.1",
                 cdp_port: int = 9222):
        self.timeout = timeout
        self.min_gap = min_gap
        self.cdp_host = cdp_host
        self.cdp_port = cdp_port
        self._last_request_at = 0.0
        self.client = httpx.Client(
            timeout=timeout,
            headers=OZON_HEADERS,
            follow_redirects=True,
            proxy=proxy,
        )

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_gap:
            time.sleep(self.min_gap - elapsed)
        self._last_request_at = time.monotonic()

    def extract_sku(self, url: str) -> Optional[str]:
        m = re.search(r'/product/[^/]*?(\d{6,})', url)
        return m.group(1) if m else None

    def _canonical_product_path(self, url: str) -> Optional[str]:
        sku = self.extract_sku(url)
        if not sku:
            return None
        return f"/product/{sku}/"

    def _cdp_available(self) -> bool:
        """Проверить, что CDP отвечает."""
        try:
            r = httpx.get(f"http://{self.cdp_host}:{self.cdp_port}/json/version", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    def _cdp_fetch(self, api_url: str) -> Optional[tuple[int, str]]:
        """
        Tier-2: fetch ИЗНУТРИ уже открытой вкладки ozon.ru через Chrome CDP.

        ВАЖНО: используем существующую вкладку ozon.ru, а не создаём новую.
        Новая вкладка не успевает загрузить cookies и получает 403.
        """

        try:
            from websocket import create_connection
        except ImportError:
            logger.error("websocket-client НЕ установлен. pip install websocket-client")
            return None

        ws = None
        try:
            # 1. Ищем СУЩЕСТВУЮЩУЮ вкладку ozon.ru
            logger.info("Ищу вкладку ozon.ru через /json/list...")
            tabs_resp = httpx.get(f"http://{self.cdp_host}:{self.cdp_port}/json/list", timeout=5.0)
            if tabs_resp.status_code != 200:
                logger.error(f"/json/list вернул HTTP {tabs_resp.status_code}")
                return None

            tabs = tabs_resp.json()
            tab = next(
                (t for t in tabs
                 if t.get("type") == "page" and "ozon.ru" in t.get("url", "")),
                None,
            )

            # Если вкладки нет — создаём новую и ждём загрузки
            if not tab:
                logger.info("Вкладка ozon.ru не найдена, создаю новую...")
                new_tab_resp = httpx.put(
                    f"http://{self.cdp_host}:{self.cdp_port}/json/new?https://www.ozon.ru/",
                    timeout=10.0,
                )
                if new_tab_resp.status_code != 200:
                    logger.error(f"/json/new вернул HTTP {new_tab_resp.status_code}")
                    return None
                tab = new_tab_resp.json()
                logger.info(f"Ждём загрузку новой вкладки (8 сек)...")
                time.sleep(8)

            ws_url = tab.get("webSocketDebuggerUrl")
            tab_id = tab.get("id")
            logger.info(f"Использую вкладку: id={tab_id}, url={tab.get('url', '')[:60]}")

            # 2. Подключаемся к WebSocket вкладки
            ws = create_connection(ws_url, timeout=self.timeout)
            logger.info("WebSocket подключён")

            msg_id = [0]

            def send(method: str, params: dict | None = None, timeout_s: float = 60.0) -> dict:
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
                    except Exception as e:
                        logger.error(f"WS recv timeout: {e}")
                        return {}
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if data.get("id") == current_id:
                        return data
                logger.error(f"Не дождались ответа на {method}")
                return {}

            send("Runtime.enable")

            # 3. Выполняем fetch ИЗНУТРИ страницы ozon.ru
            logger.info(f"Выполняю fetch: {api_url[:80]}...")
            js = f"""
            (async () => {{
                try {{
                    const res = await fetch({json.dumps(api_url)}, {{
                        credentials: 'include',
                        headers: {{
                            'Accept': 'application/json',
                            'x-o3-app-name': 'dweb_client',
                            'x-o3-app-version': 'release_2-7-1',
                            'x-o3-page-type': 'detail'
                        }}
                    }});
                    const text = await res.text();
                    return JSON.stringify({{status: res.status, text: text}});
                }} catch (e) {{
                    return JSON.stringify({{status: 0, text: String(e)}});
                }}
            }})()
            """

            result = send(
                "Runtime.evaluate",
                {
                    "expression": js,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
                timeout_s=60.0,
            )

            value = (result.get("result") or {}).get("result", {}).get("value")
            if not value:
                logger.error(f"Пустой value: {json.dumps(result)[:300]}")
                return None

            parsed = json.loads(value)
            logger.info(f"fetch вернул: status={parsed.get('status')}, body_len={len(parsed.get('text', ''))}")
            return parsed.get("status", 0), parsed.get("text", "")

        except Exception as e:
            logger.exception(f"Ошибка CDP: {e}")
            return None
        finally:
            # ВАЖНО: НЕ закрываем вкладку — она нужна для следующих запросов
            try:
                if ws:
                    ws.close()
            except Exception:
                pass

    def _cdp_read_dom_prices(self, sku: str) -> Optional[Dict[str, Any]]:
        """
        Читает цены Ozon прямо из DOM через CDP.

        Извлекает:
          - price_rub       — обычная цена ("С другими банками")
          - card_price      — цена с Ozon Card ("С банками")
          - price_original  — зачёркнутая цена (если есть)
        """

        try:
            from websocket import create_connection
        except ImportError:
            logger.error("websocket-client не установлен")
            return None

        ws = None
        try:
            # 1. Ищем вкладку ozon.ru
            tabs = httpx.get(
                f"http://{self.cdp_host}:{self.cdp_port}/json/list", timeout=5.0
            ).json()
            tab = next(
                (t for t in tabs
                 if t.get("type") == "page" and "ozon.ru" in t.get("url", "")),
                None,
            )
            if not tab:
                return None

            # 2. Подключаемся к WebSocket
            ws = create_connection(tab["webSocketDebuggerUrl"], timeout=self.timeout)
            msg_id = [0]

            def send(method: str, params: dict | None = None, timeout_s: float = 30.0) -> dict:
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

            send("Runtime.enable")

            # 3. JS для чтения цен из DOM
            js = r"""
            (() => {
                const result = {};

                // Функция для извлечения числа из строки "1 213 ₽"
                const extractPrice = (text) => {
                    if (!text) return null;
                    const digits = text.replace(/[^\d]/g, '');
                    return digits ? parseInt(digits, 10) : null;
                };

                // Ищем блок "С банками" (цена с Ozon Card)
                const cardBlock = document.querySelector('.pdp_b2h');
                if (cardBlock) {
                    const cardPriceEl = cardBlock.querySelector('.tsHeadline600Large');
                    result.card_price = extractPrice(cardPriceEl ? cardPriceEl.textContent : '');
                }

                // Ищем блок "С другими банками" (обычная цена)
                const regularBlock = document.querySelector('.pdp_b2i');
                if (regularBlock) {
                    const priceEl = regularBlock.querySelector('.tsHeadline500Medium');
                    result.price = extractPrice(priceEl ? priceEl.textContent : '');
                }

                // Зачёркнутая цена (если есть) — ищем в разных вариантах
                const originalSelectors = [
                    '.pdp_b2d .tsHeadline500Medium',  // Ozon: зачёркнутая цена в старом дизайне
                    '[class*="oldPrice"]',
                    '[class*="old-price"]',
                ];
                for (const sel of originalSelectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        result.price_original = extractPrice(el.textContent);
                        if (result.price_original) break;
                    }
                }

                // Fallback — ищем везде числа с "₽"
                if (!result.price && !result.card_price) {
                    const allPrices = [];
                    document.querySelectorAll('*').forEach(el => {
                        if (el.children.length === 0) {
                            const t = el.textContent.trim();
                            const m = t.match(/^[\d\s\u00a0]+₽$/);
                            if (m) {
                                const n = parseInt(t.replace(/[^\d]/g, ''), 10);
                                if (n > 0) allPrices.push(n);
                            }
                        }
                    });
                    // Обычно две цены: [card_price, price] или [price, card_price]
                    if (allPrices.length >= 2) {
                        result.card_price = Math.min(...allPrices);
                        result.price = Math.max(...allPrices);
                    }
                }

                return JSON.stringify(result);
            })()
            """

            result = send("Runtime.evaluate", {
                "expression": js,
                "returnByValue": True,
            }, timeout_s=30.0)

            value = (result.get("result") or {}).get("result", {}).get("value")
            if not value:
                logger.error(f"Пустой ответ DOM: {json.dumps(result)[:300]}")
                return None

            parsed = json.loads(value)
            logger.info(f"DOM prices: {parsed}")
            return parsed

        except Exception as e:
            logger.exception(f"Ошибка чтения DOM цен: {e}")
            return None
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    def fetch(self, url: str) -> ProductData:
        path = self._canonical_product_path(url)
        if not path:
            return ProductData(url=url, marketplace=self.marketplace, success=False,
                               error="Не удалось извлечь SKU из URL")

        api_url = (
            "https://www.ozon.ru/api/composer-api.bx/page/json/v2?"
            f"url={urllib.parse.quote(path, safe='/')}"
        )

        data = None
        tier = None

        # === Tier 1: httpx ===
        try:
            self._throttle()
            resp = self.client.get(api_url)
            if resp.status_code == 200 and resp.text and not resp.text.lstrip().startswith("<"):
                data = resp.json()
                tier = "http"
        except httpx.HTTPError:
            pass

        # === Tier 2: CDP (если Tier 1 не сработал) ===
        if data is None:
            if not self._cdp_available():
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error=(
                        f"HTTP 403 — Ozon блокирует IP. "
                        f"Chrome CDP НЕ найден на {self.cdp_host}:{self.cdp_port}. "
                        "Запустите start-chrome-cdp.bat и прогрейте ozon.ru."
                    ),
                )

            result = self._cdp_fetch(api_url)
            if result is None:
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error="CDP fetch failed — не удалось выполнить запрос через Chrome.",
                )

            status_code, body = result
            if status_code == 200 and body and not body.lstrip().startswith("<"):
                try:
                    data = json.loads(body)
                    tier = "cdp"
                except json.JSONDecodeError:
                    return ProductData(
                        url=url, marketplace=self.marketplace, success=False,
                        error="CDP вернул non-JSON (возможно, капча в Chrome).",
                    )
            else:
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error=(
                        f"CDP fetch вернул HTTP {status_code}. "
                        "Откройте ozon.ru в Chrome CDP, пройдите капчу, повторите."
                    ),
                )

        # === Разбор ===
        if not isinstance(data, dict) or not isinstance(data.get("widgetStates"), dict):
            return ProductData(url=url, marketplace=self.marketplace, success=False,
                               error="parser_drift — нет widgetStates")

        parsed = _parse_widgets(data)
        if not parsed.get("title"):
            return ProductData(url=url, marketplace=self.marketplace, success=False,
                               error="parser_drift — нет title")

        if parsed.get("price") is None or parsed.get("card_price") is None:
            logger.info(f"API не вернул цены, читаю DOM для SKU={self.extract_sku(url)}")
            dom_prices = self._cdp_read_dom_prices(self.extract_sku(url))
            if dom_prices:
                if parsed.get("price") is None and dom_prices.get("price"):
                    parsed["price"] = dom_prices["price"]
                if parsed.get("card_price") is None and dom_prices.get("card_price"):
                    parsed["card_price"] = dom_prices["card_price"]
                if parsed.get("price_original") is None and dom_prices.get("price_original"):
                    parsed["price_original"] = dom_prices["price_original"]

        return ProductData(
            title=parsed.get("title") or "Не найдено",
            price_rub=parsed.get("price"),
            price_with_plus=parsed.get("card_price"),
            price_original_rub=parsed.get("price_original"),
            url=f"https://www.ozon.ru{path}",
            marketplace=self.marketplace,
            sku=self.extract_sku(url),
            seller=parsed.get("seller") or "",
            rating=parsed.get("rating_score"),
            rating_count=parsed.get("rating_count"),
            in_stock=parsed.get("is_available"),
            image_url=parsed.get("image_url"),
            success=True,
            tier_used=tier,
        )

    def close(self):
        self.client.close()