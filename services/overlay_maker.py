"""
overlay_maker.py — стилизация карточек товаров для Telegram / VK.

Для каждого маркетплейса — свой рендер (переносим из scripts/card_tuner.py):
  • render_ozon        — розовая цена + зачёркнутая + скидка, название, рейтинг
  • render_yandex      — название → зелёная цена + полоска «Пэй»
  • render_wb          — красная цена + кошелёк → зачёркнутая → бренд/название → рейтинг
  • render_aliexpress  — зачёркнутая + скидка → чёрная цена → бейджи → название

Публичный API:
  • render_product_card(img, marketplace=..., ...) -> Optional[bytes]
  • render_and_save(src_path, dst_path, marketplace=..., ...) -> bool

Все размеры внутри рендеров даны «для 1440px по высоте» и потом
масштабируются коэффициентом s = H / 1440. Поэтому одна и та же
карточка выглядит одинаково и в 3:4.5, и в 9:16.
"""
from __future__ import annotations

import io
import math
import os
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageDraw, ImageFont


# ============================================================
# Шрифты
# ============================================================
_FONTS_DIR = Path(os.getenv("FONTS_DIR") or (Path(__file__).resolve().parent.parent / "fonts"))

FONT_REGULAR     = str(_FONTS_DIR / "YSText-Regular.ttf")
FONT_BOLD        = str(_FONTS_DIR / "YSText-Bold.ttf")
FONT_BOLD_ITALIC = str(_FONTS_DIR / "YSText-BoldItalic.ttf")

# Фолбэки (Linux/Docker без YS Text)
_DEJAVU = "/usr/share/fonts/truetype/dejavu"
if not Path(FONT_REGULAR).exists():
    FONT_REGULAR = f"{_DEJAVU}/DejaVuSans.ttf"
if not Path(FONT_BOLD).exists():
    FONT_BOLD = f"{_DEJAVU}/DejaVuSans-Bold.ttf"
if not Path(FONT_BOLD_ITALIC).exists():
    FONT_BOLD_ITALIC = FONT_BOLD


# ============================================================
# Параметры вёрстки по маркетплейсам (1:1 с card_tuner.py)
# ============================================================
DEFAULTS = {
    "ozon": {
        "photo_h":         1086,
        "round_radius":    0,
        "photo_margin":    40,

        "price_font":      90,
        "price_top_pad":   12,
        "price_left_pad":  58,
        "old_price_font":  64,
        "discount_font":   50,
        "old_price_gap":   21,

        "title_font":      52,
        "title_line_h":    1.15,
        "title_top_pad":   0,
        "title_left_pad":  58,

        "rating_font":     44,
        "rating_top_pad":  7,
        "star_size":       28,

        "heart_size":      42,
        "heart_right":     116,
        "heart_top":       106,
    },
    "yandex": {
        "photo_h":         1125,
        "round_radius":    81,
        "photo_margin":    40,

        "title_font":      56,
        "title_line_h":    1.14,
        "title_top_pad":   24,
        "title_left_pad":  40,

        "price_font":      94,
        "price_top_gap":   4,

        "pay_font":        60,
        "pay_bar_w":       78,
        "pay_bar_h":       40,
        "pay_gap_1":       28,
        "pay_gap_2":       18,

        "heart_size":      42,
        "heart_right":     116,
        "heart_top":       106,
    },
    "wildberries": {
        "photo_h":         1131,
        "round_radius":    36,
        "photo_margin":    25,

        "price_font":      77,
        "price_top_pad":   23,
        "price_left_pad":  47,
        "old_price_font":  46,
        "old_price_gap":   30,

        "brand_font":      43,
        "title_font":      47,
        "title_line_h":    1.09,
        "title_top_pad":   14,
        "title_left_pad":  49,

        "rating_font":     45,
        "rating_top_pad":  21,
        "star_size":       28,

        "heart_size":      42,
        "heart_right":     116,
        "heart_top":       106,
    },
    "aliexpress": {
        "photo_h":         1101,
        "round_radius":    14,
        "photo_margin":    17,

        "old_price_font":  33,
        "discount_font":   34,
        "old_price_gap":   12,
        "old_row_top_pad": 22,

        "price_font":      60,
        "price_top_pad":   0,
        "price_left_pad":  47,

        "badge_font":      34,
        "badge_pad_x":     11,
        "badge_pad_y":     8,
        "badge_radius":    13,
        "badge_top_pad":   6,
        "badge_gap":       10,

        "title_font":      39,
        "title_line_h":    1.25,
        "title_top_pad":   12,
        "title_left_pad":  51,

        "heart_size":      42,
        "heart_right":     116,
        "heart_top":       106,
    },
}

