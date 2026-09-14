"""
Парсер Яндекс.Маркета через SSR state.

Yandex Market НЕ имеет публичного JSON API:
- /api/resolve → 403
- внутренний product endpoint → gRPC
- старый Content API → 502

Работает: страницы полностью server-rendered и содержат widget state JSON
внутри <noframes data-apiary="patch">.
"""

import json
import math
import re
import time
from typing import Any, Optional, Dict, List
from urllib.parse import quote

import httpx

from core.models import ProductData
from .base import BaseParser


# === Регулярки ===
_PATCH_RE = re.compile(r'<noframes data-apiary="patch">(.*?)</noframes>', re.S)
_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)

EMPTY_RESULT_MARKER = "Попробуйте сформулировать запрос"
_CAPTCHA_MARKERS = ("SmartCaptcha", "/showcaptcha", "checkbox_captcha")
_ORIG_SUFFIX = "/orig"
_PRODUCT_PAGE_ID = "market:product"


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


def parse_card(html: str) -> dict:
    if any(m in html for m in _CAPTCHA_MARKERS):
        return {"status": ParseStatus.CAPTCHA, "reviews": []}

    merged = merge_card_collections(html)
    fallback = ldjson_product(html)

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
    """Парсер Яндекс.Маркета через SSR state"""

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

    def __init__(self, timeout: float = 40.0, min_gap: float = 1.5, proxy: Optional[str] = None):
        self.timeout = timeout
        self.min_gap = min_gap
        self._last_request_at = 0.0
        self.client = httpx.Client(
            timeout=timeout,
            headers=self.HEADERS,
            follow_redirects=True,
            proxy=proxy,
        )

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_gap:
            time.sleep(self.min_gap - elapsed)
        self._last_request_at = time.monotonic()

    def extract_sku(self, url: str) -> Optional[str]:
        m = re.search(r'/product(?:--[^/]+)?/(\d+)', url)
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

    def fetch(self, url: str) -> ProductData:
        try:
            html = self._fetch_html(url)

            if self._is_search_url(url):
                parsed = parse_search(html)
                if parsed["status"] == ParseStatus.CAPTCHA:
                    return ProductData(
                        url=url, marketplace=self.marketplace, success=False,
                        error="SmartCaptcha — нужно подождать или сменить IP",
                    )
                items = parsed.get("items") or []
                if not items:
                    return ProductData(
                        url=url, marketplace=self.marketplace, success=False,
                        error=f"Ничего не найдено (status={parsed['status']})",
                    )

                # 1. Сначала ищем товар с реальной ценой (price_rub)
                priced = [i for i in items if i.get("price_rub") is not None]
                item = priced[0] if priced else items[0]

                # 2. Fallback: если у всех цена None — берём карточку первого товара
                if not priced and item.get("product_id"):
                    try:
                        card_url = f"https://market.yandex.ru/product/{item['product_id']}"
                        card_html = self._fetch_html(card_url)
                        card = parse_card(card_html)
                        if card.get("status") == ParseStatus.OK:
                            return ProductData(
                                title=card.get("title") or item.get("title") or "Не найдено",
                                price_rub=card.get("price_rub"),
                                price_with_plus=card.get("price_with_plus"),
                                price_original_rub=card.get("price_before_discount_rub"),
                                url=card_url,
                                marketplace=self.marketplace,
                                sku=str(card.get("sku_id") or card.get("product_id") or ""),
                                brand=card.get("brand") or "",
                                seller=card.get("seller") or "",
                                rating=card.get("rating"),
                                rating_count=card.get("rating_count"),
                                image_url=card.get("image"),
                                success=True,
                                tier_used="ssr_card",
                            )
                    except Exception:
                        pass  # Если карточка не загрузилась, возвращаем то, что есть

                # 3. Возвращаем результат поиска (если карточка не помогла)
                return ProductData(
                    title=item.get("title") or "Не найдено",
                    price_rub=item.get("price_rub"),
                    price_with_plus=item.get("price_with_plus"),
                    price_original_rub=item.get("price_old_rub"),
                    url=item.get("url") or url,
                    marketplace=self.marketplace,
                    sku=str(item.get("sku_id") or item.get("product_id") or ""),
                    seller=item.get("seller") or "",
                    brand=item.get("brand") or "",
                    rating=item.get("rating"),
                    rating_count=item.get("rating_count"),
                    in_stock=item.get("in_stock"),
                    image_url=item.get("image"),
                    success=True,
                    tier_used="ssr",
                )

                # Обычный путь — вернуть то, что есть
                return ProductData(
                    title=item.get("title") or "Не найдено",
                    price_rub=item.get("price_rub"),
                    price_with_plus=item.get("price_with_plus"),
                    price_original_rub=item.get("price_old_rub"),
                    url=item.get("url") or url,
                    marketplace=self.marketplace,
                    sku=str(item.get("sku_id") or item.get("product_id") or ""),
                    seller=item.get("seller") or "",
                    brand=item.get("brand") or "",
                    rating=item.get("rating"),
                    rating_count=item.get("rating_count"),
                    in_stock=item.get("in_stock"),
                    image_url=item.get("image"),
                    success=True,
                    tier_used="ssr",
                )

            parsed = parse_card(html)
            if parsed["status"] == ParseStatus.CAPTCHA:
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error="SmartCaptcha",
                )
            if parsed["status"] == ParseStatus.EMPTY_PRODUCT_SHELL:
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error="empty_product_shell — пустой каркас страницы",
                )
            if not parsed.get("title"):
                return ProductData(
                    url=url, marketplace=self.marketplace, success=False,
                    error="parser_drift — нет title на карточке",
                )
            return ProductData(
                title=parsed.get("title") or "Не найдено",
                price_rub=parsed.get("price_rub"),
                price_with_plus=parsed.get("price_with_plus"),
                price_original_rub=parsed.get("price_before_discount_rub"),
                url=url,
                marketplace=self.marketplace,
                sku=str(parsed.get("sku_id") or parsed.get("product_id") or ""),
                brand=parsed.get("brand") or "",
                seller=parsed.get("seller") or "",
                rating=parsed.get("rating"),
                rating_count=parsed.get("rating_count"),
                image_url=parsed.get("image"),
                success=True,
                tier_used="ssr",
            )

        except httpx.HTTPStatusError as e:
            return ProductData(url=url, marketplace=self.marketplace, success=False,
                               error=f"HTTP {e.response.status_code}")
        except httpx.TransportError as e:
            return ProductData(url=url, marketplace=self.marketplace, success=False,
                               error=f"transport_down: {e}")
        except Exception as e:
            return ProductData(url=url, marketplace=self.marketplace, success=False,
                               error=f"{type(e).__name__}: {e}")

    def close(self):
        self.client.close()