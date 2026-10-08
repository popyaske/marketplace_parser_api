"""
Карточка товара в стиле Яндекс.Маркета.

Рендер 1:1 совпадает с scripts/card_tuner_old.py.
Параметры подобраны в тюнере и перенесены сюда как константы.

Структура:
  1. Фото с отступом от краёв, скруглённые углы.
  2. Красное сердечко (параметрическая кривая) в правом верхнем углу.
  3. Оранжевый бейдж «НИЖЕ РЫНКА» — левый нижний угол фото.
  4. Белый блок: название (2 строки, слева) + крупная цена + «Пэй».

Аспекты: 3:4 | 3:4.5 | 3:5 | 4:5 | 9:16
"""
import io
import logging
import math
import os
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


# ============================================================
# Шрифты
# ============================================================
FONTS_DIR = os.getenv("FONTS_DIR", "/app/fonts")

_YS_REGULAR     = f"{FONTS_DIR}/YSText-Regular.ttf"
_YS_BOLD        = f"{FONTS_DIR}/YSText-Bold.ttf"
_YS_BOLD_ITALIC = f"{FONTS_DIR}/YSText-BoldItalic.ttf"

_DEJAVU_REGULAR     = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_DEJAVU_BOLD        = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_DEJAVU_BOLD_ITALIC = "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"


def _pick_font(primary: str, fallback: str) -> str:
    return primary if Path(primary).exists() else fallback


FONT_REGULAR     = _pick_font(_YS_REGULAR,     _DEJAVU_REGULAR)
FONT_BOLD        = _pick_font(_YS_BOLD,        _DEJAVU_BOLD)
FONT_BOLD_ITALIC = _pick_font(_YS_BOLD_ITALIC, _DEJAVU_BOLD_ITALIC)

logger.info(
    "overlay: шрифты → regular=%s, bold=%s, italic=%s",
    Path(FONT_REGULAR).name, Path(FONT_BOLD).name, Path(FONT_BOLD_ITALIC).name,
)


# === Цвета ===
COLOR_WHITE          = (255, 255, 255, 255)
COLOR_BG             = (255, 255, 255, 255)
COLOR_TEXT_PRIMARY   = (25, 24, 23, 255)      # #191817
COLOR_TEXT_SECONDARY = (140, 138, 135, 255)
COLOR_PRICE_GREEN    = (38, 110, 56, 255)     # #266E38
COLOR_BADGE_ORANGE   = (255, 96, 47, 255)     # #FF602F
COLOR_HEART_RED      = (250, 62, 44, 255)     # #FA3E2C
COLOR_HEART_OUTLINE  = (255, 255, 255, 255)


# ============================================================
# Размеры (эталон 1080×1440) — из card_tuner_old.py
# ============================================================
BASE_H              = 1440

# Фото
BASE_PHOTO_H        = 1125
BASE_ROUND_RADIUS   = 81
BASE_PHOTO_MARGIN   = 40

# Название
BASE_TITLE_FONT     = 56
BASE_TITLE_LINE_H   = 1.14
BASE_TITLE_TOP_PAD  = 24
BASE_LEFT_PAD       = 56

# Цена
BASE_PRICE_FONT     = 94
BASE_PRICE_TOP_GAP  = 4
PRICE_ALIGN_LEFT    = True    # False → по центру

# Полоска «Пэй»
BASE_PAY_FONT       = 60
BASE_PAY_BAR_W      = 78
BASE_PAY_BAR_H      = 40
BASE_PAY_GAP_1      = 28
BASE_PAY_GAP_2      = 18

# Бейдж «НИЖЕ РЫНКА»
BASE_BADGE_FONT     = 57
BASE_BADGE_LEFT     = 37
BASE_BADGE_BOTTOM   = 108

# Сердечко
BASE_HEART_SIZE     = 42
BASE_HEART_OUTLINE  = 0        # 0 = без обводки
BASE_HEART_RIGHT    = 116
BASE_HEART_TOP      = 106


