"""
Карточка товара в стиле Яндекс.Маркета (без верхней плашки).

Структура:
  1. Фото товара на весь кадр (кроме нижнего белого блока).
  2. Красное сердце в правом верхнем углу фото.
  3. Маленький тёмный бейдж (опц.) слева от сердца — напр. «24 шт. x 0,5».
  4. Красный бейдж «НИЖЕ РЫНКА» в левом нижнем углу фото.
  5. Белый блок внизу: название товара, зелёная цена + иконка Пэй, юр. текст.

Два формата:
  aspect='3:4'  — 1080×1440 для Telegram
  aspect='9:16' — 1080×1920 для VK Клипов
"""
import io
import logging
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# === Цвета ===
COLOR_WHITE = (255, 255, 255, 255)
COLOR_BLACK = (15, 15, 15, 255)
COLOR_TEXT_DARK = (26, 26, 26, 255)
COLOR_TEXT_LIGHT_GRAY = (170, 170, 170, 255)
COLOR_PRICE_GREEN = (0, 178, 87, 255)
COLOR_HEART_RED = (240, 60, 60, 255)
COLOR_BADGE_RED = (232, 60, 40, 255)          # «НИЖЕ РЫНКА»
COLOR_BADGE_SMALL_BG = (40, 40, 40, 220)      # тёмный полупрозрачный для «24 шт»


# ============================================================
# Утилиты
# ============================================================

def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _fmt_price(v: Optional[float]) -> str:
    if v is None:
        return ""
    try:
        return f"{int(v):,}".replace(",", " ") + " ₽"
    except (TypeError, ValueError):
        return ""


def _wrap(draw, text, font, max_w, max_lines=2):
    """Разбивает текст на строки, последнюю обрезает с …."""
    if not text:
        return []
    words = text.split()
    lines, current, consumed = [], "", 0
    for word in words:
        cand = (current + " " + word).strip()
        if draw.textlength(cand, font=font) <= max_w:
            current = cand
            consumed += 1
        else:
            if current:
                lines.append(current)
                if len(lines) >= max_lines:
                    break
            current = word
            consumed += 1
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and consumed < len(words):
        last = lines[-1]
        while draw.textlength(last + "…", font=font) > max_w and len(last) > 1:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _open_image(src):
    try:
        if isinstance(src, (bytes, bytearray)):
            return Image.open(io.BytesIO(src)).convert("RGB")
        return Image.open(src).convert("RGB")
    except Exception as e:
        logger.warning("overlay: не открыть источник: %s", e)
        return None


def _crop_center(img: Image.Image, w: int, h: int) -> Image.Image:
    src_ratio = img.width / img.height
    tgt_ratio = w / h
    if src_ratio > tgt_ratio:
        new_h = img.height
        new_w = int(new_h * tgt_ratio)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, new_h))
    else:
        new_w = img.width
        new_h = int(new_w / tgt_ratio)
        top = (img.height - new_h) // 2
        img = img.crop((0, top, new_w, top + new_h))
    return img.resize((w, h), Image.LANCZOS)


