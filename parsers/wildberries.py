"""
Парсер Wildberries (новая схема, работает с ноября 2026).

WB закрыл анонимный доступ к card.wb.ru. Теперь API живёт на:
  https://www.wildberries.ru/__internal/card/cards/v4/detail

Требует:
  - Bearer-токен из localStorage['wbid-oauth-sdk-access-token']
  - deviceid из localStorage['wbx__sessionID'] ('site_<32hex>')
  - xinfo-строку параметров из localStorage['geo-data-v1-...'].data.xinfo
  - Живую залогиненную сессию в Chrome

Стратегия:
  Tier 1: CDP — читает localStorage вкладки wildberries.ru, делает fetch
  Tier 2: Playwright — то же, но через свежий Chromium (если CDP недоступен)

curl_cffi и httpx больше не работают — анонимный доступ закрыт.
"""

import concurrent.futures
import json
import logging
import math
import re
import time
from functools import lru_cache
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
    """Парсер WB через внутренний API с авторизацией."""

    marketplace = "wildberries"
    base_url = "https://www.wildberries.ru"
    API_BASE = "https://www.wildberries.ru/__internal/card/cards/v4/detail"

    # Стелс для Playwright
    STEALTH_INIT_SCRIPT = (
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        "Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en']});"
        "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
    )

    # JS для сбора токенов из localStorage вкладки
    _JS_COLLECT_CREDS = r"""
    (() => {
        const token = localStorage.getItem('wbid-oauth-sdk-access-token');
        const deviceid = localStorage.getItem('wbx__sessionID');
        let xinfo = null;
        // Ключ geo-data-v1-* содержит динамический хэш
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
        use_playwright: bool = True,
    ):
        self.timeout = timeout
        self.min_gap = min_gap
        self.cdp_host = cdp_host
        self.cdp_port = cdp_port
        self.proxy = proxy
        self.use_playwright = use_playwright
        self._last_request_at = 0.0
        self._last_image_url: Optional[str] = None

        # Кэш credentials (token/deviceid/xinfo) — живут 5 минут
        self._creds_cache: Optional[Dict[str, str]] = None
        self._creds_cached_at: float = 0.0
        self._creds_ttl = 300.0

        # httpx нужен только для CDP discovery (не для API)
        self.client = httpx.Client(timeout=10.0)

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_gap:
            time.sleep(self.min_gap - elapsed)
        self._last_request_at = time.monotonic()

    # -------------------- URL --------------------

    def _build_api_url(self, sku: str, xinfo: str) -> str:
        """
        xinfo уже содержит appType, curr, dest, spp, hide_vflags, ab_testing.
        Добавляем lang, mtype, nm.
        """
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

    def _cdp_call(self, api_url: str, sku: str) -> Optional[Tuple[int, str, Optional[str]]]:
        """
        Возвращает (status, body, image_url).
        image_url ищется двумя способами:
          1) regex по HTML карточки (быстро)
          2) навигация на карточку + DOM querySelector (медленнее, но надёжно)
        """
        try:
            from websocket import create_connection
        except ImportError:
            logger.error("websocket-client не установлен")
            return None

        ws = None
        try:
            # Ищем вкладку wildberries.ru
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
        """
        Ищет URL главной картинки товара:
          1) regex по HTML карточки (без навигации — быстро)
          2) навигация на карточку + DOM (медленнее, но надёжно)
        """
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
        time.sleep(5)  # ждём рендера SPA

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
                    // Апгрейдим размер до big
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

    # -------------------- Playwright fallback --------------------

    def _playwright_fetch_sync(self, sku: str) -> Optional[Tuple[int, str]]:
        """Playwright: берём credentials из localStorage и делаем page.request."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("playwright не установлен")
            return None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                ctx_kwargs = {
                    "user_agent": WB_HEADERS["User-Agent"],
                    "viewport": {"width": 1920, "height": 1080},
                    "locale": "ru-RU",
                    "timezone_id": "Europe/Moscow",
                }
                if self.proxy:
                    ctx_kwargs["proxy"] = {"server": self.proxy}

                context = browser.new_context(**ctx_kwargs)
                page = context.new_page()
                page.add_init_script(self.STEALTH_INIT_SCRIPT)

                logger.info("Playwright: открываю wildberries.ru...")
                try:
                    page.goto(
                        "https://www.wildberries.ru/",
                        wait_until="domcontentloaded",
                        timeout=40000,
                    )
                    page.wait_for_timeout(3000)
                except Exception as e:
                    logger.warning(f"Playwright: goto упал: {e}")

                # Извлекаем credentials
                creds = page.evaluate(self._JS_COLLECT_CREDS)
                if isinstance(creds, str):
                    creds = json.loads(creds)

                token = creds.get("token")
                deviceid = creds.get("deviceid")
                xinfo = creds.get("xinfo")

                if not (token and deviceid and xinfo):
                    logger.error(
                        f"Playwright: не хватает credentials "
                        f"(token={bool(token)}, deviceid={bool(deviceid)}, xinfo={bool(xinfo)}). "
                        "Залогиньтесь на wildberries.ru."
                    )
                    browser.close()
                    return None

                api_url = self._build_api_url(sku, xinfo)
                referer = f"https://www.wildberries.ru/catalog/{sku}/detail.aspx"

                logger.info("Playwright: page.request.get к API...")
                try:
                    resp = page.request.get(
                        api_url,
                        headers={
                            "accept": "*/*",
                            "accept-language": "ru-RU,ru;q=0.9",
                            "authorization": f"Bearer {token}",
                            "deviceid": deviceid,
                            "referer": referer,
                            "x-requested-with": "XMLHttpRequest",
                            "x-spa-version": "14.26.4",
                        },
                        timeout=30000,
                    )
                    status = resp.status
                    text = resp.text()
                except Exception as e:
                    err = str(e)
                    m = re.search(r'←\s*(\d{3})\s', err)
                    status = int(m.group(1)) if m else 0
                    text = err

                browser.close()
                logger.info(f"Playwright fetch: status={status}, body_len={len(text)}")
                if status == 0:
                    logger.error(f"Playwright fetch FAILED. Raw: {text[:200]!r}")
                return status, text

        except Exception as e:
            logger.exception(f"Playwright ошибка: {e}")
            return None

    def _playwright_fetch(self, sku: str) -> Optional[Tuple[int, str]]:
        if not self.use_playwright:
            return None
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(self._playwright_fetch_sync, sku).result(timeout=120)
        except concurrent.futures.TimeoutError:
            logger.error("Playwright: таймаут 120 сек")
            return None
        except Exception as e:
            logger.exception(f"Playwright в потоке упал: {e}")
            return None

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

        data = None
        tier = None

        # === Tier 1: CDP с авторизацией ===
        if self._cdp_available():
            logger.info("Tier 1 (CDP + auth): пробую...")
            result = self._cdp_call(None, sku)  # api_url формируется внутри
            if result:
                status_code, body, image_url = result
                if status_code == 200:
                    try:
                        data = json.loads(body)
                        tier = "cdp"
                        self._last_image_url = image_url
                        logger.info("Tier 1 (CDP + auth): OK ✨")
                    except json.JSONDecodeError:
                        logger.warning("CDP: non-JSON response")
                else:
                    logger.warning(f"CDP: HTTP {status_code}")
        else:
            logger.info(
                f"Tier 1 (CDP): недоступен на {self.cdp_host}:{self.cdp_port}"
            )

        # === Tier 2: Playwright fallback ===
        if data is None and self.use_playwright:
            logger.info("Tier 2 (Playwright + auth): пробую...")
            pw_result = self._playwright_fetch(sku)
            if pw_result:
                status_code, body = pw_result
                if status_code == 200:
                    try:
                        data = json.loads(body)
                        tier = "playwright"
                        logger.info("Tier 2 (Playwright + auth): OK ✨")
                    except json.JSONDecodeError:
                        logger.warning("Playwright: non-JSON response")
                else:
                    logger.warning(f"Playwright: HTTP {status_code}")

        # === Всё упало ===
        if data is None:
            return ProductData(
                url=url, marketplace=self.marketplace, sku=sku, success=False,
                error=(
                    "WB недоступен. Проверьте: "
                    "1) Chrome CDP запущен; "
                    "2) вы залогинены на wildberries.ru в этом браузере; "
                    "3) вкладка wildberries.ru открыта."
                ),
            )

        products = (
            data.get("products")
            or (data.get("data") or {}).get("products")
            or []
        )
        if not products:
            return ProductData(
                url=url, marketplace=self.marketplace, success=False,
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
            tier_used=tier,
        )

    def close(self):
        self.client.close()