# ============================================================
# Утилиты (совпадают с тюнером)
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


def _crop_cover(img: Image.Image, w: int, h: int) -> Image.Image:
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


def _rounded_top_mask(w: int, h: int, radius: int) -> Image.Image:
    """Скругление только верхних углов (photo_margin = 0)."""
    mask = Image.new("L", (w, h), 255)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rectangle((0, 0, radius, radius), fill=0)
    mdraw.ellipse((0, 0, radius * 2, radius * 2), fill=255)
    mdraw.rectangle((w - radius, 0, w, radius), fill=0)
    mdraw.ellipse((w - radius * 2, 0, w, radius * 2), fill=255)
    return mask


def _heart_polygon(cx, cy, size, steps=200):
    """Ровное сердечко через параметрическую кривую."""
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t)
              - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * size / 16.0, cy + y * size / 16.0))
    return pts


def _draw_heart_smooth(draw, cx, cy, size, fill_color,
                       outline_color=None, outline_w=0):
    if outline_color and outline_w > 0:
        draw.polygon(_heart_polygon(cx, cy, size + outline_w),
                     fill=outline_color)
    draw.polygon(_heart_polygon(cx, cy, size), fill=fill_color)


def _draw_nizhe_rynka_badge(draw, x, y, font):
    text = "НИЖЕ РЫНКА"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x, pad_y = 30, 10
    badge_w = text_w + pad_x * 2
    badge_h = text_h + pad_y * 2
    radius = badge_h // 2
    _rounded_rect(draw, (x, y, x + badge_w, y + badge_h), radius, COLOR_BADGE_ORANGE)
    draw.text(
        (x + pad_x - bbox[0], y + pad_y - bbox[1]),
        text, font=font, fill=COLOR_WHITE,
    )
    return badge_w, badge_h


def _draw_pay_bar(draw, x, y, width, height, radius=4):
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=radius, fill=COLOR_PRICE_GREEN,
    )


