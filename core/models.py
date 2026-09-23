"""Модели данных"""
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class MarketplaceType(str, Enum):
    YANDEX = "yandex"
    OZON = "ozon"
    WILDBERRIES = "wildberries"
    ALIEXPRESS = "aliexpress"
    UNKNOWN = "unknown"


@dataclass
class ProductData:
    """Данные о товаре"""
    title: str = "Не найдено"
    # Цены
    price_rub: Optional[float] = None           # основная цена
    price_with_plus: Optional[float] = None     # цена с подпиской (Yandex Plus, Ozon Card)
    price_original_rub: Optional[float] = None  # зачёркнутая цена
    # Метаданные
    url: str = "Не найдено"
    share_url: Optional[str] = None
    marketplace: str = "unknown"
    sku: Optional[str] = None
    seller: str = ""
    brand: str = ""
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    in_stock: Optional[bool] = None
    image_url: Optional[str] = None
    # Статус
    success: bool = True
    error: Optional[str] = None
    tier_used: str = ""   # curl_cffi, cdp, ssr, api