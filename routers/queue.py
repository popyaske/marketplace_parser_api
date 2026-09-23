"""API эндпоинты для работы с очередью."""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from slowapi import Limiter
from slowapi.util import get_remote_address

from broker.connection import broker
from broker.producer import send_url

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/v1/queue", tags=["queue"])


class EnqueueRequest(BaseModel):
    url: str = Field(..., description="URL товара")
    priority: int = Field(default=5, ge=1, le=10, description="Приоритет (1=высокий)")
    meta: Optional[dict] = Field(default=None, description="Метаданные")


class EnqueueBatchRequest(BaseModel):
    urls: list[str] = Field(..., description="Список URL")


class EnqueueResponse(BaseModel):
    success: bool
    message: str
    url: Optional[str] = None
    count: Optional[int] = None


@router.post("/enqueue", response_model=EnqueueResponse)
@limiter.limit("30/minute")
async def enqueue_url(request: Request, req: EnqueueRequest):
    """
    Добавить URL в очередь на парсинг.

    Результат парсинга появится в очереди `parser.results`.
    """
    try:
        await send_url(req.url, priority=req.priority, meta=req.meta)
        return EnqueueResponse(
            success=True,
            message=f"URL добавлен в очередь",
            url=req.url,
        )
    except Exception as e:
        logger.exception("Ошибка отправки в очередь")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/enqueue-batch", response_model=EnqueueResponse)
async def enqueue_batch(request: Request, req: EnqueueBatchRequest):
    """Добавить список URL в очередь."""
    try:
        for url in req.urls:
            await send_url(url)
        return EnqueueResponse(
            success=True,
            message=f"{len(req.urls)} URL добавлено в очередь",
            count=len(req.urls),
        )
    except Exception as e:
        logger.exception("Ошибка batch-отправки")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def queue_status():
    """Статус очередей через RabbitMQ Management API."""
    import httpx

    # Management API доступен по разным адресам в зависимости от того,
    # откуда вызывается — из docker-сети или с хоста
    management_urls = [
        "http://rabbitmq:15672",              # из docker-сети
        "http://host.docker.internal:15672",  # с хоста через docker
    ]

    for base_url in management_urls:
        try:
            r = httpx.get(
                f"{base_url}/api/queues?columns=name,messages",
                auth=("guest", "guest"),
                timeout=3.0,
            )
            if r.status_code == 200:
                queues = {q["name"]: q["messages"] for q in r.json()}
                return {
                    "connected": True,
                    "queues": {
                        "incoming": queues.get("parser.incoming", 0),
                        "results": queues.get("parser.results", 0),
                        "errors": queues.get("parser.errors", 0),
                    },
                }
        except Exception:
            continue

    return {"connected": False, "error": "Management API недоступен"}