# ============================================================
# Основная функция — 1:1 с render_card() в тюнере
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
    promo_badge: str = "",
    aspect: str = "3:4",
) -> Optional[bytes]:
    """3:4 | 3:4.5 | 3:5 | 4:5 | 9:16."""
    if aspect == "9:16":
        W, H = 1080, 1920
    elif aspect == "3:5":
        W, H = 1080, 1800
    elif aspect == "3:4.5":
        W, H = 1080, 1620
    elif aspect == "4:5":
        W, H = 1080, 1350
    else:
        W, H = 1080, 1440

    s = H / BASE_H

    photo_h        = int(BASE_PHOTO_H       * s)
    round_radius   = int(BASE_ROUND_RADIUS  * s)
    photo_margin   = int(BASE_PHOTO_MARGIN  * s)
    title_font_px  = int(BASE_TITLE_FONT    * s)
    title_top_pad  = int(BASE_TITLE_TOP_PAD * s)
    left_pad       = int(BASE_LEFT_PAD      * s)
    price_font_px  = int(BASE_PRICE_FONT    * s)
    price_top_gap  = int(BASE_PRICE_TOP_GAP * s)
    pay_font_px    = int(BASE_PAY_FONT      * s)
    pay_bar_w      = int(BASE_PAY_BAR_W     * s)
    pay_bar_h      = max(4, int(BASE_PAY_BAR_H * s))
    pay_gap_1      = int(BASE_PAY_GAP_1     * s)
    pay_gap_2      = int(BASE_PAY_GAP_2     * s)
    badge_font_px  = int(BASE_BADGE_FONT    * s)
    badge_left     = int(BASE_BADGE_LEFT    * s)
    badge_bottom   = int(BASE_BADGE_BOTTOM  * s)
    heart_size     = int(BASE_HEART_SIZE    * s)
    heart_outline  = int(BASE_HEART_OUTLINE * s)
    heart_right    = int(BASE_HEART_RIGHT   * s)
    heart_top      = int(BASE_HEART_TOP     * s)

    img = _open_image(src)
    if img is None:
        return None

    canvas = Image.new("RGB", (W, H), COLOR_BG)

    # ============================================================
    # 1. ФОТО
    # ============================================================
    photo_w = W - photo_margin * 2
    photo = _crop_cover(img, photo_w, photo_h)

    if photo_margin > 0:
        # Все 4 угла
        mask = Image.new("L", (photo_w, photo_h), 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.rounded_rectangle(
            (0, 0, photo_w, photo_h),
            radius=round_radius, fill=255,
        )
    else:
        mask = _rounded_top_mask(photo_w, photo_h, radius=round_radius)

    canvas.paste(photo, (photo_margin, photo_margin), mask)

    draw = ImageDraw.Draw(canvas)

    # ============================================================
    # 2. СЕРДЕЧКО (параметрическая кривая)
    # ============================================================
    _draw_heart_smooth(
        draw,
        cx=W - heart_right,
        cy=heart_top,
        size=heart_size,
        fill_color=COLOR_HEART_RED,
        outline_color=COLOR_HEART_OUTLINE if heart_outline > 0 else None,
        outline_w=heart_outline,
    )

    # ============================================================
    # 3. БЕЙДЖ «НИЖЕ РЫНКА»
    # ============================================================
    primary = price_with_plus if price_with_plus else price_rub
    old = price_original_rub
    if old and primary and old > primary:
        font_badge = _font(FONT_BOLD_ITALIC, badge_font_px)
        badge_y = photo_margin + photo_h - badge_bottom
        _draw_nizhe_rynka_badge(
            draw,
            x=photo_margin + badge_left,
            y=badge_y,
            font=font_badge,
        )

    # ============================================================
    # 4. БЕЛЫЙ БЛОК
    # ============================================================
    block_y = photo_margin + photo_h
    text_w = W - left_pad * 2

    # 4.1 Название
    font_title = _font(FONT_REGULAR, title_font_px)
    title_lines = _wrap(draw, title, font_title, text_w, max_lines=2)
    y = block_y + title_top_pad
    line_h = int(title_font_px * BASE_TITLE_LINE_H)
    for line in title_lines:
        draw.text((left_pad, y), line, font=font_title, fill=COLOR_TEXT_PRIMARY)
        y += line_h

    # 4.2 Цена + «Пэй»
    if primary is not None:
        y += price_top_gap
        font_price = _font(FONT_BOLD, price_font_px)
        price_str = _fmt_price(primary)
        price_w = draw.textlength(price_str, font=font_price)

        if price_with_plus:
            font_pay = _font(FONT_REGULAR, pay_font_px)
            pay_text = "Пэй"
            pay_w = draw.textlength(pay_text, font=font_pay)
            total_w = price_w + pay_gap_1 + pay_bar_w + pay_gap_2 + pay_w
        else:
            total_w = price_w

        start_x = left_pad if PRICE_ALIGN_LEFT else (W - total_w) / 2

        draw.text(
            (start_x, y), price_str,
            font=font_price, fill=COLOR_PRICE_GREEN,
        )

        if price_with_plus:
            bar_x = start_x + price_w + pay_gap_1
            bar_y = y + int(price_font_px * 0.45)
            _draw_pay_bar(draw, bar_x, bar_y, width=pay_bar_w, height=pay_bar_h)

            pay_x = bar_x + pay_bar_w + pay_gap_2
            pay_y = bar_y - int(pay_font_px * 0.30)
            draw.text(
                (pay_x, pay_y), pay_text,
                font=font_pay, fill=COLOR_PRICE_GREEN,
            )

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=92, optimize=True)
    return out.getvalue()


def render_and_save(src_path: str, dst_path: str, **kwargs) -> bool:
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