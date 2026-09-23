"""
Парсер Яндекс.Маркета через SSR state + CDP fallback.

Yandex Market НЕ имеет публичного JSON API:
- /api/resolve → 403
- внутренний product endpoint → gRPC
- старый Content API → 502

Работает: страницы полностью server-rendered и содержат widget state JSON
внутри <noframes data-apiary="patch">.

Для датацентровых IP Яндекс отдаёт урезанную страницу (ok_ldjson_only)
или пустой каркас (empty_product_shell). В таких случаях парсер повторяет
запрос через Chrome DevTools Protocol — у браузера есть реальные cookies
и fingerprint, поэтому SSR-стейт приходит полным.

CDP-часть работает так же, как в ozon.py / wildberries.py:
  - websocket-client (sync)
  - переиспользует уже открытую вкладку market.yandex.ru
  - Runtime.evaluate для получения outerHTML

Если Яндекс для текущей сессии отдаёт SPA-версию карточки без SSR-стейта,
парсер вытягивает цены прямо из DOM через JS и встраивает их в HTML
в виде <script id="__cdp_prices__">, откуда их читает parse_card.
"""

import json
import logging
import math
import os
import re
import time
from typing import Any, Optional, Dict
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from core.models import ProductData
from .base import BaseParser


log = logging.getLogger("yandex")


# === Регулярки ===
_PATCH_RE = re.compile(r'<noframes data-apiary="patch">(.*?)</noframes>', re.S)
_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_CDP_PRICES_RE = re.compile(r'<script id="__cdp_prices__">(.*?)</script>', re.S)

EMPTY_RESULT_MARKER = "Попробуйте сформулировать запрос"
_CAPTCHA_MARKERS = ("SmartCaptcha", "/showcaptcha", "checkbox_captcha")
_ORIG_SUFFIX = "/orig"
_PRODUCT_PAGE_ID = "market:product"
_YANDEX_DOMAIN = "market.yandex.ru"

_CDP_MAX_WAIT = 25.0   # сколько ждать появления SSR-стейта карточки
_CDP_POLL = 0.25       # шаг опроса


class ParseStatus:
    OK = "ok"
    OK_LDJSON_ONLY = "ok_ldjson_only"
    EMPTY = "empty"
    NO_PRODUCTS_FOUND = "no_products_found"
    CAPTCHA = "captcha"
    EMPTY_PRODUCT_SHELL = "empty_product_shell"


# === Утилиты ===

def _as_dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _is_empty(v: Any) -> bool:
    return v is None or v == {} or v == [] or v == ""


def _to_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    if n <= 0 or not math.isfinite(n):
        return None
    return round(n, 2)


def _to_int(value: Any) -> Optional[int]:
    n = _to_number(value)
    return int(n) if n is not None else None


def _iter_patches(html: str):
    for m in _PATCH_RE.finditer(html):
        try:
            yield json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue


def _iter_ldjson(html: str):
    for m in _LDJSON_RE.finditer(html):
        try:
            yield json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue


def _first_dict(coll: Any) -> dict:
    if isinstance(coll, dict):
        for v in coll.values():
            if isinstance(v, dict):
                return v
    return {}


