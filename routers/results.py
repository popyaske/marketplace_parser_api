"""API эндпоинты для просмотра результатов."""
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from db import (
    list_products, get_product, delete_product, get_stats,
)

router = APIRouter(prefix="/api/v1/results", tags=["results"])


class ProductOut(BaseModel):
    id: int
    url: str
    marketplace: str
    sku: Optional[str] = None
    title: Optional[str] = None
    price_rub: Optional[float] = None
    price_with_plus: Optional[float] = None
    price_original_rub: Optional[float] = None
    seller: Optional[str] = None
    brand: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    in_stock: Optional[bool] = None
    image_url: Optional[str] = None
    tier_used: Optional[str] = None
    post_text: Optional[str] = None
    success: bool
    error: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ProductsPage(BaseModel):
    items: List[ProductOut]
    total: int
    page: int
    per_page: int
    pages: int


class StatsOut(BaseModel):
    total: int
    by_marketplace: dict
    last_24h: int


@router.get("", response_model=ProductsPage)
async def list_results(
    marketplace: Optional[str] = Query(None, description="Фильтр по маркетплейсу"),
    search: Optional[str] = Query(None, description="Поиск по названию"),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """
    Список результатов парсинга с фильтрами и пагинацией.
    """
    items, total = await list_products(
        marketplace=marketplace,
        search=search,
        min_price=min_price,
        max_price=max_price,
        page=page,
        per_page=per_page,
    )
    pages = (total + per_page - 1) // per_page if total > 0 else 0
    return ProductsPage(
        items=[ProductOut.model_validate(p) for p in items],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/stats", response_model=StatsOut)
async def stats():
    """Статистика по результатам."""
    return StatsOut(**await get_stats())


@router.get("/{product_id}", response_model=ProductOut)
async def get_result(product_id: int):
    """Один результат по id."""
    product = await get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    return ProductOut.model_validate(product)


@router.delete("/{product_id}")
async def delete_result(product_id: int):
    """Удалить результат."""
    ok = await delete_product(product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Не найдено")
    return {"success": True}