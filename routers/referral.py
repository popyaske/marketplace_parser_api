"""Ручной запуск публикации Яндекс-рефералки."""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from broker.connection import broker, QUEUE_REFERRAL_MANUAL

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/referral", tags=["referral"])


class ReferralPublishRequest(BaseModel):
    url: str = Field(..., description="URL карточки Яндекс.Маркета")


class ReferralPublishResponse(BaseModel):
    success: bool
    message: str


@router.post("/publish", response_model=ReferralPublishResponse)
async def publish_referral(req: ReferralPublishRequest):
    """
    Поставить URL в очередь на публикацию Яндекс-рефералки.

    Публикация произойдёт асинхронно: воркер слушает
    очередь parser.referral.manual, получает реферальную
    ссылку через CDP и публикует пост в Telegram.
    """
    if not req.url or "market.yandex.ru" not in req.url:
        raise HTTPException(
            status_code=400,
            detail="Ожидается URL вида https://market.yandex.ru/...",
        )

    try:
        await broker.connect()
        await broker.publish(
            QUEUE_REFERRAL_MANUAL,
            {"url": req.url.strip()},
        )
    except Exception as e:
        logger.exception("referral: не удалось поставить в очередь")
        raise HTTPException(status_code=500, detail=str(e))

    return ReferralPublishResponse(
        success=True,
        message="URL добавлен в очередь реферальных публикаций",
    )