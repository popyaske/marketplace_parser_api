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
    discount_price: str = "Не найдено"
    old_price: str = "Не найдено"
    url: str = "Не найдено"
    marketplace: str = "unknown"
    sku: Optional[str] = None
    image_url: Optional[str] = None
    success: bool = True
    error: Optional[str] = None