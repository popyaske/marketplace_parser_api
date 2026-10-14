"""Локальное хранилище фото для VK-видео."""
import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "/app/media"))
PHOTOS_DIR = MEDIA_ROOT / "photos"
VIDEOS_DIR = MEDIA_ROOT / "videos"
MUSIC_DIR = MEDIA_ROOT / "music"

def ensure_dirs() -> None:
    for d in (PHOTOS_DIR, VIDEOS_DIR, MUSIC_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _guess_ext(url: str) -> str:
    low = url.lower()
    if ".png" in low:
        return ".png"
    if ".webp" in low:
        return ".webp"
    if ".jpeg" in low or ".jpg" in low:
        return ".jpg"
    return ".jpg"


def save_photo(data: bytes, url: str, source: str) -> Optional[str]:
    """Сохраняет фото на диск. Возвращает локальный путь или None."""
    try:
        ensure_dirs()
        h = hashlib.md5(url.encode()).hexdigest()[:16]
        ext = _guess_ext(url)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{source}_{h}{ext}"
        path = PHOTOS_DIR / filename
        path.write_bytes(data)
        logger.info("media: фото сохранено %s (%d байт)", path.name, len(data))
        return str(path)
    except Exception as e:
        logger.warning("media: не удалось сохранить фото %s: %s", url, e)
        return None


def delete_photo(path: str) -> bool:
    """Удаляет файл по указанному пути. Возвращает True при успехе."""
    if not path:
        return False
    try:
        p = Path(path)
        if p.exists() and p.is_file():
            p.unlink()
            logger.info("media: удалён файл %s", p.name)
            return True
    except Exception as e:
        logger.warning("media: не удалить %s: %s", path, e)
    return False