def _picture_url(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    base = node.get("baseUrl")
    if isinstance(base, str) and base:
        return base.rstrip("/") + _ORIG_SUFFIX
    ns, gid, name = node.get("namespace"), node.get("groupId"), node.get("imageName")
    if ns and gid and name:
        return f"https://avatars.mds.yandex.net/get-{ns}/{gid}/{name}{_ORIG_SUFFIX}"
    return ""


def _absolute_url(raw: Any, product_id: Any = None, slug: Any = None) -> str:
    if isinstance(raw, str) and raw:
        if raw.startswith("http"):
            return raw
        if raw.startswith("//"):
            return f"https:{raw}"
        return f"https://market.yandex.ru{raw}"
    if slug and product_id:
        return f"https://market.yandex.ru/product--{slug}/{product_id}"
    if product_id:
        return f"https://market.yandex.ru/product/{product_id}"
    return ""


def _amount_int(node: Any) -> Optional[float]:
    if not isinstance(node, dict):
        return None
    amount = node.get("amount")
    if not isinstance(amount, dict):
        return None
    return _to_number(amount.get("intPart"))


def _cart_price(cart_button: Any) -> Optional[float]:
    if not isinstance(cart_button, dict):
        return None
    price = cart_button.get("price")
    if not isinstance(price, dict):
        return None
    return _to_number(price.get("valueFmt"))


def _additional_price(baobab: Any, price_type: str) -> Optional[float]:
    if not isinstance(baobab, dict):
        return None
    for entry in _as_list(baobab.get("additionalPrices")):
        if isinstance(entry, dict) and entry.get("priceType") == price_type:
            return _to_number(entry.get("priceValue"))
    return None


# === Парсинг поиска ===

def find_search_collections(html: str) -> Optional[dict]:
    best, best_score = None, 0
    for patch in _iter_patches(html):
        widgets = patch.get("widgets")
        if not isinstance(widgets, dict):
            continue
        for widget_value in widgets.values():
            if not isinstance(widget_value, dict):
                continue
            for path_value in widget_value.values():
                if not isinstance(path_value, dict):
                    continue
                options = path_value.get("options")
                if not isinstance(options, dict):
                    continue
                collections = options.get("collections")
                if not isinstance(collections, dict):
                    continue
                score = (
                    len(_as_dict(collections.get("productSnippet")))
                    + len(_as_dict(collections.get("offer")))
                    + len(_as_dict(collections.get("product")))
                )
                if score > best_score:
                    best_score = score
                    best = collections
    return best


def merge_card_collections(html: str) -> dict:
    merged: Dict[str, Any] = {}
    for patch in _iter_patches(html):
        collections = patch.get("collections")
        if not isinstance(collections, dict):
            continue
        for name, value in collections.items():
            if not isinstance(value, dict):
                merged.setdefault(name, value)
                continue
            slot = merged.setdefault(name, {})
            if not isinstance(slot, dict):
                continue
            for entity_id, entity in value.items():
                if _is_empty(entity) and not _is_empty(slot.get(entity_id)):
                    continue
                slot[entity_id] = entity
    return merged


def ldjson_item_list(html: str) -> list:
    items: list = []
    for obj in _iter_ldjson(html):
        if obj.get("@type") != "ItemList":
            continue
        for el in obj.get("itemListElement") or []:
            item = el.get("item") if isinstance(el, dict) else None
            if not isinstance(item, dict):
                continue
            offers = _as_dict(item.get("offers"))
            rating = _as_dict(item.get("aggregateRating"))
            url = str(item.get("url") or "")
            items.append({
                "product_id": url.rstrip("/").split("/")[-1] if url else None,
                "title": item.get("name"),
                "url": url,
                "image": item.get("image"),
                "sku_id": item.get("sku"),
                "price_with_plus": _to_number(offers.get("price")),
                "rating": _to_number(rating.get("ratingValue")),
                "rating_count": _to_int(rating.get("ratingCount")),
            })
    return items


def parse_search(html: str) -> dict:
    if any(m in html for m in _CAPTCHA_MARKERS):
        return {"status": ParseStatus.CAPTCHA, "items": [], "total": None}

    collections = find_search_collections(html)
    source = collections if collections is not None else merge_card_collections(html)
    visible = _first_dict(source.get("visibleSearchResult"))

    result: Dict[str, Any] = {
        "status": ParseStatus.OK,
        "query": visible.get("text") or "",
        "total": _to_int(visible.get("total")),
        "page": _to_int(visible.get("page")),
        "page_count": _to_int(visible.get("pageCount")),
        "has_next_page": bool(visible.get("hasNextPage")),
        "items": [],
    }

    if not collections:
        fallback = ldjson_item_list(html)
        if fallback:
            result["items"] = fallback
            result["status"] = ParseStatus.OK_LDJSON_ONLY
            return result
        result["status"] = ParseStatus.EMPTY if EMPTY_RESULT_MARKER in html else ParseStatus.NO_PRODUCTS_FOUND
        return result

    offers = _as_dict(collections.get("offer"))
    products = _as_dict(collections.get("product"))
    snippets = _as_dict(collections.get("productSnippet"))
    shops = _as_dict(collections.get("shop"))
    vendors = _as_dict(collections.get("vendor"))
    show_places = _as_dict(collections.get("productShowPlace"))
    offer_places = _as_dict(collections.get("offerShowPlace"))
    visible_entities = _as_dict(collections.get("visibleEntity"))
    search_results = _as_dict(collections.get("searchResult"))

    ordered_ids = [
        eid for sr in search_results.values()
        if isinstance(sr, dict)
        for eid in (sr.get("visibleEntityIds") or [])
        if isinstance(eid, str) and eid.startswith("showPlace_")
    ]
    if ordered_ids:
        places = [
            show_places[visible_entities[eid]["productShowPlaceId"]]
            for eid in ordered_ids
            if isinstance(visible_entities.get(eid), dict)
            and visible_entities[eid].get("productShowPlaceId") in show_places
        ]
    else:
        places = [p for p in show_places.values() if isinstance(p, dict)]

    seen = set()
    for place in places:
        if not isinstance(place, dict):
            continue
        product_id = place.get("productId")
        snippet_id = place.get("productSnippetId")
        key = snippet_id or (product_id, place.get("id"))
        if key in seen:
            continue
        seen.add(key)

        snippet = snippets.get(snippet_id) if snippet_id else None
        payload = _as_dict(snippet.get("productPayload")) if isinstance(snippet, dict) else {}
        baobab = _as_dict(snippet.get("baobabPayload")) if isinstance(snippet, dict) else {}

        offer: dict = {}
        offer_place_id = place.get("defaultOfferShowPlaceId")
        if offer_place_id and isinstance(offer_places.get(offer_place_id), dict):
            offer_candidate = offers.get(offer_places[offer_place_id].get("offerId"))
            offer = offer_candidate if isinstance(offer_candidate, dict) else {}
        if not offer and isinstance(snippet_id, str) and snippet_id.startswith("prime-"):
            offer_candidate = offers.get(snippet_id[len("prime-"):])
            offer = offer_candidate if isinstance(offer_candidate, dict) else {}

        product = products.get(str(product_id)) if product_id is not None else None
        product = product if isinstance(product, dict) else {}

        price_node = _as_dict(payload.get("price"))
        cart_price = _cart_price(payload.get("cartButton"))
        with_discount = _additional_price(baobab, "withDiscount")
        offer_price = _to_number((_as_dict(offer.get("price"))).get("value"))
        price_rub = next((p for p in (cart_price, with_discount, offer_price) if p is not None), None)

        price_with_plus = _amount_int(price_node.get("actualPrice"))
        if price_with_plus is None:
            price_with_plus = _additional_price(baobab, "yaBank")

        price_old = _amount_int(price_node.get("oldPrice"))
        if price_old is None:
            price_old = _amount_int(price_node.get("initialPrice"))
        if price_old is None:
            baobab_price = _to_number(baobab.get("price"))
            if baobab_price is not None and price_rub is not None and baobab_price > price_rub:
                price_old = baobab_price

        rating_node = _as_dict(payload.get("rating"))
        delivery = _as_dict(offer.get("delivery"))

        vendor_id = offer.get("vendorId")
        supplier_id = offer.get("supplierId")
        vendor = vendors.get(str(vendor_id)) if vendor_id is not None else None
        shop = shops.get(str(supplier_id)) if supplier_id is not None else None

        titles = _as_dict(product.get("titles"))
        offer_titles = _as_dict(offer.get("titles"))
        title_node = _as_dict(payload.get("title"))
        gallery = _as_dict(payload.get("gallery"))
        media = _as_list(gallery.get("mediaItems"))

        result["items"].append({
            "product_id": str(product_id) if product_id is not None else None,
            "sku_id": offer.get("skuId"),
            "title": title_node.get("value") or titles.get("raw") or offer_titles.get("raw") or "",
            "brand": (vendor or {}).get("name") or "",
            "seller": (shop or {}).get("name") or "",
            "price_rub": price_rub,
            "price_with_plus": price_with_plus,
            "price_old_rub": price_old,
            "currency": (_as_dict(offer.get("price"))).get("currency") or "RUR",
            "rating": _to_number(rating_node.get("ratingValue")),
            "rating_count": _to_int(rating_node.get("ratingCount")),
            "in_stock": delivery.get("inStock") if isinstance(delivery.get("inStock"), bool) else None,
            "is_express": bool(delivery.get("isExpress")),
            "url": _absolute_url((_as_dict(place.get("urls"))).get("direct"), product_id, product.get("slug")),
            "image": _picture_url((media[0] or {}).get("picture") if media else None),
        })

    if not result["items"]:
        result["status"] = ParseStatus.EMPTY if EMPTY_RESULT_MARKER in html else ParseStatus.NO_PRODUCTS_FOUND
    return result


# === Парсинг карточки ===

def ldjson_product(html: str) -> dict:
    for obj in _iter_ldjson(html):
        if obj.get("@type") != "Product":
            continue
        offers = _as_dict(obj.get("offers"))
        return {
            "title": obj.get("name"),
            "brand": obj.get("brand"),
            "image": obj.get("image"),
            "description": obj.get("description"),
            "sku": obj.get("sku"),
            "price": offers.get("price"),
            "currency": offers.get("priceCurrency"),
            "url": obj.get("url"),
        }
    return {}


def _extract_cdp_prices(html: str) -> dict:
    """Читает синтетический тег <script id="__cdp_prices__">, добавленный CDP."""
    m = _CDP_PRICES_RE.search(html)
    if not m:
        return {}
    try:
        return json.loads(m.group(1)) or {}
    except Exception:
        return {}


def parse_card(html: str) -> dict:
    if any(m in html for m in _CAPTCHA_MARKERS):
        return {"status": ParseStatus.CAPTCHA, "reviews": []}

    merged = merge_card_collections(html)

    # --- Share-ссылка (кнопка «Поделиться») ---
    share_url = ""
    share_coll = _as_dict(merged.get("share"))
    for entry in share_coll.values():
        if isinstance(entry, dict) and entry.get("link"):
            share_url = str(entry["link"])
            break

    fallback = ldjson_product(html)
    cdp_dom = _extract_cdp_prices(html)

    all_prices = _first_dict(merged.get("allPrices"))
    baobab = _as_dict(all_prices.get("baobab"))
    title_node = _first_dict(merged.get("titleV2"))
    vendor_node = _as_dict(title_node.get("vendor"))

    product_id = baobab.get("productId")
    if not product_id and fallback.get("url"):
        m = re.search(r"/(\d+)(?:\?|$)", str(fallback["url"]))
        if m:
            product_id = m.group(1)

    price_node = _first_dict(merged.get("price"))
    main_price = _as_dict(price_node.get("mainPrice"))
    price_with_plus = (
        _to_number((_as_dict(main_price.get("price"))).get("value"))
        or _to_number(fallback.get("price"))
    )

    price_regular = price_before_discount = None
    for old in price_node.get("oldPrices") or []:
        if not isinstance(old, dict):
            continue
        v = _to_number((_as_dict(old.get("price"))).get("value"))
        if old.get("type") == "regular":
            price_regular = v
        elif old.get("type") == "withoutDiscount":
            price_before_discount = v

    # Фолбэк 1: regular = withoutDiscount
    if price_regular is None and price_before_discount is not None:
        price_regular = price_before_discount

    # Фолбэк 2: baobab.price > price_with_plus → это regular
    if price_regular is None:
        baobab_price = _to_number(baobab.get("price"))
        if baobab_price is not None:
            if price_with_plus is None or baobab_price > price_with_plus:
                price_regular = baobab_price

    # Фолбэк 3: любая old price больше price_with_plus
    if price_regular is None:
        for old in price_node.get("oldPrices") or []:
            if not isinstance(old, dict):
                continue
            v = _to_number((_as_dict(old.get("price"))).get("value"))
            if v is not None and v > (price_with_plus or 0):
                price_regular = v
                break

    # Фолбэк 4: цены, вытянутые из DOM через CDP
    if not all_prices and cdp_dom:
        dom_prices = cdp_dom.get("prices") or []
        if dom_prices:
            try:
                dom_prices = sorted(float(p) for p in dom_prices if p)
            except Exception:
                dom_prices = []
        if dom_prices:
            if price_with_plus is None:
                price_with_plus = dom_prices[0]
            if price_regular is None:
                price_regular = dom_prices[-1] if len(dom_prices) > 1 else dom_prices[0]
        if not title_node.get("title") and cdp_dom.get("title"):
            title_node = {**(title_node or {}), "title": cdp_dom["title"]}

    rating_node = _first_dict(merged.get("productServiceSnippets"))
    reviews_coll = _as_dict(merged.get("reviews"))
    rating_stats = _as_dict(reviews_coll.get("ratingStats"))
    business_stats = _first_dict(merged.get("businessRatingStats"))
    shop_info = _first_dict(merged.get("shopInfo"))

    rating = _to_number(rating_node.get("ratingValue")) or _to_number(rating_stats.get("ratingValue"))
    rating_count = _to_int(rating_node.get("ratingCount")) or _to_int(rating_stats.get("ratingCount"))

    result = {
        "status": ParseStatus.OK,
        "product_id": str(product_id) if product_id else "",
        "sku_id": str(baobab.get("skuId") or fallback.get("sku") or ""),
        "title": str(title_node.get("title") or fallback.get("title") or ""),
        "brand": str(vendor_node.get("name") or fallback.get("brand") or ""),
        "seller": str(shop_info.get("shopName") or shop_info.get("name") or business_stats.get("shopName") or ""),
        "price_rub": price_regular if price_regular is not None else price_with_plus,
        "price_with_plus": price_with_plus,
        "price_before_discount_rub": price_before_discount,
        "currency": (_as_dict(main_price.get("price"))).get("currency") or fallback.get("currency") or "RUR",
        "rating": rating,
        "rating_count": rating_count,
        "image": str(fallback.get("image") or ""),
        "share_url": share_url,
    }

    if (
        not result["title"]
        and result["price_rub"] is None
        and result["price_with_plus"] is None
    ):
        result["status"] = ParseStatus.EMPTY_PRODUCT_SHELL
    return result


# === Основной класс ===

class YandexParser(BaseParser):
    """Парсер Яндекс.Маркета через SSR state с фолбэком на Chrome CDP."""

    marketplace = "yandex"
    base_url = "https://market.yandex.ru"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://market.yandex.ru/",
        "Upgrade-Insecure-Requests": "1",
    }

    def __init__(
        self,
        timeout: float = 40.0,
        min_gap: float = 1.5,
        proxy: Optional[str] = None,
        cdp_host: Optional[str] = None,
        cdp_port: Optional[int] = None,
    ):
        self.timeout = timeout
        self.min_gap = min_gap
        self._last_request_at = 0.0
        self.client = httpx.Client(
            timeout=timeout,
            headers=self.HEADERS,
            follow_redirects=True,
            proxy=proxy,
        )

        # CDP — как у Ozon/WB
        self.cdp_host = cdp_host or os.getenv("CDP_HOST") or "127.0.0.1"
        env_port = os.getenv("CDP_PORT", "9222")
        self.cdp_port = cdp_port or (int(env_port) if env_port.isdigit() else 9222)

    # ---------- HTTP (SSR) ----------

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_gap:
            time.sleep(self.min_gap - elapsed)
        self._last_request_at = time.monotonic()

    def extract_sku(self, url: str) -> Optional[str]:
        m = re.search(r'/product(?:--[^/]+)?/(\d+)', url)
        if m:
            return m.group(1)
        m = re.search(r'/card/[^/]+/(\d+)', url)
        return m.group(1) if m else None

    def _is_search_url(self, url: str) -> bool:
        return "/search" in url

    def _fetch_html(self, url: str, retries: int = 2) -> str:
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                self._throttle()
                resp = self.client.get(url)
                if resp.status_code in (302, 502, 503, 504) and not resp.text.strip():
                    last_err = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code} with empty body",
                        request=resp.request, response=resp,
                    )
                    time.sleep(1.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                if not resp.text.strip():
                    raise httpx.TransportError("empty response body")
                return resp.text
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise
        if last_err:
            raise last_err
        raise RuntimeError("unreachable")

    # ---------- CDP ----------

    def _cdp_available(self) -> bool:
        try:
            r = httpx.get(
                f"http://{self.cdp_host}:{self.cdp_port}/json/version",
                timeout=2.0,
            )
            return r.status_code == 200
        except Exception:
            return False

    def _cdp_fetch_html(self, url: str) -> Optional[str]:
        """
        Открывает/переиспользует вкладку market.yandex.ru через Chrome CDP,
        жёстко перезагружает её на целевой URL, ждёт появления SSR-стейта
        карточки (или, если SSR нет — вытягивает цены из DOM JS-ом)
        и возвращает outerHTML.
        """
        try:
            from websocket import create_connection
        except ImportError:
            log.error("websocket-client не установлен: pip install websocket-client")
            return None

        ws = None
        try:
            # 1. Ищем существующую вкладку market.yandex.ru
            tabs_resp = httpx.get(
                f"http://{self.cdp_host}:{self.cdp_port}/json/list",
                timeout=5.0,
            )
            if tabs_resp.status_code != 200:
                log.error("CDP /json/list вернул HTTP %s", tabs_resp.status_code)
                return None
            tabs = tabs_resp.json()

            tab = None
            for t in tabs:
                if t.get("type") != "page":
                    continue
                tu = t.get("url") or ""
                if _YANDEX_DOMAIN not in tu:
                    continue
                if any(x in tu for x in ("/showcaptcha", "/captcha", "about:blank")):
                    log.info("CDP: пропускаю вкладку с капчей: %s", tu[:120])
                    continue
                tab = t
                break

            if not tab:
                log.info("CDP: вкладки market.yandex.ru нет, создаю новую")
                new_tab = httpx.put(
                    f"http://{self.cdp_host}:{self.cdp_port}/json/new?"
                    f"{quote(url, safe='')}",
                    timeout=10.0,
                )
                if new_tab.status_code != 200:
                    log.error("CDP /json/new вернул HTTP %s", new_tab.status_code)
                    return None
                tab = new_tab.json()
                log.info("CDP: жду загрузку новой вкладки (6 сек)")
                time.sleep(6)

            ws_url = tab.get("webSocketDebuggerUrl")
            if not ws_url:
                return None
            log.info("CDP: вкладка id=%s, url=%s",
                     tab.get("id"), (tab.get("url") or "")[:100])

            # 2. Подключаемся
            ws = create_connection(ws_url, timeout=self.timeout)
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

            def evaluate(expression: str, await_promise: bool = False,
                         timeout_s: float = 30.0) -> Any:
                params: dict = {"expression": expression, "returnByValue": True}
                if await_promise:
                    params["awaitPromise"] = True
                res = send("Runtime.evaluate", params, timeout_s=timeout_s)
                return (res.get("result") or {}).get("result", {}).get("value")

            send("Runtime.enable")
            send("Page.enable")

            # 3. Форсируем hard reload: about:blank → disabled cache → navigate
            try:
                send("Page.navigate", {"url": "about:blank"}, timeout_s=5.0)
                t0 = time.monotonic()
                blank_ok = False
                while time.monotonic() - t0 < 5.0:
                    h = evaluate("document.location.href", timeout_s=2.0) or ""
                    if h == "about:blank":
                        blank_ok = True
                        break
                    time.sleep(0.2)
                log.info("CDP: about:blank %s", "ok" if blank_ok else "ТАЙМАУТ")
            except Exception as e:
                log.warning("CDP: about:blank error: %s", e)

            try:
                send("Network.enable", timeout_s=5.0)
                send("Network.setCacheDisabled", {"cacheDisabled": True}, timeout_s=5.0)
            except Exception:
                pass

            send("Page.navigate", {"url": url}, timeout_s=10.0)

            # 4. Ждём появления признаков карточки:
            #    readyState=complete И patches>0 И есть allPrices/baobab/withDiscount
            start = time.monotonic()
            ready = False
            last_href = ""
            last_patches = 0
            last_has_prices = False
            while time.monotonic() - start < _CDP_MAX_WAIT:
                info_raw = evaluate(
                    "JSON.stringify({"
                    "  href: document.location.href,"
                    "  ready: document.readyState,"
                    "  patches: document.querySelectorAll("
                    "    'noframes[data-apiary=\"patch\"]'"
                    "  ).length,"
                    "  has_prices: (document.documentElement.outerHTML.includes('allPrices')"
                    "             || document.documentElement.outerHTML.includes('baobab')"
                    "             || document.documentElement.outerHTML.includes('\"priceType\":\"withDiscount\"'))"
                    "})",
                    timeout_s=5.0,
                )
                try:
                    info = json.loads(info_raw) if info_raw else {}
                except Exception:
                    info = {}
                href = info.get("href") or ""
                last_href = href
                last_patches = info.get("patches") or 0
                last_has_prices = bool(info.get("has_prices"))

                if (
                    info.get("ready") == "complete"
                    and last_patches > 0
                    and last_has_prices
                ):
                    ready = True
                    break
                time.sleep(_CDP_POLL)

            if not ready:
                title = evaluate("document.title", timeout_s=3.0) or ""
                if "captcha" in title.lower() or "Подтвердите" in title:
                    log.warning("CDP: Яндекс показал капчу (title=%r)", title[:100])
                    return None
                log.warning(
                    "CDP: карточка не догрузилась за %.0fs "
                    "(href=%s, patches=%d, has_prices=%s)",
                    _CDP_MAX_WAIT, last_href[:120], last_patches, last_has_prices,
                )
            else:
                log.info("CDP: карточка готова (patches=%d, has_prices=True)", last_patches)

            # 5. Даём SPA дописать цены в DOM
            time.sleep(1.5)

            # 6. Забираем outerHTML
            html = evaluate(
                "document.documentElement.outerHTML",
                timeout_s=15.0,
            )
            if not html:
                log.error("CDP: пустой outerHTML")
                return None

            # 7. Если SSR-карточки нет — вытягиваем цены прямо из DOM
            #    и встраиваем синтетический тег, который читает parse_card.
            if "allPrices" not in html and "baobab" not in html:
                js_extract = r"""
                (() => {
                    const text = (sel) => {
                        const el = document.querySelector(sel);
                        return el ? el.textContent.trim() : null;
                    };
                    const toNum = (s) => {
                        if (!s) return null;
                        const digits = String(s).replace(/[^\d]/g, '');
                        const n = digits ? parseInt(digits, 10) : null;
                        return n && n > 0 ? n : null;
                    };
                    const nodes = [...document.querySelectorAll('span, div')]
                        .filter(el => el.children.length === 0)
                        .map(el => el.textContent.trim())
                        .filter(t => /^\s*\d[\d\s\u00a0]*\s*₽\s*$/.test(t))
                        .map(toNum)
                        .filter(n => n && n > 0);
                    const uniq = [...new Set(nodes)].sort((a,b) => a - b);
                    const title = text('h1');
                    return JSON.stringify({ title: title, prices: uniq });
                })()
                """
                extra = evaluate(js_extract, timeout_s=5.0)
                if extra:
                    try:
                        html = html.replace(
                            "</body>",
                            f'<script id="__cdp_prices__">{extra}</script></body>',
                        )
                        log.info("CDP: цены вытянуты из DOM: %s", str(extra)[:200])
                    except Exception as e:
                        log.warning("CDP: не удалось внедрить __cdp_prices__: %s", e)

            log.info("CDP: получен HTML size=%d, patches=%d",
                     len(html), html.count('data-apiary="patch"'))
            return html

        except Exception as e:
            log.exception("CDP ошибка: %s", e)
            return None
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    # ---------- Разбор HTML ----------

    def _build_from_html(self, url: str, html: str, tier: str) -> ProductData:
        """Превратить HTML (SSR или из CDP) в ProductData."""

        if self._is_search_url(url):
            parsed = parse_search(html)
            if parsed["status"] == ParseStatus.CAPTCHA:
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error="SmartCaptcha",
                )
            items = parsed.get("items") or []
            if not items:
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error=f"Ничего не найдено (status={parsed['status']})",
                )

            priced = [i for i in items if i.get("price_rub") is not None]
            item = priced[0] if priced else items[0]

            return ProductData(
                title=item.get("title") or "Не найдено",
                price_rub=item.get("price_rub"),
                price_with_plus=item.get("price_with_plus"),
                price_original_rub=item.get("price_old_rub"),
                url=item.get("url") or url,
                share_url=parsed.get("share_url") or None,
                marketplace=self.marketplace,
                sku=str(item.get("sku_id") or item.get("product_id") or ""),
                seller=item.get("seller") or "",
                brand=item.get("brand") or "",
                rating=item.get("rating"),
                rating_count=item.get("rating_count"),
                in_stock=item.get("in_stock"),
                image_url=item.get("image"),
                success=True,
                tier_used=tier,
            )

        # --- карточка ---
        parsed = parse_card(html)
        if parsed["status"] == ParseStatus.CAPTCHA:
            return ProductData(url=url, marketplace=self.marketplace,
                               success=False, error="SmartCaptcha")
        if parsed["status"] == ParseStatus.EMPTY_PRODUCT_SHELL:
            return ProductData(url=url, marketplace=self.marketplace,
                               success=False,
                               error="empty_product_shell — пустой каркас страницы")
        if not parsed.get("title"):
            return ProductData(url=url, marketplace=self.marketplace,
                               success=False, error="parser_drift — нет title на карточке")

        return ProductData(
            title=parsed.get("title") or "Не найдено",
            price_rub=parsed.get("price_rub"),
            price_with_plus=parsed.get("price_with_plus"),
            price_original_rub=parsed.get("price_before_discount_rub"),
            url=url,
            share_url=parsed.get("share_url") or None,
            marketplace=self.marketplace,
            sku=str(parsed.get("sku_id") or parsed.get("product_id") or ""),
            brand=parsed.get("brand") or "",
            seller=parsed.get("seller") or "",
            rating=parsed.get("rating"),
            rating_count=parsed.get("rating_count"),
            image_url=parsed.get("image"),
            success=True,
            tier_used=tier,
        )

    # ---------- Публичный fetch ----------

    @staticmethod
    def _needs_cdp(res: ProductData) -> bool:
        if not res.success:
            return True
        if res.price_rub is None:
            return True
        return False

    def _normalize_url(self, url: str) -> str:
        """Срезаем query — Яндекс на URL с utm/sponsored/cpc/do-waremd5
        часто отдаёт degraded-страницу."""
        p = urlsplit(url)
        if p.query:
            clean = urlunsplit((p.scheme, p.netloc, p.path, "", ""))
            log.info("yandex: чищу query: %s → %s", url, clean)
            return clean
        return url

    def fetch(self, url: str) -> ProductData:
        url = self._normalize_url(url)

        # 1. SSR через httpx
        ssr_result: ProductData
        try:
            html = self._fetch_html(url)
            ssr_result = self._build_from_html(url, html, tier="ssr")
        except httpx.HTTPStatusError as e:
            ssr_result = ProductData(url=url, marketplace=self.marketplace,
                                     success=False,
                                     error=f"HTTP {e.response.status_code}")
        except httpx.TransportError as e:
            ssr_result = ProductData(url=url, marketplace=self.marketplace,
                                     success=False, error=f"transport_down: {e}")
        except Exception as e:
            ssr_result = ProductData(url=url, marketplace=self.marketplace,
                                     success=False,
                                     error=f"{type(e).__name__}: {e}")

        if not self._needs_cdp(ssr_result):
            return ssr_result

        log.info(
            "yandex: SSR не дал данных (%s) → пробую CDP",
            ssr_result.error or f"price_rub={ssr_result.price_rub}",
        )

        # 2. CDP-фолбэк
        if not self._cdp_available():
            log.warning("yandex: CDP недоступен на %s:%s — отдаю SSR-результат",
                        self.cdp_host, self.cdp_port)
            return ssr_result

        cdp_html = self._cdp_fetch_html(url)
        if not cdp_html:
            log.warning("yandex: CDP не вернул HTML — отдаю SSR-результат")
            return ssr_result

        try:
            cdp_result = self._build_from_html(url, cdp_html, tier="cdp")
        except Exception as e:
            log.warning("yandex: разбор CDP-HTML упал: %s", e)
            return ssr_result

        if cdp_result.success:
            return cdp_result

        log.warning("yandex: CDP тоже не помог: %s", cdp_result.error)
        return cdp_result if cdp_result.title else ssr_result

    def close(self):
        self.client.close()