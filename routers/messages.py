"""API для отслеживания опубликованных сообщений Telegram."""
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime

from db import list_published_messages, get_published_message, delete_published_message

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/messages", tags=["messages"])

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


class MessageOut(BaseModel):
    id: int
    product_id: Optional[int] = None
    chat_id: str
    message_id: int
    message_link: str
    source_url: Optional[str] = None
    marketplace: Optional[str] = None
    title: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MessagesPage(BaseModel):
    items: list[MessageOut]
    total: int
    limit: int
    offset: int


@router.get("", response_model=MessagesPage)
async def list_messages(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    marketplace: Optional[str] = Query(None),
    only_ozon_short: bool = Query(
        True,
        description="Только Ozon с короткой ссылкой https://ozon.ru/t/...",
    ),
):
    items, total = await list_published_messages(
        limit=limit,
        offset=offset,
        marketplace=marketplace,
        only_ozon_short=only_ozon_short,
    )
    return MessagesPage(
        items=[MessageOut.model_validate(m) for m in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.delete("/{msg_id}")
async def delete_message(
    msg_id: int,
    delete_from_telegram: bool = Query(False),
):
    """
    Удаляет запись о сообщении.
    Если delete_from_telegram=true — сначала пробует удалить из Telegram.
    """
    record = await get_published_message(msg_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")

    deleted_in_tg = False

    if delete_from_telegram and TELEGRAM_BOT_TOKEN:
        try:
            from aiogram import Bot
            from aiogram.client.session.aiohttp import AiohttpSession

            session = AiohttpSession()
            bot = Bot(token=TELEGRAM_BOT_TOKEN, session=session)
            try:
                await bot.delete_message(
                    chat_id=int(record.chat_id),
                    message_id=record.message_id,
                )
                deleted_in_tg = True
                logger.info(
                    "messages: удалено из Telegram chat=%s msg=%s",
                    record.chat_id, record.message_id,
                )
            finally:
                await bot.session.close()
        except Exception as e:
            logger.warning(
                "messages: не удалось удалить из Telegram: %s", e,
            )

    ok = await delete_published_message(msg_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")

    return {
        "success": True,
        "deleted_from_telegram": deleted_in_tg,
    }