BASE_H = 1440


# ============================================================
# Общие утилиты
# ============================================================

def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _open_image(src) -> Optional[Image.Image]:
    try:
        if isinstance(src, (bytes, bytearray)):
            return Image.open(io.BytesIO(src)).convert("RGB")
        if isinstance(src, Image.Image):
            return src.convert("RGB")
        return Image.open(src).convert("RGB")
    except Exception as e:
        print(f"overlay_maker: не открыть источник: {e}")
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


def _fmt_price(v) -> str:
    if v is None:
        return ""
    try:
        return f"{int(v):,}".replace(",", " ") + " ₽"
    except (TypeError, ValueError):
        return ""


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        n = float(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _wrap(draw, text, font, max_w, max_lines=2):
    """Перенос по словам + балансировка строк (выравнивание по ширине)."""
    if not text:
        return []
    words = text.split()
    if not words:
        return []

    lines, current = [], ""
    for word in words:
        cand = (current + " " + word).strip()
        if draw.textlength(cand, font=font) <= max_w:
            current = cand
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    if len(lines) <= max_lines:
        return lines

    lines = lines[:max_lines]

    for i in range(len(lines) - 1, 0, -1):
        prev, curr = lines[i - 1], lines[i]
        while True:
            prev_words = prev.split()
            if len(prev_words) <= 1:
                break
            if draw.textlength(curr + " " + prev_words[-1], font=font) > max_w:
                break
            curr = prev_words[-1] + " " + curr
            prev = " ".join(prev_words[:-1])
        lines[i - 1], lines[i] = prev, curr

    if len(words) > sum(len(l.split()) for l in lines):
        last = lines[-1]
        while draw.textlength(last + "…", font=font) > max_w and len(last) > 1:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"

    return lines


def _heart_polygon(cx, cy, size, steps=200):
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t)
              - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * size / 16.0, cy + y * size / 16.0))
    return pts


def _draw_star(draw, cx, cy, size, color):
    pts = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = size if i % 2 == 0 else size * 0.42
        pts.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    draw.polygon(pts, fill=color)


def _resolve_canvas_size(aspect: str) -> tuple[int, int]:
    if aspect == "9:16":
        return 1080, 1920
    if aspect == "3:5":
        return 1080, 1800
    if aspect == "3:4.5":
        return 1080, 1620
    if aspect == "4:5":
        return 1080, 1350
    return 1080, 1440


def _draw_promo_badge(draw, x, y, text, s, bg=(229, 57, 53, 255)):
    """Плашка (например «-35%») поверх фото."""
    font = _font(FONT_BOLD, int(38 * s))
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad_x = int(22 * s)
    pad_y = int(12 * s)
    w = tw + pad_x * 2
    h = th + pad_y * 2
    radius = int(14 * s)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=bg)
    draw.text(
        (x + pad_x - bbox[0], y + pad_y - bbox[1]),
        text, font=font, fill=(255, 255, 255, 255),
    )


# ============================================================
# Рендер: Ozon
# ============================================================

