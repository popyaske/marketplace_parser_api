"""Локальное хранилище фото для VK-видео."""
import hashlib
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MIN_FREE_BYTES = int(os.getenv("MEDIA_MIN_FREE_BYTES", str(500 * 1024 * 1024)))  # 500 MB
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


def _has_free_space(path: str, min_bytes: int = MIN_FREE_BYTES) -> bool:
    try:
        usage = shutil.disk_usage(path)
        return usage.free >= min_bytes
    except OSError:
        return True


def save_photo(data: bytes, url: str, source: str) -> Optional[str]:
    """Сохраняет фото на диск. Возвращает локальный путь или None."""
    # === Защита от пустых/битых байтов ===
    if not data or len(data) < 100:
        logger.warning(
            "media: save_photo получил пустые/короткие байты (%d) — пропускаю (%s)",
            len(data) if data else 0, source,
        )
        return None

    # Быстрая проверка по magic bytes — реальный формат, а не расширение
    head = data[:12]
    is_jpeg = head[:3] == b"\xff\xd8\xff"
    is_png = head[:8] == b"\x89PNG\r\n\x1a\n"
    is_webp = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if not (is_jpeg or is_png or is_webp):
        logger.warning(
            "media: save_photo получил не-картинку (head=%r, len=%d) — пропускаю (%s)",
            head, len(data), source,
        )
        return None

    if not _has_free_space(str(PHOTOS_DIR)):
        logger.error("save_photo: недостаточно места на диске (<%d MB) — пропускаю", MIN_FREE_BYTES // 1024 // 1024)
        return None
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