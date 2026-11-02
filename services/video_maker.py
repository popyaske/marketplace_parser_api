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
    fps: int = 30,
    music_path: Optional[str] = None,
) -> bool:
    """
    Собирает вертикальное слайдшоу 9:16 с fade-переходами между фото
    и (опционально) музыкой.

    Ключевые решения:
      * concat + fade (не xfade: в FFmpeg 7.1.5 xfade падает с "1/0 invalid"
        на image2-входах);
      * каждое фото нормализуется через Pillow → чистый baseline JPEG
        (решает WebP/PNG с расширением .jpg, битые файлы и progressive JPEG,
        на которых mjpeg-decoder падает с "No JPEG data found");
      * битые/нечитаемые фото пропускаются, а не валят весь клип;
      * -nostdin, stdin=DEVNULL, timeout=180 — fail-fast вместо зависания.
    """
    if not _check_ffmpeg():
        logger.error("video: ffmpeg не найден в PATH")
        return False

    raw_existing = [Path(p) for p in photos if Path(p).exists()]
    if not raw_existing:
        logger.error("video: нет ни одного существующего фото")
        return False

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        try:
            output.unlink()
        except Exception:
            pass

    # === 1. Нормализация: любой формат → чистый baseline JPEG через Pillow ===
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="vk_slideshow_"))

    normalized: list[Path] = []
    for idx, src in enumerate(raw_existing):
        try:
            from PIL import Image, ImageOps
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode != "RGB":
                    im = im.convert("RGB")
                dst = tmp_dir / f"{idx:02d}_{src.stem}.jpg"
                im.save(dst, "JPEG", quality=92, optimize=False)
                normalized.append(dst)
                logger.info(
                    "video: нормализовано %s (%dx%d) → %s",
                    src.name, im.width, im.height, dst.name,
                )
        except Exception as e:
            logger.warning(
                "video: пропускаю нечитаемое фото %s: %s", src, e,
            )

    if not normalized:
        logger.error("video: ни одного валидного фото после нормализации")
        _cleanup_dir(tmp_dir)
        return False

    existing = normalized
    n = len(existing)
    fade = 0.5
    total_duration = n * duration_per_photo

    inputs: list[str] = []
    for p in existing:
        inputs += [
            "-loop", "1",
            "-framerate", str(fps),
            "-t", f"{duration_per_photo:.3f}",
            "-i", str(p),
        ]

    music_ok = bool(music_path and Path(music_path).exists())
    if music_ok:
        inputs += ["-i", str(music_path)]

    parts: list[str] = []
    for i in range(n):
        fade_out_st = max(0.0, duration_per_photo - fade)
        parts.append(
            f"[{i}:v]"
            f"fps={fps},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,"
            f"format=yuv420p,"
            f"trim=duration={duration_per_photo:.3f},"
            f"setpts=PTS-STARTPTS,"
            f"fade=t=in:st=0:d={fade},"
            f"fade=t=out:st={fade_out_st:.3f}:d={fade}"
            f"[v{i}]"
        )

    if n == 1:
        parts.append("[v0]copy[vout]")
    else:
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[vout]")

    filter_complex = ";".join(parts)

    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "warning",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
    ]

    if music_ok:
        cmd += [
            "-map", f"{n}:a",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
        ]

    cmd += [
        "-t", f"{total_duration:.3f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-movflags", "+faststart",
        str(output),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "video: ffmpeg TIMEOUT 180s (photos=%d, music=%s)",
            n, Path(music_path).name if music_ok else None,
        )
        _try_unlink_empty(output)
        _cleanup_dir(tmp_dir)
        return False
    except Exception as e:
        logger.exception("video: ffmpeg error: %s", e)
        _try_unlink_empty(output)
        _cleanup_dir(tmp_dir)
        return False

    _cleanup_dir(tmp_dir)

    if result.returncode != 0:
        logger.error(
            "video: ffmpeg rc=%d, stderr=%s",
            result.returncode, (result.stderr or "")[-1200:],
        )
        _try_unlink_empty(output)
        return False

    if not output.exists() or output.stat().st_size == 0:
        logger.error(
            "video: ffmpeg rc=0, но файл пустой (stderr=%s)",
            (result.stderr or "")[-800:],
        )
        _try_unlink_empty(output)
        return False

    logger.info(
        "video: создан %s (%.1f MB, %dx%d, %.1f сек, %d фото, %dfps)",
        output.name, output.stat().st_size / 1e6,
        width, height, total_duration, n, fps,
    )
    return True




def _try_unlink_empty(p: Path) -> None:
    """Удаляет файл, если он есть и нулевого размера."""
    try:
        if p.exists() and p.stat().st_size == 0:
            p.unlink()
    except Exception:
        pass


def _cleanup_dir(d: Path) -> None:
    """Удаляет временную папку с нормализованными фото."""
    try:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass