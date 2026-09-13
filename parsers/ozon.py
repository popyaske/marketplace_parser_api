"""Парсер Ozon через API (заглушка)"""
import re
from typing import Optional, Dict, Any

from .base import BaseParser
from core.models import ProductData


class OzonParser(BaseParser):
    marketplace = "ozon"
    base_url = "https://www.ozon.ru"

    def extract_sku(self, url: str) -> Optional[str]:
        # https://www.ozon.ru/product/nazvanie-1234567890/
        match = re.search(r'/product/[^/]*?(\d{6,})', url)
        return match.group(1) if match else None

    def _build_api_url(self, sku: str) -> str:
        # Неофициальный endpoint Ozon
        return f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{sku}"

    def _parse_api_response(self, data: Dict[str, Any], url: str) -> ProductData:
        return ProductData(
            title=data.get("title", "Не найдено"),
            discount_price=str(data.get("price", "Не найдено")),
            old_price=str(data.get("oldPrice", "Не найдено")),
            url=url,
            marketplace=self.marketplace,
            sku=str(data.get("sku", "")),
            success=True,
        )