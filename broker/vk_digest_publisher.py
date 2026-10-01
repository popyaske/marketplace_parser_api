"""
Раз в сутки собирает 5 последних фото, опубликованных в Telegram,
в вертикальный клип 9:16 с популярной музыкой VK и публикует в Клипы.
"""
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import random

from db import (
    get_unused_media,
    create_published_video,
    update_published_video,
    mark_media_as_used,
    cleanup_old_media,
)
from services.media_storage import VIDEOS_DIR, MUSIC_DIR, ensure_dirs
from services.video_maker import create_slideshow
from services.vk_client import VKClient
from services.vk_music import get_popular_audio, download_audio

logger = logging.getLogger(__name__)

VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN", "")
VK_USER_ID = int(os.getenv("VK_USER_ID", "0") or 0)

VK_DIGEST_INTERVAL = int(os.getenv("VK_DIGEST_INTERVAL", "86400"))
VK_PHOTOS_PER_VIDEO = int(os.getenv("VK_PHOTOS_PER_VIDEO", "5"))
VK_PHOTO_DURATION = float(os.getenv("VK_PHOTO_DURATION", "3.0"))
VK_VIDEO_WIDTH = int(os.getenv("VK_VIDEO_WIDTH", "1080"))
VK_VIDEO_HEIGHT = int(os.getenv("VK_VIDEO_HEIGHT", "1920"))
VK_USE_POPULAR_MUSIC = os.getenv("VK_USE_POPULAR_MUSIC", "1") == "1"
MIN_PHOTOS = 3  # минимальный порог, иначе видео не

# === Отдельный пул хештегов для VK ===
VK_HASHTAGS_RAW = os.getenv(
    "VK_HASHTAGS",
    "#скидки,#маркетплейсы,#промокоды,#ozon,#wildberries,#яндексмаркет,#aliexpress,#распродажа",
)
VK_HASHTAGS_COUNT = int(os.getenv("VK_HASHTAGS_COUNT", "3"))
VK_CHANNEL_LINK = os.getenv("VK_CHANNEL_LINK", "https://t.me/PromoMP1")

# Разбираем пул один раз при импорте
VK_HASHTAGS_POOL: list[str] = [
    t.strip() for t in VK_HASHTAGS_RAW.split(",") if t.strip()
]

async def run_vk_digest_publisher():
    if not VK_ACCESS_TOKEN or not VK_USER_ID:
        logger.warning("VK digest: нет VK_ACCESS_TOKEN/VK_USER_ID, выключаюсь")
        return

    logger.info(
        "VK digest publisher запущен: интервал %d сек (%.1f ч), %d фото по %.1f сек, %dx%d",
        VK_DIGEST_INTERVAL, VK_DIGEST_INTERVAL / 3600.0,
        VK_PHOTOS_PER_VIDEO, VK_PHOTO_DURATION, VK_VIDEO_WIDTH, VK_VIDEO_HEIGHT,
    )

    # первый запуск — сразу
    while True:
        try:
            await _make_and_upload_one()
            # раз в сутки — подчищаем старое
            deleted = await cleanup_old_media(days=1)
            if deleted:
                logger.info("VK digest: очищено %d старых записей media", deleted)
        except asyncio.CancelledError:
            logger.info("VK digest publisher остановлен")
            raise
        except Exception as e:
            logger.exception("VK digest ошибка: %s", e)

        await asyncio.sleep(VK_DIGEST_INTERVAL)


def _build_vk_post_text() -> str:
    """
    Собирает текст поста для VK:
      - базовый шаблон ""
      - N случайных хештегов из VK_HASHTAGS_POOL
      - ссылку на Telegram-канал

    Возвращает готовую строку, которую можно передать
    в video.save(description=...) и/или wall.post(message=...).
    """
    body = ""

    hashtags: list[str] = []
    if VK_HASHTAGS_POOL:
        n = min(VK_HASHTAGS_COUNT, len(VK_HASHTAGS_POOL))
        hashtags = random.sample(VK_HASHTAGS_POOL, n)

    parts: list[str] = []
    if body:
        parts.append(body)
    if hashtags:
        parts.append(" ".join(hashtags))
    if VK_CHANNEL_LINK:
        parts.append(VK_CHANNEL_LINK)

    return "\n\n".join(parts)


def _build_vk_video_title() -> str:
    """Заголовок видео в VK (не путать с текстом поста)."""
    return f"Скидки {datetime.now().strftime('%d.%m.%Y')}"


async def _make_and_upload_one():
    ensure_dirs()

    media = await get_unused_media(limit=VK_PHOTOS_PER_VIDEO)
    if not media:
        logger.info("VK digest: нет новых фото")
        return

    logger.info("VK digest: собрано %d фото", len(media))

    if len(media) < MIN_PHOTOS:
        logger.info(
            "VK digest: фото меньше минимума (%d < %d), пропускаю",
            len(media), MIN_PHOTOS,
        )
        return

    photo_paths = [m.image_path for m in media if Path(m.image_path).exists()]
    if not photo_paths:
        logger.warning("VK digest: файлов на диске нет, помечаю как использованные")
        dummy = await create_published_video(
            file_path="", photo_count=0, duration_sec=0.0, status="skipped"
        )
        await mark_media_as_used(dummy.id, [m.id for m in media])
        return

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    video_path = VIDEOS_DIR / f"clip_{ts}.mp4"
    duration = len(photo_paths) * VK_PHOTO_DURATION

    # === Музыка ===
    music_path = await _prepare_music()

    ok = await asyncio.to_thread(
        create_slideshow,
        photo_paths,
        str(video_path),
        duration_per_photo=VK_PHOTO_DURATION,
        width=VK_VIDEO_WIDTH,
        height=VK_VIDEO_HEIGHT,
        music_path=str(music_path) if music_path else None,
    )
    if not ok:
        logger.error("VK digest: не удалось создать видео")
        return

    video = await create_published_video(
        file_path=str(video_path),
        photo_count=len(photo_paths),
        duration_sec=duration,
        status="created",
    )
    await mark_media_as_used(video.id, [m.id for m in media])

    # === Публикация в VK ===
    post_text = _build_vk_post_text()
    video_title = _build_vk_video_title()
    logger.info("VK digest: текст поста — %r", post_text[:200])

    try:
        vk = VKClient(VK_ACCESS_TOKEN, VK_USER_ID)
        try:
            owner_id, vk_video_id = await asyncio.to_thread(
                vk.upload_video,
                str(video_path),
                video_title,
                post_text,  # ← теперь с хештегами и ссылкой
                True,  # wallpost=True
            )
            await update_published_video(
                video.id,
                vk_owner_id=owner_id,
                vk_video_id=vk_video_id,
                status="uploaded",
            )
            logger.info(
                "VK digest: видео опубликовано (owner_id=%s, video_id=%s, %d фото, %.1f сек)",
                owner_id, vk_video_id, len(photo_paths), duration,
            )
        finally:
            vk.close()
    except Exception as e:
        logger.exception("VK digest: ошибка публикации: %s", e)
        await update_published_video(video.id, status="failed", error=str(e)[:500])

async def _prepare_music() -> Optional[Path]:
    """Возвращает путь к mp3 для фона (или None)."""
    if MUSIC_DIR.exists():
        tracks = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.m4a"))
        if tracks:
            import random
            return random.choice(tracks)
    return None