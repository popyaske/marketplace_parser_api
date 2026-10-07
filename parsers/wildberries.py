"""
Парсер Wildberries (новая схема).

WB закрыл анонимный доступ к card.wb.ru. API живёт на:
  https://www.wildberries.ru/__internal/card/cards/v4/detail

Требует:
  - Bearer-токен из localStorage['wbid-oauth-sdk-access-token']
  - deviceid из localStorage['wbx__sessionID'] ('site_<32hex>')
  - xinfo-строку параметров из localStorage['geo-data-v1-...'].data.xinfo
  - Живую залогиненную сессию в Chrome

Работает ТОЛЬКО через Chrome CDP. Если CDP недоступен — возвращает
success=False с понятной ошибкой.
"""

import json
import logging
import math
import re
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx

from core.models import ProductData
from .base import BaseParser

logger = logging.getLogger("wildberries")


WB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}


# ============================================================
# Утилиты парсинга цен
# ============================================================

def _kopeck_to_rub(v: Any) -> Optional[float]:
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


def _extract_price_rub(p: dict) -> Tuple[Optional[float], Optional[float]]:
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


# ============================================================
# Основной парсер
# ============================================================

class WildberriesParser(BaseParser):
    """Парсер WB через внутренний API с авторизацией (только CDP)."""

    marketplace = "wildberries"
    base_url = "https://www.wildberries.ru"
    API_BASE = "https://www.wildberries.ru/__internal/card/cards/v4/detail"

    # JS для сбора токенов из localStorage вкладки
    _JS_COLLECT_CREDS = r"""
    (() => {
        const token = localStorage.getItem('wbid-oauth-sdk-access-token');
        const deviceid = localStorage.getItem('wbx__sessionID');
        let xinfo = null;
        for (const k of Object.keys(localStorage)) {
            if (k.startsWith('geo-data-v1-')) {
                try {
                    const v = JSON.parse(localStorage.getItem(k));
                    if (v?.data?.xinfo) { xinfo = v.data.xinfo; break; }
                } catch (e) {}
            }
        }
        return JSON.stringify({ token, deviceid, xinfo });
    })()
    """

    def __init__(
        self,
        timeout: float = 30.0,
        min_gap: float = 1.5,
        proxy: Optional[str] = None,
        cdp_host: str = "127.0.0.1",
        cdp_port: int = 9222,
    ):
        self.timeout = timeout
        self.min_gap = min_gap
        self.cdp_host = cdp_host
        self.cdp_port = cdp_port
        self.proxy = proxy
        self._last_request_at = 0.0
        self._last_image_url: Optional[str] = None

        # httpx нужен только для CDP discovery
        self.client = httpx.Client(timeout=10.0)

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_gap:
            time.sleep(self.min_gap - elapsed)
        self._last_request_at = time.monotonic()

    # -------------------- URL --------------------

    def _build_api_url(self, sku: str, xinfo: str) -> str:
        extra = urlencode({"lang": "ru", "mtype": "257", "nm": sku})
        return f"{self.API_BASE}?{xinfo}&{extra}"

    # -------------------- CDP --------------------

    def _cdp_available(self) -> bool:
        try:
            r = httpx.get(
                f"http://{self.cdp_host}:{self.cdp_port}/json/version", timeout=2.0
            )
            return r.status_code == 200
        except Exception:
            return False

    def _cdp_call(self, sku: str) -> Optional[Tuple[int, str, Optional[str]]]:
        """
        Возвращает (status, body, image_url).
        image_url ищется двумя способами:
          1) regex по HTML карточки (быстро)
          2) навигация на карточку + DOM querySelector (медленнее, надёжнее)
        """
        try:
            from websocket import create_connection
        except ImportError:
            logger.error("websocket-client не установлен")
            return None

        ws = None
        try:
            tabs = httpx.get(
                f"http://{self.cdp_host}:{self.cdp_port}/json/list", timeout=5.0
            ).json()

            tab = next(
                (t for t in tabs
                 if t.get("type") == "page" and "wildberries.ru" in t.get("url", "")),
                None,
            )
            if not tab:
                logger.warning("CDP: вкладка wildberries.ru не найдена")
                return None

            ws = create_connection(tab["webSocketDebuggerUrl"], timeout=self.timeout)
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

            send("Runtime.enable")
            send("Page.enable")

            # === 1. Собрать credentials ===
            creds_result = send("Runtime.evaluate", {
                "expression": self._JS_COLLECT_CREDS,
                "returnByValue": True,
            }, timeout_s=10.0)

            creds_raw = ((creds_result.get("result") or {}).get("result") or {}).get("value")
            if not creds_raw:
                logger.error("CDP: не удалось получить credentials")
                return None

            creds = json.loads(creds_raw)
            token = creds.get("token")
            deviceid = creds.get("deviceid")
            xinfo = creds.get("xinfo")

            if not (token and deviceid and xinfo):
                logger.error("CDP: token/deviceid/xinfo отсутствуют — залогиньтесь в WB")
                return None

            api_url = self._build_api_url(sku, xinfo)
            logger.info(f"CDP: token={token[:20]}..., deviceid={deviceid[:20]}...")

            # === 2. Fetch JSON ===
            referer = f"https://www.wildberries.ru/catalog/{sku}/detail.aspx"
            fetch_js = f"""
            (async () => {{
                try {{
                    const r = await fetch({json.dumps(api_url)}, {{
                        credentials: 'include',
                        headers: {{
                            'accept': '*/*',
                            'accept-language': 'ru-RU,ru;q=0.9',
                            'authorization': 'Bearer ' + {json.dumps(token)},
                            'deviceid': {json.dumps(deviceid)},
                            'referer': {json.dumps(referer)},
                            'x-requested-with': 'XMLHttpRequest',
                            'x-spa-version': '14.26.4'
                        }}
                    }});
                    const text = await r.text();
                    return JSON.stringify({{status: r.status, text: text}});
                }} catch (e) {{
                    return JSON.stringify({{status: 0, text: String(e)}});
                }}
            }})()
            """
            result = send("Runtime.evaluate", {
                "expression": fetch_js,
                "awaitPromise": True,
                "returnByValue": True,
            }, timeout_s=40.0)

            value = ((result.get("result") or {}).get("result") or {}).get("value")
            if not value:
                logger.error(f"CDP: пустой ответ: {json.dumps(result)[:300]}")
                return None

            parsed = json.loads(value)
            status = parsed.get("status", 0)
            body = parsed.get("text", "")
            logger.info(f"CDP fetch: status={status}, body_len={len(body)}")

            if status != 200:
                return status, body, None

            # === 3. Найти URL картинки ===
            image_url = self._cdp_find_image(send, sku)
            return status, body, image_url

        except Exception as e:
            logger.exception(f"CDP ошибка: {e}")
            return None
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    def _cdp_find_image(self, send, sku: str) -> Optional[str]:
        """Ищет URL главной картинки товара."""
        card_url = f"https://www.wildberries.ru/catalog/{sku}/detail.aspx"

        # === Попытка 1: regex по HTML ===
        html_js = f"""
        (async () => {{
            try {{
                const r = await fetch({json.dumps(card_url)}, {{credentials: 'include'}});
                const html = await r.text();
                const m = html.match(/https:\\/\\/[a-z0-9-]+\\.(geobasket|wbbasket)\\.ru\\/vol\\d+\\/part\\d+\\/\\d+\\/images\\/[^"'\\s)]+/);
                return m ? m[0] : null;
            }} catch (e) {{ return null; }}
        }})()
        """
        res = send("Runtime.evaluate", {
            "expression": html_js,
            "awaitPromise": True,
            "returnByValue": True,
        }, timeout_s=15.0)
        url = ((res.get("result") or {}).get("result") or {}).get("value")
        if url:
            logger.info(f"image (HTML): {url}")
            return url

        # === Попытка 2: навигация + DOM ===
        logger.info("image: HTML не дал URL, навигирую на карточку...")
        send("Page.navigate", {"url": card_url})
        time.sleep(5)

        dom_js = r"""
        (() => {
            const selectors = [
                'img[src*="geobasket"][src*="/images/c516x688/"]',
                'img[src*="geobasket"][src*="/images/big/"]',
                'img[src*="geobasket"][src*="/images/"]',
                'img[src*="wbbasket"][src*="/images/"]',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.src && el.src.startsWith('http')) {
                    return el.src.replace(/\/images\/c\d+x\d+\//, '/images/big/');
                }
            }
            return null;
        })()
        """
        res = send("Runtime.evaluate", {
            "expression": dom_js,
            "returnByValue": True,
        }, timeout_s=10.0)
        url = ((res.get("result") or {}).get("result") or {}).get("value")
        if url:
            logger.info(f"image (DOM): {url}")
        else:
            logger.warning("image: не найдена ни в HTML, ни в DOM")
        return url

    # -------------------- Public API --------------------

    def extract_sku(self, url: str) -> Optional[str]:
        m = re.search(r'/catalog/(\d+)', url)
        return m.group(1) if m else None

    def fetch(self, url: str) -> ProductData:
        sku = self.extract_sku(url)
        if not sku:
            return ProductData(
                url=url, marketplace=self.marketplace, success=False,
                error="Не удалось извлечь SKU из URL",
            )

        # === Проверка CDP ===
        if not self._cdp_available():
            return ProductData(
                url=url, marketplace=self.marketplace, sku=sku, success=False,
                error=(
                    f"Chrome CDP недоступен на {self.cdp_host}:{self.cdp_port}. "
                    "WB работает только через CDP — запустите Chrome с "
                    "--remote-debugging-port=9222, откройте wildberries.ru, "
                    "залогиньтесь, оставьте вкладку открытой."
                ),
            )

        logger.info("WB: пробую CDP + auth...")
        result = self._cdp_call(sku)
        if not result:
            return ProductData(
                url=url, marketplace=self.marketplace, sku=sku, success=False,
                error="CDP не вернул данные. Проверьте, что вкладка wildberries.ru "
                      "открыта и вы залогинены.",
            )

        status_code, body, image_url = result
        if status_code != 200:
            return ProductData(
                url=url, marketplace=self.marketplace, sku=sku, success=False,
                error=f"WB API вернул HTTP {status_code}. "
                      "Возможно, истёк токен — перезайдите на wildberries.ru.",
            )

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return ProductData(
                url=url, marketplace=self.marketplace, sku=sku, success=False,
                error="WB API вернул non-JSON (возможно, капча).",
            )

        self._last_image_url = image_url

        products = (
            data.get("products")
            or (data.get("data") or {}).get("products")
            or []
        )
        if not products:
            return ProductData(
                url=url, marketplace=self.marketplace, sku=sku, success=False,
                error="Товар не найден",
            )

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
            image_url=self._last_image_url,
            success=True,
            tier_used="cdp",
        )

    def close(self):
        self.client.close()