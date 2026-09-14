"""Producer — отправляет URL в очередь."""
import logging
from typing import Optional

from .connection import broker, QUEUE_INCOMING

logger = logging.getLogger(__name__)


async def send_url(url: str, priority: int = 5, meta: Optional[dict] = None):
    """
    Отправить URL в очередь на парсинг.

    Args:
        url: URL товара
        priority: приоритет (1 = высокий, 10 = низкий)
        meta: дополнительные метаданные (user_id, callback_url и т.д.)
    """
    payload = {
        "url": url,
        "priority": priority,
        "meta": meta or {},
    }
    await broker.publish(QUEUE_INCOMING, payload)
    logger.info(f"URL отправлен в очередь: {url}")


async def send_urls(urls: list[str]):
    """Отправить несколько URL."""
    for url in urls:
        await send_url(url)