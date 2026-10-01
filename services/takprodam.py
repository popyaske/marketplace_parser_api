"""
Клиент API Такпродам (https://api.takprodam.ru/v2/publisher).

Документация: раздел «API для паблишера».
Используется для ежедневной публикации товаров в Telegram.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

TAKPRODAM_BASE_URL = "https://api.takprodam.ru/v2/publisher"


class TakProDamClient:
    """Тонкая обёртка над REST API Такпродам."""

    def __init__(self, token: str, timeout: float = 30.0):
        if not token:
            raise ValueError("TAKPRODAM_API_TOKEN не задан")
        self.client = httpx.Client(
            base_url=TAKPRODAM_BASE_URL,
            timeout=timeout,
            headers={
                "Host": "api.takprodam.ru",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )

    # ---------- Get source list ----------
    def get_sources(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"status": status} if status else {}
        r = self.client.get("/source/", params=params)
        r.raise_for_status()
        return r.json().get("items", [])

    # ---------- Get products list with links ----------
    def get_products(
        self,
        source_id: int,
        marketplace: Optional[str] = None,
        category_id: Optional[int] = None,
        favorite: Optional[bool] = None,
        payment_type: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
        subid: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"source_id": source_id, "page": page, "limit": limit}
        if marketplace:
            params["marketplace"] = marketplace
        if category_id is not None:
            params["category_id"] = category_id
        if favorite is not None:
            params["favorite"] = str(favorite).lower()
        if payment_type:
            params["payment_type"] = payment_type
        if subid:
            params["subid"] = subid
        r = self.client.get("/product/", params=params)
        r.raise_for_status()
        return r.json()

    # ---------- Get product categories list ----------
    def get_categories(self, product_category: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"product-category": product_category} if product_category else {}
        r = self.client.get("/product-category/", params=params)
        r.raise_for_status()
        return r.json().get("items", [])

    # ---------- Get promotions list ----------
    def get_promotions(
        self,
        source_id: int,
        marketplace: Optional[str] = None,
        promotion_type: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"source_id": source_id, "page": page, "limit": limit}
        if marketplace:
            params["marketplace"] = marketplace
        if promotion_type:
            params["promotion_type"] = promotion_type
        r = self.client.get("/promotion/", params=params)
        r.raise_for_status()
        return r.json()

    # ---------- Get promotion products ----------
    def get_promotion_products(
        self,
        source_id: int,
        promotion_id: int,
        marketplace: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "source_id": source_id,
            "promotion_id": promotion_id,
            "page": page,
            "limit": limit,
        }
        if marketplace:
            params["marketplace"] = marketplace
        r = self.client.get("/promotion/product/", params=params)
        r.raise_for_status()
        return r.json()

    def close(self):
        self.client.close()