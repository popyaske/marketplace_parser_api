"""services/video_maker.py — вертикальные клипы 9:16 для VK Shorts.

Принцип: НИКАКОЙ цветокоррекции.
  • фон 9:16 — размытая копия того же фото (без eq/colorbalance/vignette),
    чтобы карточка не «висела» на чёрных полосах;
  • передний план — оригинальное фото, по центру, с белой рамкой;
  • Ken Burns — очень мягкий zoom-in (по умолчанию 5%), только движение;
  • xfade-переходы, музыка зацикливается.

Опционально (через env, по умолчанию OFF):
  • VK_BG_BRIGHTNESS / CONTRAST / SATURATION — если всё-таки нужен грейд фона;
  • VK_VIGNETTE — виньетка;
  • VK_GRAIN — зерно.
"""

import logging
import os
import random
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================
# Параметры
# ============================================================

FPS = int(os.getenv("VK_VIDEO_FPS", "30"))

# --- Фон (по умолчанию — НЕЙТРАЛЬНЫЙ) ---
BG_BLUR_SIGMA    = float(os.getenv("VK_BG_BLUR", "50"))
BG_BRIGHTNESS    = float(os.getenv("VK_BG_BRIGHTNESS", "0.0"))    # 0 = не трогать
BG_CONTRAST      = float(os.getenv("VK_BG_CONTRAST",   "1.0"))    # 1 = не трогать
BG_SATURATION    = float(os.getenv("VK_BG_SATURATION", "1.0"))    # 1 = не трогать
# teal/orange — по умолчанию отключён
BG_CB_SHADOWS    = os.getenv("VK_BG_CB_SHADOWS",    "")           # "" = выкл
BG_CB_HIGHLIGHTS = os.getenv("VK_BG_CB_HIGHLIGHTS", "")           # "" = выкл

# --- Передний план ---
FG_FIT_WIDTH     = float(os.getenv("VK_FG_FIT_WIDTH", "0.88"))
FG_FIT_HEIGHT    = float(os.getenv("VK_FG_FIT_HEIGHT", "0.85"))
FG_BORDER        = int(os.getenv("VK_FG_BORDER", "10"))
FG_BORDER_COLOR  = os.getenv("VK_FG_BORDER_COLOR", "white")

# --- Ken Burns (мягкий по умолчанию) ---
KB_ZOOM_END      = float(os.getenv("VK_KB_ZOOM", "1.05"))
KB_ZOOM_SPEED    = os.getenv("VK_KB_SPEED")                       # None = авто

# --- Переходы ---
TRANSITION_DURATION = float(os.getenv("VK_TRANSITION_DURATION", "0.35"))
TRANSITIONS = os.getenv(
    "VK_TRANSITIONS",
    "fade,fadewhite,circleopen,circleclose,"
    "smoothleft,smoothright,slideleft,slideright",
).split(",")

# --- Финальный грейд кадра (по умолчанию — 1.0 / 0 / выкл) ---
FINAL_CONTRAST   = float(os.getenv("VK_FINAL_CONTRAST",   "1.0"))
FINAL_SATURATION = float(os.getenv("VK_FINAL_SATURATION", "1.0"))
VIGNETTE         = os.getenv("VK_VIGNETTE", "")                   # "" = выкл
GRAIN            = int(os.getenv("VK_GRAIN", "0"))                # 0 = выкл


# ============================================================
# Утилиты
# ============================================================

def _check_ffmpeg() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-version"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _run_ffmpeg(cmd: list[str], timeout: int = 300) -> bool:
    logger.info("ffmpeg: запуск (%d аргументов)", len(cmd))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg: таймаут %ds", timeout)
        return False
    except Exception as e:
        logger.exception("ffmpeg: ошибка запуска: %s", e)
        return False

    if r.returncode != 0:
        logger.error("ffmpeg: код %d\nstderr:\n%s",
                     r.returncode, (r.stderr or "")[-3000:])
        return False

    logger.info("ffmpeg: готово")
    return True


# ============================================================
# filter_complex
# ============================================================

def _build_bg_chain(src: str, dst: str) -> str:
    """Фон: crop до 9:16 + blur. Цвет — трогаем только если явно задан env."""
    steps = [
        f"scale=W:H:force_original_aspect_ratio=increase",
        f"crop=W:H",
        f"gblur=sigma={BG_BLUR_SIGMA}",
    ]
    if (BG_BRIGHTNESS != 0.0
            or BG_CONTRAST != 1.0
            or BG_SATURATION != 1.0):
        steps.append(
            f"eq=brightness={BG_BRIGHTNESS}:"
            f"contrast={BG_CONTRAST}:"
            f"saturation={BG_SATURATION}"
        )
    if BG_CB_SHADOWS and BG_CB_HIGHLIGHTS:
        rs, gs, bs = (float(x) for x in BG_CB_SHADOWS.split(","))
        rh, gh, bh = (float(x) for x in BG_CB_HIGHLIGHTS.split(","))
        steps.append(
            f"colorbalance=rs={rs}:gs={gs}:bs={bs}:"
            f"rh={rh}:gh={gh}:bh={bh}"
        )
    steps.append("format=yuv420p")
    return f"[{src}]" + ",".join(steps) + f"[{dst}]"


