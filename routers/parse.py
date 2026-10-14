"""FastAPI роуты"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from core.formatters import TextGenerator
from services.marketplace import MarketplaceFactory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["parse"])
factory = MarketplaceFactory()


class ParseRequest(BaseModel):
    url: str = Field(..., description="URL товара")
    format_prices: bool = Field(True, description="Форматировать цены")
    generate_post: bool = Field(True, description="Генерировать текст поста")


class ProductResponse(BaseModel):
    success: bool
    marketplace: Optional[str] = None
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
    url: Optional[str] = None
    tier_used: Optional[str] = None
    post_text: Optional[str] = None
    error: Optional[str] = None


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/parse", response_model=ProductResponse)
async def parse_product(req: ParseRequest):
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

    post_text = TextGenerator.generate(data) if req.generate_post else None

    return ProductResponse(
        success=True,
        marketplace=key,
        sku=data.sku,
        title=data.title,
        price_rub=data.price_rub,
        price_with_plus=data.price_with_plus,
        price_original_rub=data.price_original_rub,
        seller=data.seller,
        brand=data.brand,
        rating=data.rating,
        rating_count=data.rating_count,
        in_stock=data.in_stock,
        image_url=data.image_url,
        url=data.url,
        tier_used=data.tier_used,
        post_text=post_text,
    )

@router.get("/cdp-check")
async def cdp_check():
    """
    Проверить доступность Chrome CDP.

    Возвращает host и port, к которым реально подключается парсер —
    это помогает понять, куда смотрит код в Docker.
    """
    import httpx
    from config.settings import settings

    # Достаём значения явно — если settings это модуль, увидим это
    host = getattr(settings, "CDP_HOST", None)
    port = getattr(settings, "CDP_PORT", None)

    # Диагностика: показываем, откуда взялись значения
    debug = {
        "settings_type": type(settings).__name__,
        "has_CDP_HOST": hasattr(settings, "CDP_HOST"),
        "has_CDP_PORT": hasattr(settings, "CDP_PORT"),
        "raw_host": host,
        "raw_port": port,
    }

    # Если settings — модуль, атрибутов нет, ставим дефолты для docker
    if host is None:
        host = "chrome"
    if port is None:
        port = 3000

    try:
        r = httpx.get(f"http://{host}:{port}/json/version", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            return {
                "available": True,
                "host": host,
                "port": port,
                "browser": data.get("Browser"),
                "ws_url": data.get("webSocketDebuggerUrl"),
                **debug,
            }
        return {
            "available": False,
            "host": host,
            "port": port,
            "error": f"HTTP {r.status_code}",
            **debug,
        }
    except Exception as e:
        return {
            "available": False,
            "host": host,
            "port": port,
            "error": str(e),
            **debug,
        }