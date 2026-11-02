"""
Раз в сутки собирает 5 последних фото, опубликованных в Telegram,
в вертикальный клип 9:16 с популярной музыкой VK и публикует в VK.

Хештеги:
  - Раз в 6 часов обновляются из vk.ru/trending (iframe vklanding).
  - Если trending недоступен - фолбэк на .env (VK_HASHTAGS).
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

logger = logging.getLogger(__name__)

# === VK ===
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN", "")
VK_USER_ID = int(os.getenv("VK_USER_ID", "0") or 0)
VK_CHANNEL_LINK = os.getenv("VK_CHANNEL_LINK", "https://t.me/PromoMP1")

# === VK Stories ===
VK_PUBLISH_STORY    = os.getenv("VK_PUBLISH_STORY", "0") == "1"     # вкл/выкл
VK_STORY_GROUP_ID   = int(os.getenv("VK_STORY_GROUP_ID", "0") or 0) # 0 = от себя
VK_STORY_LINK_TEXT  = os.getenv("VK_STORY_LINK_TEXT", "more")
# Ссылка - берётся из VK_CHANNEL_LINK, либо отдельная VK_STORY_LINK_URL
VK_STORY_LINK_URL   = os.getenv("VK_STORY_LINK_URL") or VK_CHANNEL_LINK

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


def _build_vk_post_text(media: list | None = None) -> str:
    """
    Полный текст описания для VK-видео.

    Состав:
      1. Заголовок
      2. Интро (о чём подборка)
      3. Топ-5 товаров из текущего клипа с ценами
      4. CTA + ссылка на Telegram
      5. Хештеги (трендовые из vk.ru/trending или .env-фолбэк)
    """
    hashtags = _pick_hashtags(VK_HASHTAGS_COUNT)

    parts: list[str] = []
    parts.append("🔥 Свежая подборка скидок и трендов!")
    parts.append("")
    parts.append(
        "В этом клипе - самые интересные предложения "
        "с Ozon, Wildberries, Яндекс.Маркета и AliExpress."
    )

    # === Топ-5 товаров ===
    if media:
        lines: list[str] = []
        for m in media[:5]:
            title = (getattr(m, "title", None) or "").strip()
            if not title:
                continue
            if len(title) > 60:
                title = title[:57].rstrip() + "…"

            # === Приоритет: Пэй/карта → обычная акционная → ничего ===
            # (price_with_plus - это Pay-цена для Яндекса, card-цена для Ozon)
            plus = getattr(m, "price_with_plus", None)
            rub = getattr(m, "price_rub", None)
            original = getattr(m, "price_original_rub", None)

            # Скидочная цена = та, что показывается крупно на карточке
            main_price = plus if plus else rub

            if not main_price:
                lines.append(f"• {title}")
                continue

            main_str = f"{int(main_price):,}".replace(",", " ") + " ₽"

            # Если есть "было" и оно больше - дописываем зачёркнутой
            if original and original > main_price:
                old_str = f"{int(original):,}".replace(",", " ") + " ₽"
                # эмодзи вместо зачёркивания: VK не поддерживает <s> в описании видео
                lines.append(f"• {title} - {main_str}  (было {old_str})")
            else:
                lines.append(f"• {title} - {main_str}")

        if lines:
            parts.append("")
            parts.append("📦 В подборке:")
            parts.extend(lines)

    parts.append("")
    if VK_CHANNEL_LINK:
        parts.append(
            f"💬 Все товары и ссылки - в нашем Telegram: {VK_CHANNEL_LINK}"
        )
    parts.append("")
    parts.append("❤️ Поставь лайк, если было полезно, и подпишись!")

    if hashtags:
        parts.append("")
        parts.append(" ".join(hashtags))

    return "\n".join(parts)


def _build_vk_video_title() -> str:
    """Заголовок видео в VK (до 128 символов)."""
    today = datetime.now().strftime("%d.%m.%Y")
    return f"🔥 Скидки и тренды {today} - Ozon, WB, Яндекс, Ali"


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
    Если папка пуста - None (видео соберётся без звука).
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
            logger.info("VK digest: фон - %s", track.name)
            return track

    logger.warning("VK digest: media/music/ пуста - видео без звука")
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
            "VK digest: фото %d из %d - жду накопления",
            len(media), VK_PHOTOS_PER_VIDEO,
        )
        return

    logger.info("VK digest: взято %d фото", len(media))

    # === Вписываем уже стилизованные карточки в 9:16 ===
    styled_paths = [m.image_path for m in media if Path(m.image_path).exists()]
    if not styled_paths:
        logger.warning("VK digest: нет файлов на диске, чищу записи")
        await delete_media_records([m.id for m in media])
        return

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    video_path = VIDEOS_DIR / f"clip_{ts}.mp4"
    duration = len(styled_paths) * VK_PHOTO_DURATION

    music_path = await _prepare_music()

    try:
        ok = await asyncio.wait_for(
            asyncio.to_thread(
                create_slideshow,
                styled_paths,
                str(video_path),
                duration_per_photo=VK_PHOTO_DURATION,
                width=VK_VIDEO_WIDTH,
                height=VK_VIDEO_HEIGHT,
                music_path=str(music_path) if music_path else None,
            ),
            timeout=240.0,
        )
    except asyncio.TimeoutError:
        logger.error(
            "VK digest: create_slideshow TIMEOUT 240s "
            "(photos=%d, music=%s) — пропускаю",
            len(styled_paths), music_path.name if music_path else None,
        )
        _cleanup_empty_video(video_path)
        return

    if not ok:
        logger.error("VK digest: не удалось создать видео, чищу styled и выхожу")
        _cleanup_empty_video(video_path)
        return

    # Записываем факт создания видео
    video = await create_published_video(
        file_path=str(video_path),
        photo_count=len(styled_paths),
        duration_sec=duration,
        status="created",
    )

    # === Публикация в VK ===
    post_text = _build_vk_post_text(media)
    video_title = _build_vk_video_title()
    logger.info("VK digest: текст поста - %r", post_text[:200])

    try:
        vk = VKClient(VK_ACCESS_TOKEN, VK_USER_ID)
        try:
            try:
                owner_id, vk_video_id = await asyncio.wait_for(
                    asyncio.to_thread(
                        vk.upload_video,
                        str(video_path),
                        video_title,
                        post_text,
                        True,
                    ),
                    timeout=300.0,
                )
            except asyncio.TimeoutError:
                logger.error("VK digest: upload_video TIMEOUT 300s")
                await update_published_video(
                    video.id, status="failed", error="upload timeout",
                )
                return

            await update_published_video(
                video.id,
                vk_owner_id=owner_id,
                vk_video_id=vk_video_id,
                status="uploaded",
            )
            logger.info(
                "VK digest: видео опубликовано (owner_id=%s, video_id=%s, %d фото, %.1f сек)",
                owner_id, vk_video_id, len(styled_paths), duration,
            )

            # ============================================================
            # 3. Убираем ВСЁ, что было сгенерировано в этом цикле:
            #    оригиналы (image_path) + промежуточные _9x16.jpg
            # ============================================================
            removed = 0
            for p in set(styled_paths):
                if not p:
                    continue
                try:
                    fp = Path(p)
                    if fp.exists() and fp.is_file():
                        fp.unlink()
                        removed += 1
                except Exception as e:
                    logger.warning("VK digest: не удалить %s: %s", p, e)

            deleted_records = await delete_media_records([m.id for m in media])
            logger.info(
                "VK digest: удалено %d файлов из photos/ и %d записей media",
                removed, deleted_records,
            )

            # ============================================================
            # 4. Убираем видеофайл с диска (запись в БД оставляем)
            # ============================================================
            try:
                video_file = Path(video_path)
                if video_file.exists():
                    size_mb = video_file.stat().st_size / 1e6
                    video_file.unlink()
                    logger.info(
                        "VK digest: видеофайл удалён с диска (%s, %.1f MB)",
                        video_file.name, size_mb,
                    )
                else:
                    logger.warning(
                        "VK digest: видеофайл не найден для удаления: %s",
                        video_path,
                    )
            except Exception as e:
                logger.warning(
                    "VK digest: не удалось удалить видеофайл %s: %s",
                    video_path, e,
                )

        finally:
            vk.close()
    except Exception as e:
        logger.exception("VK digest: ошибка публикации: %s", e)
        await update_published_video(video.id, status="failed", error=str(e)[:500])
        return


def _cleanup_empty_video(path) -> None:
    """Удаляет 0-байтный mp4 после неудачной сборки."""
    try:
        from pathlib import Path
        p = Path(path)
        if p.exists() and p.stat().st_size == 0:
            p.unlink()
    except Exception:
        pass