def _draw_heart(draw, cx, cy, size, color):
    """Рисует сердце в точке (cx, cy) с размером size."""
    r = size // 2
    draw.ellipse((cx - size, cy - size // 2, cx, cy + size // 2), fill=color)
    draw.ellipse((cx, cy - size // 2, cx + size, cy + size // 2), fill=color)
    draw.polygon(
        [(cx - size, cy), (cx + size, cy), (cx, cy + size)],
        fill=color,
    )


# ============================================================
# Основная функция рендера
# ============================================================

def render_product_card(
    src: Union[str, bytes, Path],
    title: str = "",
    brand: str = "",
    price_rub: Optional[float] = None,
    price_with_plus: Optional[float] = None,
    price_original_rub: Optional[float] = None,
    rating: Optional[float] = None,
    rating_count: Optional[int] = None,
    legal_text: str = "",
    small_badge: str = "",
    aspect: str = "3:4",
) -> Optional[bytes]:
    """
    Собирает карточку в стиле Яндекс.Маркета. Возвращает JPEG-байты.

    Верхней цветной плашки нет — фото занимает всю площадь, кроме
    нижнего белого блока.

    Параметры:
        small_badge — маленький бейдж справа сверху (напр. «24 шт. x 0,5»).
        aspect      — '3:4' (1080×1440) для Telegram или '9:16' (1080×1920) для VK.
    """
    if aspect == "9:16":
        W, H = 1080, 1920
        bottom_h = 440
    else:
        W, H = 1080, 1440
        bottom_h = 380

    img = _open_image(src)
    if img is None:
        return None

    canvas = Image.new("RGBA", (W, H), COLOR_WHITE)

    # ============ 1. Фото на весь кадр (кроме нижнего блока) ============
    photo_h = H - bottom_h
    photo = _crop_center(img, W, photo_h)
    canvas.paste(photo, (0, 0))

    draw = ImageDraw.Draw(canvas)

    # ============ 2. Сердце — красное, в правом верхнем углу фото ============
    heart_x = W - 90
    heart_y = 80
    _draw_heart(draw, heart_x, heart_y, 38, COLOR_HEART_RED)

    # ============ 3. Маленький бейдж слева от сердца ============
    if small_badge:
        font_small = _font(FONT_REGULAR, 30)
        bbox = draw.textbbox((0, 0), small_badge, font=font_small)
        bw = bbox[2] - bbox[0] + 30
        bh = bbox[3] - bbox[1] + 18
        bx = heart_x - 80 - bw
        by = 55
        _rounded_rect(draw, (bx, by, bx + bw, by + bh), 10, COLOR_BADGE_SMALL_BG)
        draw.text((bx + 15, by + 8), small_badge, font=font_small, fill=COLOR_WHITE)

    # ============ 4. Красный бейдж «НИЖЕ РЫНКА» на фото ============
    primary = price_with_plus if price_with_plus else price_rub
    old = price_original_rub
    if old and primary and old > primary:
        b_text = "НИЖЕ РЫНКА"
        font_b = _font(FONT_BOLD, 44)
        bbox = draw.textbbox((0, 0), b_text, font=font_b)
        bw = bbox[2] - bbox[0] + 44
        bh = bbox[3] - bbox[1] + 26
        bx = 45
        by = photo_h - bh - 35
        _rounded_rect(draw, (bx, by, bx + bw, by + bh), 10, COLOR_BADGE_RED)
        draw.text((bx + 22, by + 12), b_text, font=font_b, fill=COLOR_WHITE)

    # ============ 5. Нижний белый блок ============
    block_y = H - bottom_h

    # 5.1 Название товара — 2 строки, тёмный текст
    font_title = _font(FONT_REGULAR, 44 if aspect == "9:16" else 40)
    title_lines = _wrap(draw, title, font_title, W - 100, max_lines=2)
    y = block_y + 40
    for line in title_lines:
        draw.text((50, y), line, font=font_title, fill=COLOR_TEXT_DARK)
        y += int(font_title.size * 1.25)

    # 5.2 Цена — большая зелёная + иконка Пэй
    if primary is not None:
        y += 20
        font_price = _font(FONT_BOLD, 92 if aspect == "9:16" else 84)
        price_str = _fmt_price(primary)
        draw.text((50, y), price_str, font=font_price, fill=COLOR_PRICE_GREEN)
        price_w = draw.textlength(price_str, font=font_price)

        # Иконка «Пэй» — зелёная плашка с двумя белыми полосками
        if price_with_plus:
            icon_w, icon_h = 52, 36
            icon_x = 50 + price_w + 24
            icon_y = y + 30
            _rounded_rect(
                draw,
                (icon_x, icon_y, icon_x + icon_w, icon_y + icon_h),
                6, COLOR_PRICE_GREEN,
            )
            inner = 10
            draw.rectangle(
                (icon_x + inner, icon_y + 10,
                 icon_x + icon_w - inner, icon_y + 14),
                fill=COLOR_WHITE,
            )
            draw.rectangle(
                (icon_x + inner, icon_y + 22,
                 icon_x + icon_w - inner, icon_y + 26),
                fill=COLOR_WHITE,
            )
            font_pay = _font(FONT_REGULAR, 38)
            draw.text(
                (icon_x + icon_w + 12, icon_y + 2),
                "Пэй", font=font_pay, fill=COLOR_PRICE_GREEN,
            )

    # 5.3 Юр. текст — мелкий серый в самом низу
    if legal_text:
        font_legal = _font(FONT_REGULAR, 22)
        draw.text(
            (50, H - 32),
            legal_text[:90], font=font_legal, fill=COLOR_TEXT_LIGHT_GRAY,
        )

    # ============ 6. Экспорт ============
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


def render_and_save(
    src_path: str,
    dst_path: str,
    **kwargs,
) -> bool:
    """Рендер + сохранение в файл (для VK-видео)."""
    data = render_product_card(src_path, **kwargs)
    if not data:
        return False
    try:
        Path(dst_path).write_bytes(data)
        logger.info("overlay: сохранено %s", Path(dst_path).name)
        return True
    except Exception as e:
        logger.warning("overlay: не удалось записать %s: %s", dst_path, e)
        return False