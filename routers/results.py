"""API эндпоинты для просмотра результатов."""
from sse_starlette.sse import EventSourceResponse
from db import get_products_since
import asyncio
import json
from datetime import datetime
from typing import Optional, List
import csv
import io

from fastapi.responses import StreamingResponse
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from db import (
    list_products, get_product, delete_product, get_stats, get_price_history
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


@router.get("/stats", response_model=StatsOut)
async def stats():
    """Статистика по результатам."""
    return StatsOut(**await get_stats())


@router.get("/stream")
async def results_stream():
    """SSE: новые товары в реальном времени."""
    async def event_generator():
        last_id = 0
        while True:
            try:
                products = await get_products_since(last_id)
                for p in products:
                    yield {
                        "event": "product",
                        "data": json.dumps(
                            ProductOut.model_validate(p).model_dump(mode="json"),
                            ensure_ascii=False,
                        ),
                    }
                    last_id = max(last_id, p.id)
            except Exception:
                pass
            await asyncio.sleep(3)

    return EventSourceResponse(event_generator())


@router.get("/export/csv")
async def export_csv():
    """Экспорт всех товаров в CSV."""
    items, _ = await list_products(per_page=10000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "marketplace", "sku", "title", "price_rub",
        "price_original_rub", "seller", "rating", "url", "created_at",
    ])
    for p in items:
        writer.writerow([
            p.id, p.marketplace, p.sku, p.title, p.price_rub,
            p.price_original_rub, p.seller, p.rating, p.url, p.created_at,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=products.csv"},
    )


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


@router.get("/{product_id}/history")
async def product_price_history(product_id: int, limit: int = 100):
    """История цен для товара."""
    history = await get_price_history(product_id, limit)
    return {
        "product_id": product_id,
        "history": [
            {
                "price_rub": h.price_rub,
                "price_original_rub": h.price_original_rub,
                "in_stock": h.in_stock,
                "created_at": h.created_at,
            }
            for h in history
        ],
    }