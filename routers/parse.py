"""FastAPI роуты"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from services.marketplace import MarketplaceFactory
from core.formatters import PriceFormatter, TextGenerator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["parse"])
factory = MarketplaceFactory()


# === Pydantic-схемы ===

class ParseRequest(BaseModel):
    url: str = Field(..., description="URL товара с маркетплейса")
    format_prices: bool = Field(True, description="Форматировать цены (1 990₽)")
    generate_post: bool = Field(True, description="Генерировать текст поста")


class ProductResponse(BaseModel):
    success: bool
    marketplace: Optional[str] = None
    sku: Optional[str] = None
    title: Optional[str] = None
    discount_price: Optional[str] = None
    old_price: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    post_text: Optional[str] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str


# === Эндпоинты ===

@router.get("/health", response_model=HealthResponse)
async def health():
    from config.settings import settings
    return HealthResponse(
        status="ok",
        app=settings.APP_NAME,
        version=settings.VERSION,
    )


@router.post("/parse", response_model=ProductResponse)
async def parse_product(req: ParseRequest):
    """Спарсить товар по URL"""
    try:
        key, parser = factory.get_parser(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        data = parser.fetch(req.url)
    except Exception as e:
        logger.exception("Ошибка парсинга")
        raise HTTPException(status_code=500, detail=f"Ошибка парсинга: {e}")

    if not data.success:
        return ProductResponse(
            success=False,
            url=req.url,
            marketplace=key,
            error=data.error or "Не удалось получить данные",
        )

    if req.format_prices:
        data.discount_price = PriceFormatter.format_price(data.discount_price)
        data.old_price = PriceFormatter.format_price(data.old_price)

    post_text = TextGenerator.generate(data) if req.generate_post else None

    return ProductResponse(
        success=True,
        marketplace=key,
        sku=data.sku,
        title=data.title,
        discount_price=data.discount_price,
        old_price=data.old_price,
        url=data.url,
        image_url=data.image_url,
        post_text=post_text,
    )