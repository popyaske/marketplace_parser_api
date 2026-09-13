"""Парсер AliExpress через API (заглушка)"""
import re
from typing import Optional, Dict, Any

from .base import BaseParser
from core.models import ProductData


class AliexpressParser(BaseParser):
    marketplace = "aliexpress"
    base_url = "https://aliexpress.ru"

    def extract_sku(self, url: str) -> Optional[str]:
        # https://aliexpress.ru/item/1005001234567890.html
        match = re.search(r'/item/(\d+)', url)
        return match.group(1) if match else None

    def _build_api_url(self, sku: str) -> str:
        return f"https://aliexpress.ru/aeglodetailweb/api/detail/{sku}"

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