"""Сборка вертикального слайдшоу 9:16 из фото через ffmpeg."""
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _scale9x16_chain(idx: int, W: int, H: int, fps: int) -> str:
    """
    Фон = вся картинка, растянутая на W×H + размытие.
    Передний план = картинка по ширине W, с сохранением пропорций.
    Overlay по центру.
    """
    return (
        f"[{idx}:v]split=2[bg{idx}_src][fg{idx}_src];"
        f"[bg{idx}_src]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},gblur=sigma=30[bg{idx}];"
        f"[fg{idx}_src]scale={W}:-2[fg{idx}];"
        f"[bg{idx}][fg{idx}]overlay=(W-w)/2:(H-h)/2,"
        f"setsar=1,fps={fps}[v{idx}]"
    )


def create_slideshow(
    photos: list[str],
    output_path: str,
    duration_per_photo: float = 3.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 60,
    music_path: Optional[str] = None,
) -> bool:
    """
    Собирает вертикальное слайдшоу 9:16 с эффектом Ken Burns и музыкой.

    Возвращает True при успехе.
    """
    if not _check_ffmpeg():
        logger.error("video: ffmpeg не найден в PATH")
        return False

    existing = [Path(p) for p in photos if Path(p).exists()]
    if not existing:
        logger.error("video: нет ни одного существующего фото")
        return False

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    n = len(existing)
    total_duration = n * duration_per_photo

    # Строим filter_complex:
    #   каждый вход → scale/crop до 9:16 → zoompan (Ken Burns) → xfade между ними
    inputs: list[str] = []
    for p in existing:
        inputs += ["-loop", "1", "-t", str(duration_per_photo), "-i", str(p)]

    # Базовый фильтр для каждого фото: кроп до 9:16 + zoompan
    # scale сначала увеличиваем, потом zoompan уменьшает видимую область
    filter_parts: list[str] = []
    for i in range(n):
        filter_parts.append(_scale9x16_chain(i, width, height, fps))

    # Переходы xfade между парами
    if n == 1:
        last_label = "v0"
    else:
        fade = 0.3
        prev = "v0"
        for i in range(1, n):
            offset = i * duration_per_photo - fade * i
            out_label = f"x{i}" if i < n - 1 else "vout"
            filter_parts.append(
                f"[{prev}][v{i}]xfade=transition=fade:"
                f"duration={fade}:offset={offset:.3f}[{out_label}]"
            )
            prev = out_label
        last_label = "vout"

    # Формат для mp4
    filter_parts.append(f"[{last_label}]format=yuv420p[v]")
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    cmd += inputs

    if music_path and Path(music_path).exists():
        cmd += ["-stream_loop", "-1", "-i", music_path]

    cmd += ["-filter_complex", filter_complex, "-map", "[v]"]

    if music_path and Path(music_path).exists():
        cmd += ["-map", f"{n}:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]

    cmd += [
        "-t", f"{total_duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-movflags", "+faststart",
        str(output),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=900, text=True)
        if result.returncode != 0:
            logger.error("video: ffmpeg вернул %d: %s", result.returncode, result.stderr[-800:])
            return False
        logger.info(
            "video: создан %s (%.1f MB, %dx%d, %.1f сек)",
            output.name, output.stat().st_size / 1e6, width, height, total_duration,
        )
        return True
    except subprocess.TimeoutExpired:
        logger.error("video: ffmpeg таймаут 900 сек")
        return False
    except Exception as e:
        logger.exception("video: ошибка ffmpeg: %s", e)
        return False