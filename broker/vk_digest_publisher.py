"""
Раз в сутки собирает 5 последних фото, опубликованных в Telegram,
в вертикальный клип 9:16 с популярной музыкой VK и публикует в VK.

Хештеги:
  - Раз в 6 часов обновляются из vk.ru/trending (iframe vklanding).
  - Если trending недоступен — фолбэк на .env (VK_HASHTAGS).
"""
import asyncio
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from db import (
    get_unused_media,
    create_published_video,
    update_published_video,
    delete_media_records,
    cleanup_old_media,
)
from services.media_storage import (
    VIDEOS_DIR,
    MUSIC_DIR,
    ensure_dirs,
    delete_photo,
)
from services.video_maker import create_slideshow
from services.vk_client import VKClient
from services.overlay_maker import render_and_save

logger = logging.getLogger(__name__)

# === VK ===
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN", "")
VK_USER_ID = int(os.getenv("VK_USER_ID", "0") or 0)
VK_CHANNEL_LINK = os.getenv("VK_CHANNEL_LINK", "https://t.me/PromoMP1")

# === Видео ===
VK_DIGEST_INTERVAL = int(os.getenv("VK_DIGEST_INTERVAL", "86400"))       # сек
VK_PHOTOS_PER_VIDEO = int(os.getenv("VK_PHOTOS_PER_VIDEO", "5"))
VK_PHOTO_DURATION = float(os.getenv("VK_PHOTO_DURATION", "3.0"))
VK_VIDEO_WIDTH = int(os.getenv("VK_VIDEO_WIDTH", "1080"))
VK_VIDEO_HEIGHT = int(os.getenv("VK_VIDEO_HEIGHT", "1920"))
MIN_PHOTOS = 3   # ниже этого порога видео не собираем

# === Хештеги: env-фолбэк ===
VK_HASHTAGS_RAW = os.getenv(
    "VK_HASHTAGS",
    "#скидки,#маркетплейсы,#промокоды,#ozon,#wildberries,#яндексмаркет,#aliexpress,#распродажа",
)
VK_HASHTAGS_COUNT = int(os.getenv("VK_HASHTAGS_COUNT", "3"))
VK_HASHTAGS_POOL: list[str] = [
    t.strip() for t in VK_HASHTAGS_RAW.split(",") if t.strip()
]

# === Хештеги: trending ===
VK_HASHTAGS_SOURCE = os.getenv("VK_HASHTAGS_SOURCE", "trending").lower()
VK_TRENDING_PAGES = int(os.getenv("VK_TRENDING_PAGES", "2"))
VK_TRENDING_REFRESH_SEC = int(os.getenv("VK_TRENDING_REFRESH_SEC", str(6 * 3600)))

# === Runtime ===
VK_HASHTAGS_RUNTIME: list[str] = []


# ============================================================
# Планировщик
# ============================================================

async def run_vk_digest_publisher():
    if not VK_ACCESS_TOKEN or not VK_USER_ID:
        logger.warning("VK digest: нет VK_ACCESS_TOKEN/VK_USER_ID, выключаюсь")
        return

    logger.info(
        "VK digest publisher запущен: интервал %d сек (%.1f ч), %d фото по %.1f сек, %dx%d",
        VK_DIGEST_INTERVAL, VK_DIGEST_INTERVAL / 3600.0,
        VK_PHOTOS_PER_VIDEO, VK_PHOTO_DURATION, VK_VIDEO_WIDTH, VK_VIDEO_HEIGHT,
    )
    await _refresh_trending_hashtags()
    last_hashtags_refresh = time.monotonic()

    while True:
        try:
            if time.monotonic() - last_hashtags_refresh > VK_TRENDING_REFRESH_SEC:
                await _refresh_trending_hashtags()
                last_hashtags_refresh = time.monotonic()

            await _make_and_upload_one()

            deleted = await cleanup_old_media(days=30)
            if deleted:
                logger.info("VK digest: очищено %d старых записей media", deleted)

        except asyncio.CancelledError:
            logger.info("VK digest publisher остановлен")
            raise
        except Exception as e:
            logger.exception("VK digest ошибка: %s", e)

        await asyncio.sleep(VK_DIGEST_INTERVAL)


# ============================================================
# Обновление трендовых хештегов
# ============================================================

async def _refresh_trending_hashtags() -> None:
    """Скрапит vk.ru/trending и обновляет VK_HASHTAGS_RUNTIME."""
    global VK_HASHTAGS_RUNTIME

    if VK_HASHTAGS_SOURCE != "trending":
        logger.info("VK hashtags: источник = env (trending отключён)")
        return

    try:
        from services.vk_trending import fetch_vk_trending_hashtags_sync

        cdp_host = os.getenv("CDP_HOST", "127.0.0.1")
        cdp_port = int(os.getenv("CDP_PORT", "9222"))

        tags = await asyncio.to_thread(
            fetch_vk_trending_hashtags_sync,
            cdp_host, cdp_port, VK_TRENDING_PAGES,
        )
        if tags:
            VK_HASHTAGS_RUNTIME = tags
            logger.info(
                "VK hashtags: пул обновлён из vk.ru/trending (%d тегов): %s",
                len(tags), tags[:15],
            )
        else:
            logger.warning(
                "VK hashtags: vk.ru/trending не дал тегов, остаюсь на .env (%d тегов)",
                len(VK_HASHTAGS_POOL),
            )
    except Exception as e:
        logger.warning("VK hashtags: ошибка обновления трендов: %s", e)