def render_ozon(img_src, params, data, aspect="3:4.5", W=None, H=None):
    """Розовая цена + зачёркнутая + скидка → название → ★ рейтинг."""
    if W is None or H is None:
        W, H = _resolve_canvas_size(aspect)

    s = H / BASE_H
    p = params

    photo_h       = int(p["photo_h"]        * s)
    round_radius  = int(p["round_radius"]   * s)
    photo_margin  = int(p["photo_margin"]   * s)
    price_font_px = int(p["price_font"]     * s)
    price_top_pad = int(p["price_top_pad"]  * s)
    old_font_px   = int(p["old_price_font"] * s)
    disc_font_px  = int(p["discount_font"]  * s)
    old_gap       = int(p["old_price_gap"]  * s)
    title_font_px = int(p["title_font"]     * s)
    title_top_pad = int(p["title_top_pad"]  * s)
    left_pad      = int(p["title_left_pad"] * s)
    rating_font_px = int(p["rating_font"]   * s)
    rating_top_pad = int(p["rating_top_pad"] * s)
    star_size     = int(p["star_size"]      * s)
    heart_size    = int(p["heart_size"]     * s)
    heart_right   = int(p["heart_right"]    * s)
    heart_top     = int(p["heart_top"]      * s)
    price_left_pad = int(p["price_left_pad"] * s)

    COLOR_PRICE_OZON = (241, 17, 126, 255)
    COLOR_HEART_RED  = (248, 16, 75, 255)
    COLOR_STAR_GOLD  = (255, 168, 0, 255)
    COLOR_TEXT_PRIMARY   = (25, 24, 23, 255)
    COLOR_TEXT_SECONDARY = (140, 138, 135, 255)

    img = _open_image(img_src)
    if img is None:
        return None

    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    photo_w = W - photo_margin * 2
    photo = _crop_cover(img, photo_w, photo_h)
    mask = Image.new("L", (photo_w, photo_h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle((0, 0, photo_w, photo_h), radius=round_radius, fill=255)
    canvas.paste(photo, (photo_margin, photo_margin), mask)

    draw = ImageDraw.Draw(canvas)

    # Сердечко
    draw.polygon(_heart_polygon(W - heart_right, heart_top, heart_size),
                 fill=COLOR_HEART_RED)

    y = photo_margin + photo_h
    text_w = W - left_pad * 2

    # --- Цена ---
    price_val = _to_float(data.get("price_rub")) or _to_float(data.get("price_with_plus"))
    price_str = _fmt_price(price_val) if price_val else "—"

    y += price_top_pad
    font_price = _font(FONT_BOLD, price_font_px)
    draw.text((price_left_pad, y), price_str, font=font_price, fill=COLOR_PRICE_OZON)
    price_w = draw.textlength(price_str, font=font_price)

    bbox_main = draw.textbbox((price_left_pad, y), price_str, font=font_price)
    center_y = (bbox_main[1] + bbox_main[3]) // 2

    # Старая цена + скидка
    old = _to_float(data.get("price_original_rub"))
    if old and price_val and old > price_val:
        font_old = _font(FONT_REGULAR, old_font_px)
        font_disc = _font(FONT_BOLD, disc_font_px)
        old_str = _fmt_price(old)
        disc_pct = int(round((old - price_val) / old * 100))

        x_old = price_left_pad + int(price_w) + old_gap
        draw.text((x_old, center_y), old_str, font=font_old,
                  fill=COLOR_TEXT_SECONDARY, anchor="lm")
        bbox_old = draw.textbbox((x_old, center_y), old_str, font=font_old, anchor="lm")
        draw.line([(bbox_old[0], center_y), (bbox_old[2], center_y)],
                  fill=COLOR_TEXT_SECONDARY, width=3)

        x_disc = bbox_old[2] + old_gap
        draw.text((x_disc, center_y), f"−{disc_pct}%", font=font_disc,
                  fill=COLOR_PRICE_OZON, anchor="lm")

    y += int(price_font_px * 1.25)

    # --- Название ---
    y += title_top_pad
    font_title = _font(FONT_REGULAR, title_font_px)
    title_lines = _wrap(draw, data.get("title") or "", font_title, text_w, max_lines=2)
    line_h = int(title_font_px * p["title_line_h"])
    for line in title_lines:
        draw.text((left_pad, y), line, font=font_title, fill=COLOR_TEXT_PRIMARY)
        y += line_h

    # --- Рейтинг ---
    rating = _to_float(data.get("rating"))
    if rating:
        y += rating_top_pad
        font_rating = _font(FONT_REGULAR, rating_font_px)
        star_cx = left_pad + star_size
        star_cy = y + rating_font_px // 2
        _draw_star(draw, star_cx, star_cy, star_size, COLOR_STAR_GOLD)

        rating_str = f"{rating:.1f}".replace(".", ",")
        x_rating = left_pad + star_size * 2 + 10
        draw.text((x_rating, y), rating_str, font=font_rating, fill=COLOR_TEXT_PRIMARY)

        rc = data.get("rating_count")
        if rc:
            reviews_str = f"· {int(rc):,}".replace(",", " ") + " отзывов"
            x_rev = x_rating + int(draw.textlength(rating_str, font=font_rating)) + 15
            draw.text((x_rev, y), reviews_str, font=font_rating, fill=COLOR_TEXT_SECONDARY)
        y += int(rating_font_px * 1.25)


    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


# ============================================================
# Рендер: Yandex
# ============================================================

def render_yandex(img_src, params, data, aspect="3:4.5", W=None, H=None):
    """Название → зелёная цена (Пэй) + полоска «Пэй»."""
    if W is None or H is None:
        W, H = _resolve_canvas_size(aspect)

    s = H / BASE_H
    p = params

    photo_h       = int(p["photo_h"]        * s)
    round_radius  = int(p["round_radius"]   * s)
    photo_margin  = int(p["photo_margin"]   * s)
    title_font_px = int(p["title_font"]     * s)
    title_top_pad = int(p["title_top_pad"]  * s)
    left_pad      = int(p["title_left_pad"] * s)
    price_font_px = int(p["price_font"]     * s)
    price_top_gap = int(p["price_top_gap"]  * s)
    pay_font_px   = int(p["pay_font"]       * s)
    pay_bar_w     = int(p["pay_bar_w"]      * s)
    pay_bar_h     = max(4, int(p["pay_bar_h"] * s))
    pay_gap_1     = int(p["pay_gap_1"]      * s)
    pay_gap_2     = int(p["pay_gap_2"]      * s)
    heart_size    = int(p["heart_size"]     * s)
    heart_right   = int(p["heart_right"]    * s)
    heart_top     = int(p["heart_top"]      * s)

    COLOR_PRICE_GREEN  = (38, 110, 56, 255)
    COLOR_HEART_RED    = (250, 62, 44, 255)
    COLOR_TEXT_PRIMARY = (25, 24, 23, 255)

    img = _open_image(img_src)
    if img is None:
        return None

    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    photo_w = W - photo_margin * 2
    photo = _crop_cover(img, photo_w, photo_h)
    mask = Image.new("L", (photo_w, photo_h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle((0, 0, photo_w, photo_h), radius=round_radius, fill=255)
    canvas.paste(photo, (photo_margin, photo_margin), mask)

    draw = ImageDraw.Draw(canvas)

    draw.polygon(_heart_polygon(W - heart_right, heart_top, heart_size),
                 fill=COLOR_HEART_RED)

    # Название
    y = photo_margin + photo_h + title_top_pad
    text_w = W - left_pad * 2
    font_title = _font(FONT_REGULAR, title_font_px)
    title_lines = _wrap(draw, data.get("title") or "", font_title, text_w, max_lines=2)
    line_h = int(title_font_px * p["title_line_h"])
    for line in title_lines:
        draw.text((left_pad, y), line, font=font_title, fill=COLOR_TEXT_PRIMARY)
        y += line_h

    # Цена: зелёная = Пэй-цена (или обычная, если Пэй нет)
    green = _to_float(data.get("price_with_plus")) or _to_float(data.get("price_rub"))
    green_str = _fmt_price(green) if green else "—"

    y += price_top_gap
    font_price = _font(FONT_BOLD, price_font_px)
    draw.text((left_pad, y), green_str, font=font_price, fill=COLOR_PRICE_GREEN)
    price_w = draw.textlength(green_str, font=font_price)

    # Полоска «Пэй» — только если у нас реально Пэй-цена
    if _to_float(data.get("price_with_plus")):
        font_pay = _font(FONT_REGULAR, pay_font_px)
        bar_x = left_pad + int(price_w) + pay_gap_1
        bar_y = y + int(price_font_px * 0.45)
        draw.rounded_rectangle(
            (bar_x, bar_y, bar_x + pay_bar_w, bar_y + pay_bar_h),
            radius=4, fill=COLOR_PRICE_GREEN,
        )
        pay_x = bar_x + pay_bar_w + pay_gap_2
        pay_y = bar_y - int(pay_font_px * 0.30)
        draw.text((pay_x, pay_y), "Пэй", font=font_pay, fill=COLOR_PRICE_GREEN)

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


# ============================================================
# Рендер: Wildberries
# ============================================================

def render_wb(img_src, params, data, aspect="3:4.5", W=None, H=None):
    """Красная цена + кошелёк → зачёркнутая → бренд/название → ★ рейтинг."""
    if W is None or H is None:
        W, H = _resolve_canvas_size(aspect)

    s = H / BASE_H
    p = params

    photo_h       = int(p["photo_h"]        * s)
    round_radius  = int(p["round_radius"]   * s)
    photo_margin  = int(p["photo_margin"]   * s)
    price_font_px = int(p["price_font"]     * s)
    price_top_pad = int(p["price_top_pad"]  * s)
    price_left_pad = int(p["price_left_pad"] * s)
    old_font_px   = int(p["old_price_font"] * s)
    old_gap       = int(p["old_price_gap"]  * s)
    brand_font_px = int(p["brand_font"]     * s)
    title_font_px = int(p["title_font"]     * s)
    title_top_pad = int(p["title_top_pad"]  * s)
    left_pad      = int(p["title_left_pad"] * s)
    rating_font_px = int(p["rating_font"]   * s)
    rating_top_pad = int(p["rating_top_pad"] * s)
    star_size     = int(p["star_size"]      * s)
    heart_size    = int(p["heart_size"]     * s)
    heart_right   = int(p["heart_right"]    * s)
    heart_top     = int(p["heart_top"]      * s)

    COLOR_PRICE_WB       = (255, 51, 51, 255)
    COLOR_HEART_WB       = (255, 51, 51, 255)
    COLOR_STAR_GOLD      = (255, 168, 0, 255)
    COLOR_TEXT_PRIMARY   = (25, 24, 23, 255)
    COLOR_TEXT_SECONDARY = (140, 138, 135, 255)
    COLOR_BRAND          = (60, 100, 180, 255)

    img = _open_image(img_src)
    if img is None:
        return None

    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    photo_w = W - photo_margin * 2
    photo = _crop_cover(img, photo_w, photo_h)
    mask = Image.new("L", (photo_w, photo_h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle((0, 0, photo_w, photo_h), radius=round_radius, fill=255)
    canvas.paste(photo, (photo_margin, photo_margin), mask)

    draw = ImageDraw.Draw(canvas)

    draw.polygon(_heart_polygon(W - heart_right, heart_top, heart_size),
                 fill=COLOR_HEART_WB)

    y = photo_margin + photo_h

    # --- Цена с кошельком ---
    y += price_top_pad
    price_val = _to_float(data.get("price_rub")) or _to_float(data.get("price_with_plus"))
    price_str = _fmt_price(price_val) if price_val else "—"

    font_price = _font(FONT_BOLD, price_font_px)
    bbox_main = draw.textbbox((price_left_pad, y), price_str, font=font_price)
    center_y = (bbox_main[1] + bbox_main[3]) // 2

    icon_size = int(price_font_px * 0.60)
    icon_x = price_left_pad
    icon_y = center_y - icon_size // 2
    body_h = int(icon_size * 0.75)
    body_y = icon_y + (icon_size - body_h) // 2
    draw.rounded_rectangle(
        (icon_x, body_y, icon_x + icon_size, body_y + body_h),
        radius=3, fill=COLOR_PRICE_WB,
    )
    clip_r = int(body_h * 0.30)
    clip_cx = icon_x + icon_size - int(icon_size * 0.15)
    clip_cy = body_y + body_h // 2
    draw.ellipse(
        (clip_cx - clip_r, clip_cy - clip_r,
         clip_cx + clip_r, clip_cy + clip_r),
        fill=(255, 255, 255), outline=COLOR_PRICE_WB, width=2,
    )

    x_price = icon_x + icon_size + int(price_font_px * 0.20)
    draw.text((x_price, center_y), price_str, font=font_price,
              fill=COLOR_PRICE_WB, anchor="lm")
    price_w = draw.textlength(price_str, font=font_price)

    bbox_main = draw.textbbox((x_price, y), price_str, font=font_price)
    center_y = (bbox_main[1] + bbox_main[3]) // 2

    # --- Старая цена ---
    old = _to_float(data.get("price_original_rub"))
    if old and price_val and old > price_val:
        font_old = _font(FONT_REGULAR, old_font_px)
        old_str = _fmt_price(old)
        x_old = x_price + int(price_w) + old_gap
        draw.text((x_old, center_y), old_str, font=font_old,
                  fill=COLOR_TEXT_SECONDARY, anchor="lm")
        bbox_old = draw.textbbox((x_old, center_y), old_str,
                                 font=font_old, anchor="lm")
        draw.line([(bbox_old[0], center_y), (bbox_old[2], center_y)],
                  fill=COLOR_TEXT_SECONDARY, width=2)

    y += int(price_font_px * 1.25)

    # --- Бренд / Название ---
    y += title_top_pad
    text_w = W - left_pad * 2

    font_brand = _font(FONT_REGULAR, brand_font_px)
    font_title = _font(FONT_REGULAR, title_font_px)

    # Синяя галочка
    check_size = int(brand_font_px * 0.75)
    check_x = left_pad
    check_y = y + int(brand_font_px * 0.15)
    draw.ellipse((check_x, check_y, check_x + check_size, check_y + check_size),
                 fill=COLOR_BRAND)
    cx = check_x + check_size // 2
    cy = check_y + check_size // 2
    draw.line(
        [(cx - check_size * 0.25, cy),
         (cx - check_size * 0.05, cy + check_size * 0.20),
         (cx + check_size * 0.25, cy - check_size * 0.20)],
        fill=(255, 255, 255), width=2,
    )

    brand_str = (data.get("brand") or "").strip()
    name_str = (data.get("title") or "").strip()

    if brand_str:
        x_brand = check_x + check_size + 8
        draw.text((x_brand, y), brand_str, font=font_brand,
                  fill=COLOR_TEXT_PRIMARY)
        brand_w = draw.textlength(brand_str, font=font_brand)

        if name_str:
            x_slash = x_brand + int(brand_w) + 8
            draw.text((x_slash, y), "/", font=font_title,
                      fill=COLOR_TEXT_SECONDARY)
            slash_w = draw.textlength("/", font=font_title)

            x_title = x_slash + int(slash_w) + 8
            remaining_w = W - x_title - left_pad
            title_lines = _wrap(draw, name_str, font_title, remaining_w, max_lines=1)
            if title_lines:
                draw.text((x_title, y), title_lines[0],
                          font=font_title, fill=COLOR_TEXT_SECONDARY)
    else:
        # Нет бренда — только название, две строки
        title_lines = _wrap(draw, name_str, font_title, text_w, max_lines=2)
        line_h = int(title_font_px * p["title_line_h"])
        for line in title_lines:
            draw.text((left_pad, y), line, font=font_title,
                      fill=COLOR_TEXT_PRIMARY)
            y += line_h

    y += int(max(brand_font_px, title_font_px) * 1.25)

    # --- Рейтинг ---
    rating = _to_float(data.get("rating"))
    if rating:
        y += rating_top_pad
        font_rating = _font(FONT_REGULAR, rating_font_px)
        star_cx = left_pad + star_size
        star_cy = y + rating_font_px // 2
        _draw_star(draw, star_cx, star_cy, star_size, COLOR_STAR_GOLD)

        rating_str = f"{rating:.1f}".replace(".", ",")
        x_rating = left_pad + star_size * 2 + 10
        draw.text((x_rating, y), rating_str, font=font_rating,
                  fill=COLOR_TEXT_PRIMARY)

        rc = data.get("rating_count")
        if rc:
            reviews_str = f"· {int(rc):,}".replace(",", " ") + " оценок"
            x_rev = x_rating + int(draw.textlength(rating_str, font=font_rating)) + 12
            draw.text((x_rev, y), reviews_str, font=font_rating,
                      fill=COLOR_TEXT_SECONDARY)
        y += int(rating_font_px * 1.25)

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


# ============================================================
# Рендер: AliExpress
# ============================================================

def render_aliexpress(img_src, params, data, aspect="3:4.5", W=None, H=None):
    """Зачёркнутая + скидка → чёрная цена → бейджи → название."""
    if W is None or H is None:
        W, H = _resolve_canvas_size(aspect)

    s = H / BASE_H
    p = params

    photo_h         = int(p["photo_h"]        * s)
    round_radius    = int(p["round_radius"]   * s)
    photo_margin    = int(p["photo_margin"]   * s)
    old_font_px     = int(p["old_price_font"] * s)
    disc_font_px    = int(p["discount_font"]  * s)
    old_gap         = int(p["old_price_gap"]  * s)
    old_top_pad     = int(p["old_row_top_pad"] * s)
    price_font_px   = int(p["price_font"]     * s)
    price_top_pad   = int(p["price_top_pad"]  * s)
    price_left_pad  = int(p["price_left_pad"] * s)
    badge_font_px   = int(p["badge_font"]     * s)
    badge_pad_x     = int(p["badge_pad_x"]    * s)
    badge_pad_y     = int(p["badge_pad_y"]    * s)
    badge_radius    = int(p["badge_radius"]   * s)
    badge_top_pad   = int(p["badge_top_pad"]  * s)
    badge_gap       = int(p["badge_gap"]      * s)
    title_font_px   = int(p["title_font"]     * s)
    title_top_pad   = int(p["title_top_pad"]  * s)
    left_pad        = int(p["title_left_pad"] * s)
    heart_size      = int(p["heart_size"]     * s)
    heart_right     = int(p["heart_right"]    * s)
    heart_top       = int(p["heart_top"]      * s)

    COLOR_TEXT_PRIMARY   = (24, 24, 27, 255)
    COLOR_TEXT_SECONDARY = (137, 137, 147, 255)
    COLOR_RATING_BG      = (255, 230, 212, 255)
    COLOR_RATING_TEXT    = (255, 108, 2, 255)
    COLOR_BUYERS_BG      = (243, 246, 249, 255)
    COLOR_BUYERS_TEXT    = (24, 24, 27, 255)
    COLOR_HEART_RED      = (255, 71, 71, 255)
    COLOR_STAR_GOLD      = (255, 168, 0, 255)

    img = _open_image(img_src)
    if img is None:
        return None

    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    photo_w = W - photo_margin * 2
    photo = _crop_cover(img, photo_w, photo_h)
    mask = Image.new("L", (photo_w, photo_h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle((0, 0, photo_w, photo_h),
                            radius=round_radius, fill=255)
    canvas.paste(photo, (photo_margin, photo_margin), mask)

    draw = ImageDraw.Draw(canvas)

    draw.polygon(_heart_polygon(W - heart_right, heart_top, heart_size),
                 fill=COLOR_HEART_RED)

    y = photo_margin + photo_h
    text_w = W - left_pad * 2

    price_val = _to_float(data.get("price_rub")) or _to_float(data.get("price_with_plus"))
    old = _to_float(data.get("price_original_rub"))

    # === 1. Старая цена + скидка ===
    if old and price_val and old > price_val:
        y += old_top_pad
        font_old  = _font(FONT_REGULAR, old_font_px)
        font_disc = _font(FONT_REGULAR, disc_font_px)

        x = price_left_pad
        old_str = _fmt_price(old)
        draw.text((x, y), old_str, font=font_old, fill=COLOR_TEXT_SECONDARY)
        bbox_old = draw.textbbox((x, y), old_str, font=font_old)
        strike_y = (bbox_old[1] + bbox_old[3]) // 2
        draw.line([(bbox_old[0], strike_y), (bbox_old[2], strike_y)],
                  fill=COLOR_TEXT_SECONDARY, width=2)

        x_disc = bbox_old[2] + old_gap
        disc_pct = int(round((old - price_val) / old * 100))
        draw.text((x_disc, y), f"-{disc_pct}%",
                  font=font_disc, fill=COLOR_TEXT_PRIMARY)

        y += int(old_font_px * 1.25)

    # === 2. Цена ===
    y += price_top_pad
    font_price = _font(FONT_BOLD, price_font_px)
    price_str = _fmt_price(price_val) if price_val else "—"
    draw.text((price_left_pad, y), price_str,
              font=font_price, fill=COLOR_TEXT_PRIMARY)
    y += int(price_font_px * 1.25)

    # === 3. Бейджи ★ и «N купили» ===
    rating = _to_float(data.get("rating"))
    rating_count = data.get("rating_count")
    has_badges = bool(rating) or bool(rating_count)

    if has_badges:
        y += badge_top_pad
        font_badge = _font(FONT_REGULAR, badge_font_px)

        x_cursor = price_left_pad
        badge_h = 0

        # Бейдж рейтинга
        if rating:
            rating_text = f"{rating:.1f}"
            bbox = draw.textbbox((0, 0), rating_text, font=font_badge)
            rating_text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            star_size = int(badge_font_px * 0.9)
            star_gap = 4
            rating_w = badge_pad_x * 2 + star_size + star_gap + rating_text_w
            rating_h = text_h + badge_pad_y * 2

            draw.rounded_rectangle(
                (x_cursor, y, x_cursor + rating_w, y + rating_h),
                radius=badge_radius, fill=COLOR_RATING_BG,
            )
            star_cx = x_cursor + badge_pad_x + star_size // 2
            star_cy = y + rating_h // 2
            _draw_star(draw, star_cx, star_cy, star_size // 2, COLOR_STAR_GOLD)

            x_text = x_cursor + badge_pad_x + star_size + star_gap
            y_text = y + badge_pad_y - bbox[1]
            draw.text((x_text, y_text), rating_text,
                      font=font_badge, fill=COLOR_RATING_TEXT)

            x_cursor += rating_w + badge_gap
            badge_h = rating_h

        # Бейдж «N купили»
        if rating_count:
            buyers_text = f"{int(rating_count):,}".replace(",", " ") + " купили"
            bbox_b = draw.textbbox((0, 0), buyers_text, font=font_badge)
            buyers_w = (bbox_b[2] - bbox_b[0]) + badge_pad_x * 2
            buyers_h = (bbox_b[3] - bbox_b[1]) + badge_pad_y * 2

            draw.rounded_rectangle(
                (x_cursor, y, x_cursor + buyers_w, y + buyers_h),
                radius=badge_radius, fill=COLOR_BUYERS_BG,
            )
            draw.text(
                (x_cursor + badge_pad_x - bbox_b[0],
                 y + badge_pad_y - bbox_b[1]),
                buyers_text, font=font_badge, fill=COLOR_BUYERS_TEXT,
            )
            badge_h = max(badge_h, buyers_h)

        y += badge_h

    # === 4. Название ===
    y += title_top_pad
    font_title = _font(FONT_REGULAR, title_font_px)
    title_lines = _wrap(draw, data.get("title") or "",
                        font_title, text_w, max_lines=2)
    line_h = int(title_font_px * p["title_line_h"])
    for line in title_lines:
        draw.text((left_pad, y), line, font=font_title, fill=COLOR_TEXT_PRIMARY)
        y += line_h

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


# ============================================================
# Диспетчер
# ============================================================

RENDERERS = {
    "ozon":        render_ozon,
    "yandex":      render_yandex,
    "wildberries": render_wb,
    "aliexpress":  render_aliexpress,
}


def render_product_card(
    img: Union[bytes, bytearray, Image.Image],
    *,
    marketplace: str = "yandex",
    title: str = "",
    brand: str = "",
    price_rub: Optional[float] = None,
    price_with_plus: Optional[float] = None,
    price_original_rub: Optional[float] = None,
    rating: Optional[float] = None,
    rating_count: Optional[int] = None,
    legal_text: str = "",
    promo_badge: str = "",
    aspect: str = "3:4.5",
) -> Optional[bytes]:
    """Отрисовать стилизованную карточку и вернуть JPEG-байты (или None)."""
    mp = (marketplace or "yandex").lower()
    render_fn = RENDERERS.get(mp, render_yandex)
    params = DEFAULTS.get(mp, DEFAULTS["yandex"])

    data = {
        "title": title,
        "brand": brand,
        "price_rub": price_rub,
        "price_with_plus": price_with_plus,
        "price_original_rub": price_original_rub,
        "rating": rating,
        "rating_count": rating_count,
        "legal_text": legal_text,
        "promo_badge": promo_badge,
    }

    return render_fn(img, params, data, aspect=aspect)


def render_and_save(
    src_path: str,
    dst_path: str,
    *,
    marketplace: str = "yandex",
    title: str = "",
    brand: str = "",
    price_rub: Optional[float] = None,
    price_with_plus: Optional[float] = None,
    price_original_rub: Optional[float] = None,
    rating: Optional[float] = None,
    rating_count: Optional[int] = None,
    legal_text: str = "",
    promo_badge: str = "",
    aspect: str = "3:4.5",
) -> bool:
    """Прочитать src_path, отрисовать карточку, записать dst_path. True — успех."""
    try:
        with open(src_path, "rb") as f:
            raw = f.read()
        out = render_product_card(
            raw,
            marketplace=marketplace,
            title=title, brand=brand,
            price_rub=price_rub,
            price_with_plus=price_with_plus,
            price_original_rub=price_original_rub,
            rating=rating, rating_count=rating_count,
            legal_text=legal_text,
            promo_badge=promo_badge,
            aspect=aspect,
        )
        if not out:
            return False
        dst = Path(dst_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(out)
        return True
    except Exception as e:
        print(f"render_and_save: {e}")
        return False