def _build_filter_complex(
    n: int,
    frames_per_photo: int,
    width: int,
    height: int,
    transition_duration: float,
) -> tuple[str, str]:
    parts: list[str] = []
    fg_w = int(width * FG_FIT_WIDTH)
    fg_h = int(height * FG_FIT_HEIGHT)
    border = FG_BORDER

    if KB_ZOOM_SPEED:
        kb_speed = float(KB_ZOOM_SPEED)
    else:
        kb_speed = (KB_ZOOM_END - 1.0) / max(1, frames_per_photo)

    for i in range(n):
        d = frames_per_photo

        parts.append(f"[{i}:v]split=2[bgsrc_{i}][fgsrc_{i}]")

        # 1) Фон — размытие, БЕЗ изменения цвета
        bg_chain = _build_bg_chain(f"bgsrc_{i}", f"bg_{i}")
        # подставим реальные W/H
        bg_chain = bg_chain.replace("scale=W:H", f"scale={width}:{height}") \
                           .replace("crop=W:H", f"crop={width}:{height}")
        parts.append(bg_chain)

        # 2) Передний план — оригинал, по центру, белая рамка
        parts.append(
            f"[fgsrc_{i}]"
            f"scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease,"
            f"pad=iw+{border * 2}:ih+{border * 2}:"
            f"{border}:{border}:{FG_BORDER_COLOR},"
            f"format=yuv420p"
            f"[fg_{i}]"
        )

        # 3) Overlay
        parts.append(
            f"[bg_{i}][fg_{i}]overlay=(W-w)/2:(H-h)/2[comp_{i}]"
        )

        # 4) Ken Burns (мягкий) + опциональные эффекты (по умолчанию выкл)
        zoom_expr = f"min(zoom+{kb_speed:.6f},{KB_ZOOM_END})"
        chain = (
            f"[comp_{i}]"
            f"zoompan=z='{zoom_expr}':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={FPS}"
        )
        if FINAL_CONTRAST != 1.0 or FINAL_SATURATION != 1.0:
            chain += (
                f",eq=contrast={FINAL_CONTRAST}:saturation={FINAL_SATURATION}"
            )
        if VIGNETTE:
            chain += f",vignette={VIGNETTE}"
        if GRAIN > 0:
            chain += f",noise=alls={GRAIN}:allf=t+u"
        chain += f",format=yuv420p[v_{i}]"
        parts.append(chain)

    if n == 1:
        return ";".join(parts), "v_0"

    seg_dur = frames_per_photo / FPS
    prev = "v_0"
    offset = seg_dur - transition_duration

    for i in range(1, n):
        trans = random.choice(TRANSITIONS)
        out = f"x_{i}"
        parts.append(
            f"[{prev}][v_{i}]"
            f"xfade=transition={trans}:"
            f"duration={transition_duration}:"
            f"offset={offset:.4f}"
            f"[{out}]"
        )
        prev = out
        offset += seg_dur - transition_duration

    return ";".join(parts), prev


# ============================================================
# Публичная функция
# ============================================================

def create_slideshow(
    image_paths: list[str],
    output_path: str,
    duration_per_photo: float = 3.0,
    width: int = 1080,
    height: int = 1920,
    music_path: Optional[str] = None,
) -> bool:
    if not _check_ffmpeg():
        logger.error("ffmpeg не найден")
        return False

    image_paths = [p for p in image_paths if Path(p).exists()]
    if not image_paths:
        logger.error("create_slideshow: нет валидных изображений")
        return False

    n = len(image_paths)
    if n == 1:
        logger.warning("create_slideshow: одно фото — переходов не будет")

    frames_per_photo = max(1, int(round(duration_per_photo * FPS)))

    filter_str, last_label = _build_filter_complex(
        n=n,
        frames_per_photo=frames_per_photo,
        width=width,
        height=height,
        transition_duration=TRANSITION_DURATION,
    )

    cmd: list[str] = ["ffmpeg", "-y"]
    for p in image_paths:
        cmd.extend(["-i", p])

    has_music = bool(music_path and Path(music_path).exists())
    if has_music:
        cmd.extend(["-stream_loop", "-1", "-i", str(music_path)])

    cmd.extend(["-filter_complex", filter_str, "-map", f"[{last_label}]"])

    if has_music:
        cmd.extend(["-map", f"{n}:a", "-c:a", "aac", "-b:a", "192k"])

    if n > 1:
        total_dur = n * duration_per_photo - (n - 1) * TRANSITION_DURATION
    else:
        total_dur = duration_per_photo

    cmd.extend([
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-movflags", "+faststart",
        "-t", f"{total_dur:.2f}",
        str(output_path),
    ])

    logger.info(
        "create_slideshow: %d фото × %.1fс = %.1fс, %dx%d, музыка=%s",
        n, duration_per_photo, total_dur, width, height,
        Path(music_path).name if has_music else "нет",
    )

    return _run_ffmpeg(cmd, timeout=300)