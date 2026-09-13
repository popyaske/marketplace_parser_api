"""Парсер Яндекс.Маркет через API (заглушка)"""
import re
from typing import Optional, Dict, Any

from .base import BaseParser
from core.models import ProductData


class YandexParser(BaseParser):
    marketplace = "yandex"
    base_url = "https://market.yandex.ru"

    def extract_sku(self, url: str) -> Optional[str]:
        # https://market.yandex.ru/product--nazvanie/1234567890
        match = re.search(r'/product[^/]*/(\d+)', url)
        return match.group(1) if match else None

    def _build_api_url(self, sku: str) -> str:
        # Реальный endpoint Яндекса (неофициальный)
        return f"https://market.yandex.ru/api/products/{sku}"

    def _parse_api_response(self, data: Dict[str, Any], url: str) -> ProductData:
        # Здесь разбираем JSON от API
        return ProductData(
            title=data.get("title", "Не найдено"),
            discount_price=str(data.get("price", {}).get("value", "Не найдено")),
            old_price=str(data.get("oldPrice", {}).get("value", "Не найдено")),
            url=url,
            marketplace=self.marketplace,
            sku=str(data.get("sku", "")),
            success=True,
        )