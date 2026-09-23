"""Сравнение цен по маркетплейсам."""
import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.marketplace import MarketplaceFactory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["compare"])


class CompareRequest(BaseModel):
    query: str = Field(..., description="Поисковый запрос")
    limit: int = Field(default=3, ge=1, le=10)


class Offer(BaseModel):
    marketplace: str
    title: Optional[str] = None
    price_rub: Optional[float] = None
    price_original_rub: Optional[float] = None
    seller: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None


class CompareResponse(BaseModel):
    query: str
    offers: List[Offer]
    cheapest: Optional[Offer] = None
    spread: Optional[float] = None
    complete: bool


def _build_search_url(marketplace: str, query: str) -> str:
    """Построить URL поиска для маркетплейса."""
    import urllib.parse
    q = urllib.parse.quote(query)
    if marketplace == "yandex":
        return f"https://market.yandex.ru/search?text={q}"
    if marketplace == "ozon":
        return f"https://www.ozon.ru/search/?text={q}"
    if marketplace == "wildberries":
        return f"https://www.wildberries.ru/catalog/0/search.aspx?search={q}"
    if marketplace == "aliexpress":
        return f"https://aliexpress.ru/wholesale?SearchText={q}"
    raise ValueError(f"Неизвестный маркетплейс: {marketplace}")


@router.post("/compare", response_model=CompareResponse)
async def compare_prices(req: CompareRequest):
    """Сравнить цены по всем маркетплейсам."""
    factory = MarketplaceFactory()
    marketplaces = ["ozon", "wildberries", "aliexpress", "yandex"]

    async def fetch_one(mp: str) -> Optional[Offer]:
        try:
            url = _build_search_url(mp, req.query)
            key, parser = factory.get_parser(url)
            data = await asyncio.to_thread(parser.fetch, url)
            if data.success and data.price_rub:
                return Offer(
                    marketplace=mp,
                    title=data.title,
                    price_rub=data.price_rub,
                    price_original_rub=data.price_original_rub,
                    seller=data.seller,
                    url=data.url,
                    image_url=data.image_url,
                )
        except Exception as e:
            logger.warning(f"{mp}: {e}")
        return None

    results = await asyncio.gather(*[fetch_one(mp) for mp in marketplaces])
    offers = [r for r in results if r is not None]
    offers.sort(key=lambda x: x.price_rub or float("inf"))

    return CompareResponse(
        query=req.query,
        offers=offers,
        cheapest=offers[0] if offers else None,
        spread=(offers[-1].price_rub - offers[0].price_rub) if len(offers) > 1 else None,
        complete=len(offers) == len(marketplaces),
    )