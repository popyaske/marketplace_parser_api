"""Парсер Wildberries через API (заглушка)"""
import re
from typing import Optional, Dict, Any

from .base import BaseParser
from core.models import ProductData


class WildberriesParser(BaseParser):
    marketplace = "wildberries"
    base_url = "https://www.wildberries.ru"

    def extract_sku(self, url: str) -> Optional[str]:
        # https://www.wildberries.ru/catalog/12345678/detail.aspx
        match = re.search(r'/catalog/(\d+)', url)
        return match.group(1) if match else None

    def _build_api_url(self, sku: str) -> str:
        # Публичный JSON-эндпоинт WB (card.wb.ru)
        return f"https://card.wb.ru/cards/v1/detail?nm={sku}"

    def _parse_api_response(self, data: Dict[str, Any], url: str) -> ProductData:
        products = data.get("data", {}).get("products", [])
        if not products:
            return ProductData(
                url=url, marketplace=self.marketplace,
                success=False, error="Товар не найден",
            )
        p = products[0]
        return ProductData(
            title=p.get("name", "Не найдено"),
            discount_price=str(p.get("salePriceU", 0) // 100),
            old_price=str(p.get("priceU", 0) // 100),
            url=url,
            marketplace=self.marketplace,
            sku=str(p.get("id", sku)),
            success=True,
        )