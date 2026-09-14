"""
Парсер Wildberries через публичный v4 API.

Endpoints:
  - https://card.wb.ru/cards/v4/detail?nm={id}&dest=-1257786

CRITICAL: цены НЕ в priceU/salePriceU (они null с Nov 2026).
Реальные цены — в sizes[0].price.{product, basic} в копейках.

Обязательные параметры:
  - dest=-1257786 (Москва) — без него пустые stocks/неверные цены
  - appType=1, curr=rub, locale=ru, spp=30
"""

import math
import re
import time
from typing import Any, Optional, List
from urllib.parse import urlencode
import json

import httpx

from core.models import ProductData
from .base import BaseParser


WB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def _kopeck_to_rub(v: Any) -> Optional[float]:
    """WB integer kopecks -> float rubles."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            rubles = v / 100
        except OverflowError:
            return None
        return rubles if rubles > 0 and math.isfinite(rubles) else None
    if isinstance(v, str):
        s = v.strip()
        if not s or not s.isdigit():
            return None
        try:
            rubles = int(s) / 100
        except (ValueError, OverflowError):
            return None
        return rubles if rubles > 0 and math.isfinite(rubles) else None
    return None


def _extract_price_rub(p: dict) -> tuple[Optional[float], Optional[float]]:
    """(current_rub, original_rub) из sizes[0].price, с fallback на priceU/salePriceU."""
    if not isinstance(p, dict):
        return (None, None)
    sizes = p.get("sizes") or []
    if isinstance(sizes, list):
        for sz in sizes:
            if not isinstance(sz, dict):
                continue
            price = sz.get("price")
            if not isinstance(price, dict):
                continue
            product = _kopeck_to_rub(price.get("product") or price.get("total"))
            basic = _kopeck_to_rub(price.get("basic")) or product
            if product:
                return (product, basic or product)
    sale = _kopeck_to_rub(p.get("salePriceU"))
    base = _kopeck_to_rub(p.get("priceU"))
    if sale or base:
        return ((sale or base), (base or sale))
    return (None, None)


def _decode_mojibake(s: Any) -> str:
    """Fix WB double-encoded UTF-8."""
    if not isinstance(s, str):
        return ""
    if not s:
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
        cyr_orig = sum("\u0400" <= c <= "\u04ff" for c in s)
        cyr_fixed = sum("\u0400" <= c <= "\u04ff" for c in fixed)
        return fixed if cyr_fixed > cyr_orig else s
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def _coerce_int(v: Any) -> Optional[int]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if math.isfinite(v) else None
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


class WildberriesParser(BaseParser):
    """Парсер Wildberries через card.wb.ru v4"""

    marketplace = "wildberries"
    base_url = "https://www.wildberries.ru"
    default_dest = "-1257786"  # Москва

    def __init__(self, timeout: float = 15.0, min_gap: float = 1.5, proxy: Optional[str] = None,
                 cdp_host: str = "127.0.0.1", cdp_port: int = 9222):
        self.timeout = timeout
        self.min_gap = min_gap
        self.cdp_host = cdp_host
        self.cdp_port = cdp_port
        self._last_request_at = 0.0
        self.client = httpx.Client(
            timeout=timeout,
            headers=WB_HEADERS,
            follow_redirects=False,
            proxy=proxy,
        )

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_gap:
            time.sleep(self.min_gap - elapsed)
        self._last_request_at = time.monotonic()

    def _cdp_available(self) -> bool:
        """Проверить, что Chrome CDP отвечает."""
        try:
            r = httpx.get(f"http://{self.cdp_host}:{self.cdp_port}/json/version", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    def _cdp_fetch(self, api_url: str) -> Optional[tuple[int, str]]:
        """
        Fetch через Chrome CDP с существующей вкладки wildberries.ru.

        Использует УЖЕ ОТКРЫТУЮ вкладку wildberries.ru (не создаёт новую).
        Прогрейте её вручную: откройте https://www.wildberries.ru/ в Chrome CDP.
        """
        import logging
        logger = logging.getLogger("wb.cdp")

        try:
            from websocket import create_connection
        except ImportError:
            logger.error("websocket-client не установлен")
            return None

        ws = None
        try:
            # 1. Ищем вкладку wildberries.ru
            tabs_resp = httpx.get(f"http://{self.cdp_host}:{self.cdp_port}/json/list", timeout=5.0)
            if tabs_resp.status_code != 200:
                return None
            tabs = tabs_resp.json()

            tab = next(
                (t for t in tabs
                 if t.get("type") == "page" and "wildberries.ru" in t.get("url", "")),
                None,
            )

            if not tab:
                logger.info("Вкладка wildberries.ru не найдена, создаю новую...")
                new_tab = httpx.put(
                    f"http://{self.cdp_host}:{self.cdp_port}/json/new?https://www.wildberries.ru/",
                    timeout=10.0,
                )
                if new_tab.status_code != 200:
                    return None
                tab = new_tab.json()
                time.sleep(8)

            ws_url = tab.get("webSocketDebuggerUrl")
            logger.info(f"CDP вкладка: id={tab.get('id')}, url={tab.get('url', '')[:60]}")

            ws = create_connection(ws_url, timeout=self.timeout)

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

            # 2. Fetch API изнутри страницы wildberries.ru
            js = f"""
            (async () => {{
                try {{
                    const res = await fetch({json.dumps(api_url)}, {{
                        credentials: 'include',
                        headers: {{
                            'Accept': 'application/json',
                            'Accept-Language': 'ru-RU,ru;q=0.9'
                        }}
                    }});
                    const text = await res.text();
                    return JSON.stringify({{status: res.status, text: text}});
                }} catch (e) {{
                    return JSON.stringify({{status: 0, text: String(e)}});
                }}
            }})()
            """

            result = send("Runtime.evaluate", {
                "expression": js,
                "awaitPromise": True,
                "returnByValue": True,
            }, timeout_s=60.0)

            value = (result.get("result") or {}).get("result", {}).get("value")
            if not value:
                logger.error(f"Пустой ответ CDP: {json.dumps(result)[:300]}")
                return None

            parsed = json.loads(value)
            logger.info(f"CDP fetch: status={parsed.get('status')}, body_len={len(parsed.get('text', ''))}")
            return parsed.get("status", 0), parsed.get("text", "")

        except Exception as e:
            logger.exception(f"Ошибка CDP: {e}")
            return None
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    def extract_sku(self, url: str) -> Optional[str]:
        m = re.search(r'/catalog/(\d+)', url)
        return m.group(1) if m else None

    def fetch(self, url: str) -> ProductData:
        sku = self.extract_sku(url)
        if not sku:
            return ProductData(url=url, marketplace=self.marketplace, success=False,
                               error="Не удалось извлечь SKU из URL")

        params = urlencode({
            "appType": "1",
            "curr": "rub",
            "dest": self.default_dest,
            "locale": "ru",
            "spp": "30",
            "nm": sku,
        })
        api_url = f"https://card.wb.ru/cards/v4/detail?{params}"

        data = None
        tier = None

        # === Tier 1: httpx ===
        try:
            self._throttle()
            resp = self.client.get(api_url)
            if resp.status_code == 200 and resp.text and not resp.text.lstrip().startswith("<"):
                try:
                    data = resp.json()
                    tier = "api"
                except json.JSONDecodeError:
                    pass
        except httpx.HTTPError:
            pass

        # === Tier 2: CDP (если Tier 1 не сработал) ===
        if data is None:
            if not self._cdp_available():
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error=(
                        "WB HTTP 403 и Chrome CDP не запущен. "
                        f"Запустите Chrome с --remote-debugging-port={self.cdp_port}, "
                        "откройте https://www.wildberries.ru/, затем повторите."
                    ),
                )

            cdp_result = self._cdp_fetch(api_url)
            if cdp_result is None:
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error="CDP fetch failed — не удалось выполнить запрос через Chrome.",
                )

            status_code, body = cdp_result
            if status_code == 200 and body and not body.lstrip().startswith("<"):
                try:
                    data = json.loads(body)
                    tier = "cdp"
                except json.JSONDecodeError:
                    return ProductData(
                        url=url, marketplace=self.marketplace, success=False,
                        error="CDP вернул non-JSON.",
                    )
            else:
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error=f"CDP fetch вернул HTTP {status_code}.",
                )

        products = data.get("products") or (data.get("data") or {}).get("products") or []
        if not products:
            return ProductData(url=url, marketplace=self.marketplace, success=False,
                               error="Товар не найден")

        p = products[0]
        current, original = _extract_price_rub(p)
        qty = _coerce_int(p.get("totalQuantity"))

        return ProductData(
            title=_decode_mojibake(p.get("name", "")) or "Не найдено",
            price_rub=current,
            price_original_rub=original,
            url=url,
            marketplace=self.marketplace,
            sku=str(p.get("id", sku)),
            seller=_decode_mojibake(p.get("supplier", "")),
            brand=_decode_mojibake(p.get("brand", "")),
            rating=p.get("reviewRating"),
            rating_count=_coerce_int(p.get("feedbacks")),
            in_stock=qty is not None and qty > 0 and current is not None,
            image_url=f"https://basket-01.wbbasket.ru/vol{int(sku)//100000}/part{int(sku)//1000}/{sku}/images/big/1.webp",
            success=True,
            tier_used=tier,
        )

    def close(self):
        self.client.close()