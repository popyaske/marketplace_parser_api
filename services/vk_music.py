"""Получение популярных треков VK для фона видео."""
import logging
import random
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = "5.199"


async def get_popular_audio(token: str, count: int = 50) -> Optional[dict]:
    """Возвращает один случайный популярный трек (dict с 'url', 'artist', 'title')."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{VK_API_BASE}/audio.getPopular",
                params={
                    "access_token": token,
                    "v": VK_API_VERSION,
                    "count": count,
                    "only_eng": 0,
                },
            )
            data = r.json()
            if "error" in data:
                logger.warning("audio.getPopular error: %s", data["error"])
                return None
            items = data.get("response") or []
            if not items:
                logger.warning("audio.getPopular вернул пустой список")
                return None
            # фильтруем треки с прямой ссылкой
            valid = [t for t in items if isinstance(t, dict) and t.get("url")]
            if not valid:
                logger.warning("в популярных треках нет прямых URL")
                return None
            track = random.choice(valid)
            logger.info("VK music: выбран трек %s — %s", track.get("artist"), track.get("title"))
            return track
    except Exception as e:
        logger.warning("VK music: ошибка получения трека: %s", e)
        return None


async def download_audio(url: str, dest_path: str) -> bool:
    """Скачивает mp3 по URL в dest_path."""
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                f.write(r.content)
        logger.info("VK music: трек скачан %s (%d байт)", dest_path, len(r.content))
        return True
    except Exception as e:
        logger.warning("VK music: не удалось скачать трек: %s", e)
        return False