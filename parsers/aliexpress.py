"""
Парсер AliExpress через Chrome CDP.

AliExpress защищён x5sec. Анонимный HTTP получает challenge-страницу.
Решение: читать данные из DOM уже открытой вкладки aliexpress.ru
в Chrome CDP.

Порядок работы:
1. Прогрейте aliexpress.ru в Chrome CDP (пройдите x5sec вручную).
2. Откройте карточку товара в этом же Chrome.
3. Парсер прочитает DOM существующей вкладки.
"""

import json
import logging
import re
import time
from typing import Optional

import httpx

from core.models import ProductData
from .base import BaseParser

logger = logging.getLogger("aliexpress")


_ITEM_ID_RE = re.compile(r"/item/(\d{9,})(?:\.html)?")
_BARE_ID_RE = re.compile(r"\d{9,}")


class AliexpressParser(BaseParser):
    marketplace = "aliexpress"
    base_url = "https://aliexpress.ru"

    def __init__(self, timeout: float = 30.0, min_gap: float = 3.0,
                 cdp_host: str = "127.0.0.1", cdp_port: int = 9222):
        self.timeout = timeout
        self.min_gap = min_gap
        self.cdp_host = cdp_host
        self.cdp_port = cdp_port
        self._last_request_at = 0.0
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_gap:
            time.sleep(self.min_gap - elapsed)
        self._last_request_at = time.monotonic()

    def extract_sku(self, url: str) -> Optional[str]:
        if _BARE_ID_RE.fullmatch(url.strip()):
            return url.strip()
        m = _ITEM_ID_RE.search(url)
        return m.group(1) if m else None

    def _cdp_available(self) -> bool:
        try:
            r = httpx.get(f"http://{self.cdp_host}:{self.cdp_port}/json/version", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    def _cdp_read_dom(self, item_id: str) -> Optional[dict]:
        """
        Читает DOM карточки AliExpress из Chrome CDP.

        Ищет вкладку с URL, содержащим /item/{item_id}.
        Если не находит — открывает новую вкладку с карточкой и ждёт загрузки.
        """

        try:
            from websocket import create_connection
        except ImportError:
            return None

        target_url = f"https://aliexpress.ru/item/{item_id}.html"
        ws = None
        try:
            # 1. Ищем вкладку с этим товаром
            tabs = httpx.get(f"http://{self.cdp_host}:{self.cdp_port}/json/list", timeout=5.0).json()
            tab = next(
                (t for t in tabs
                 if t.get("type") == "page" and f"/item/{item_id}" in t.get("url", "")),
                None,
            )

            # Если нет — создаём новую
            if not tab:
                logger.info(f"Вкладка /item/{item_id} не найдена, создаю новую...")
                new_tab = httpx.put(
                    f"http://{self.cdp_host}:{self.cdp_port}/json/new?{target_url}",
                    timeout=10.0,
                )
                if new_tab.status_code != 200:
                    logger.error(f"/json/new вернул HTTP {new_tab.status_code}")
                    return None

                # Ждём появления вкладки с нужным URL в /json/list
                tab = None
                for attempt in range(20):  # до 20 секунд
                    time.sleep(1.0)
                    try:
                        tabs = httpx.get(
                            f"http://{self.cdp_host}:{self.cdp_port}/json/list",
                            timeout=5.0,
                        ).json()
                    except Exception:
                        continue

                    candidate = next(
                        (t for t in tabs
                         if t.get("type") == "page"
                         and f"/item/{item_id}" in t.get("url", "")
                         and t.get("webSocketDebuggerUrl")),
                        None,
                    )
                    if candidate:
                        tab = candidate
                        logger.info(f"Вкладка готова после {attempt + 1} сек ожидания")
                        break

                if not tab:
                    logger.error(f"Вкладка /item/{item_id} не появилась за 20 сек")
                    return None

                # Даём странице догрузиться (x5sec, DOM, цены)
                time.sleep(3.0)

            ws_url = tab.get("webSocketDebuggerUrl")
            if not ws_url:
                logger.error("У вкладки нет webSocketDebuggerUrl")
                return None

            ws = create_connection(ws_url, timeout=self.timeout)

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

            # JS для чтения DOM
            js = r"""
            (() => {
                const result = {};
                if (document.title.includes('проверк') ||
                    document.body.innerText.includes('Пройдите проверку')) {
                    result.punish = true;
                    return JSON.stringify(result);
                }

                const h1 = document.querySelector('h1');
                result.title = h1 ? h1.textContent.trim() : null;

                const currentPriceSelectors = [
                    '[class*="HazeProductPrice_SnowPrice__mainM"]',
                    '[class*="HazeProductPrice_SnowPrice__mainS"]',
                    '[class*="HazeStickyOfferPrice_HazeStickyOfferPrice__price"]'
                ];
                const oldPriceSelectors = [
                    '[class*="HazeProductPrice_SnowPrice__secondPrice"]',
                    '[class*="HazeStickyOfferPrice_HazeStickyOfferPrice__secondPrice"]'
                ];

                const extractPrice = (el) => {
                    if (!el) return null;
                    const t = el.textContent.trim();
                    const m = t.match(/[\d\s\u00a0]+/);
                    if (!m) return null;
                    const n = parseInt(m[0].replace(/[^\d]/g, ''), 10);
                    return n > 0 ? n : null;
                };

                let currentPrice = null;
                for (const sel of currentPriceSelectors) {
                    currentPrice = extractPrice(document.querySelector(sel));
                    if (currentPrice) break;
                }

                let oldPrice = null;
                for (const sel of oldPriceSelectors) {
                    oldPrice = extractPrice(document.querySelector(sel));
                    if (oldPrice) break;
                }

                result.price_current = currentPrice;
                result.price_old = oldPrice;

                img = document.querySelector('img[src*="aliexpress-media"]');
                let imageUrl = img ? img.src : null;
                
                // Страховка от двойного расширения
                if (imageUrl) {
                    imageUrl = imageUrl.replace(/\.(jpg|jpeg|png|webp)\.(jpg|jpeg|png|webp)$/i, '.$2');
                    // Апгрейд 120x120 → 960x960
                    imageUrl = imageUrl.replace(/_(\d+x\d+)\.(jpg|jpeg|png|webp)$/i, '_960x960.$2');
                }
                result.image_url = imageUrl;

                return JSON.stringify(result);
            })()
            """

            result = send("Runtime.evaluate", {
                "expression": js,
                "returnByValue": True,
            }, timeout_s=30.0)

            value = (result.get("result") or {}).get("result", {}).get("value")
            if not value:
                return None

            return json.loads(value)


        except Exception as e:
            import traceback
            traceback.print_exc()
            return None
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    def fetch(self, url: str) -> ProductData:
        item_id = self.extract_sku(url)
        if not item_id:
            return ProductData(url=url, marketplace=self.marketplace, success=False,
                               error="Не удалось извлечь item_id из URL")

        if not self._cdp_available():
            return ProductData(
                url=url, marketplace=self.marketplace, sku=item_id, success=False,
                error=(
                    f"Chrome CDP НЕ найден на {self.cdp_host}:{self.cdp_port}. "
                    "Запустите Chrome с --remote-debugging-port=9222, "
                    "откройте aliexpress.ru и пройдите x5sec вручную."
                ),
            )

        dom = None
        for attempt in range(3):
            dom = self._cdp_read_dom(item_id)
            if dom is None:
                if attempt < 2:
                    time.sleep(2)
                    continue
                return ProductData(
                    url=url, marketplace=self.marketplace, sku=item_id, success=False,
                    error="Не удалось прочитать DOM карточки AliExpress.",
                )

            if dom.get("punish"):
                if attempt < 2:
                    time.sleep(5)
                    continue
                return ProductData(
                    url=url, marketplace=self.marketplace, sku=item_id, success=False,
                    error=(
                        "AliExpress показал x5sec проверку (3 попытки). "
                        "Откройте aliexpress.ru в Chrome CDP и пройдите проверку вручную."
                    ),
                )
            break

        title = dom.get("title")
        if not title:
            return ProductData(
                url=url, marketplace=self.marketplace, sku=item_id, success=False,
                error="Не удалось прочитать title из DOM.",
            )

        # Цены из DOM
        price_rub = dom.get("price_current")
        price_original = dom.get("price_old")
        # Если старая цена <= текущей — это не зачёркнутая, а дубль
        if price_original is not None and price_rub is not None and price_original <= price_rub:
            price_original = None

        return ProductData(
            title=title,
            price_rub=float(price_rub) if price_rub else None,
            price_original_rub=float(price_original) if price_original else None,
            url=f"https://aliexpress.ru/item/{item_id}.html",
            marketplace=self.marketplace,
            sku=item_id,
            rating=dom.get("rating"),
            rating_count=dom.get("orders"),
            image_url=dom.get("image_url"),
            success=True,
            tier_used="cdp_dom",
        )

    def close(self):
        self.client.close()