# ============================================================
# Выбор хештегов и сборка текста поста
# ============================================================

def _pick_hashtags(n: int) -> list[str]:
    """Берёт n случайных из runtime-пула (тренды) или env-пула (фолбэк)."""
    pool = VK_HASHTAGS_RUNTIME or VK_HASHTAGS_POOL
    if not pool:
        return []
    k = min(n, len(pool))
    return random.sample(pool, k)


def _build_vk_post_text() -> str:
    """
    Собирает текст поста для VK:
      - N случайных хештегов (тренды VK или .env)
      - ссылку на Telegram-канал
    """
    hashtags = _pick_hashtags(VK_HASHTAGS_COUNT)

    parts: list[str] = []
    if hashtags:
        parts.append(" ".join(hashtags))
    if VK_CHANNEL_LINK:
        parts.append(VK_CHANNEL_LINK)

    return "\n\n".join(parts)


def _build_vk_video_title() -> str:
    """Заголовок видео в VK."""
    return f"Скидки {datetime.now().strftime('%d.%m.%Y')}"


# ============================================================
# Музыка: выбор трека для видео
# ============================================================

def _pick_random_audio(directory: Path, exclude_names: set[str] | None = None) -> Optional[Path]:
    """Случайный mp3/m4a из папки (с фильтром по именам)."""
    if not directory.exists():
        return None
    exclude = exclude_names or set()
    tracks = [
        p for p in list(directory.glob("*.mp3")) + list(directory.glob("*.m4a"))
        if p.name not in exclude
    ]
    return random.choice(tracks) if tracks else None


async def _prepare_music() -> Optional[Path]:
    """
    Возвращает mp3 для фона видео из media/music/.
    Если папка пуста — None (видео соберётся без звука).
    """
    if MUSIC_DIR.exists():
        tracks = (
            list(MUSIC_DIR.glob("*.mp3"))
            + list(MUSIC_DIR.glob("*.m4a"))
        )
        # Исключаем старый кэш от прошлых экспериментов
        tracks = [t for t in tracks if t.name != "current_popular.mp3"]
        if tracks:
            track = random.choice(tracks)
            logger.info("VK digest: фон — %s", track.name)
            return track

    logger.warning("VK digest: media/music/ пуста — видео без звука")
    return None


# ============================================================
# Одна публикация
# ============================================================

async def _make_and_upload_one():
    ensure_dirs()

    # Берём ровно N фото (в порядке создания)
    media = await get_unused_media(limit=VK_PHOTOS_PER_VIDEO)

    if len(media) < VK_PHOTOS_PER_VIDEO:
        logger.info(
            "VK digest: фото %d из %d — жду накопления",
            len(media), VK_PHOTOS_PER_VIDEO,
        )
        return

    logger.info("VK digest: взято %d фото", len(media))

    photo_paths = [m.image_path for m in media if Path(m.image_path).exists()]
    if not photo_paths:
        logger.warning("VK digest: файлов на диске нет, чищу записи")
        await delete_media_records([m.id for m in media])
        return

        # === Накладываем оформление на каждое фото ===
    styled_paths: list[str] = []
    for m in media:
        src = Path(m.image_path)
        if not src.exists():
            continue
        dst = src.with_name(src.stem + "_styled.jpg")

        # Бейдж с промокодом или расчётной скидкой
        promo = ""

        ok = await asyncio.to_thread(
            render_and_save,
            str(src),
            str(dst),
            title=m.title or "",
            brand=m.brand or "",
            price_rub=m.price_rub,
            price_with_plus=m.price_with_plus,
            price_original_rub=m.price_original_rub,
            rating=m.rating,
            rating_count=m.rating_count,
            legal_text=m.legal_text or "",
            promo_badge=promo,
            aspect="9:16",
        )
        if ok:
            styled_paths.append(str(dst))
        else:
            styled_paths.append(str(src))

    if not styled_paths:
        logger.warning("VK digest: не удалось подготовить ни одного фото")
        return

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    video_path = VIDEOS_DIR / f"clip_{ts}.mp4"
    duration = len(styled_paths) * VK_PHOTO_DURATION

    music_path = await _prepare_music()

    ok = await asyncio.to_thread(
        create_slideshow,
        styled_paths,
        str(video_path),
        duration_per_photo=VK_PHOTO_DURATION,
        width=VK_VIDEO_WIDTH,
        height=VK_VIDEO_HEIGHT,
        music_path=str(music_path) if music_path else None,
    )
    if not ok:
        logger.error("VK digest: не удалось создать видео, фото оставляю")
        return

    # === Удаляем styled-копии после сборки видео ===
    for p in styled_paths:
        if p.endswith("_styled.jpg"):
            try:
                Path(p).unlink()
            except Exception:
                pass

    # Записываем факт создания видео
    video = await create_published_video(
        file_path=str(video_path),
        photo_count=len(photo_paths),
        duration_sec=duration,
        status="created",
    )

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
                post_text,
                True,
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

            # === Успех → удаляем исходные фото ===
            deleted_files = 0
            for m in media:
                if delete_photo(m.image_path):
                    deleted_files += 1
            deleted_records = await delete_media_records([m.id for m in media])
            logger.info(
                "VK digest: удалено %d файлов и %d записей media",
                deleted_files, deleted_records,
            )

        finally:
            vk.close()
    except Exception as e:
        logger.exception("VK digest: ошибка публикации: %s", e)
        await update_published_video(video.id, status="failed", error=str(e)[:500])
        # Фото НЕ удаляем — уйдут в следующий цикл
        return