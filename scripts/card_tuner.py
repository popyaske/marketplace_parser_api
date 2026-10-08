"""
Универсальный тюнер карточки товара.

Сверху — выбор маркетплейса. У каждого свои параметры и свой рендер.
Пока поддерживаются: Ozon, Yandex. WB и AliExpress — заготовки.

Запуск:
    python scripts/card_tuner.py scripts/test.jpg
    python scripts/card_tuner.py C:\\path\\to\\photo.jpg
"""
import io
import math
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageDraw, ImageFont, ImageTk


# ============================================================
# Пути и импорт overlay_maker (для шрифтов)
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_LOCAL_FONTS_DIR = Path(os.getenv("FONTS_DIR") or (PROJECT_ROOT / "fonts"))
os.environ["FONTS_DIR"] = str(_LOCAL_FONTS_DIR)

import services.overlay_maker as om  # noqa: E402


# ============================================================
# Список маркетплейсов и их рендеры
# ============================================================
MARKETPLACES = ["ozon", "yandex", "wildberries", "aliexpress"]
# WB и AliExpress — заготовки, рендер пока как у Яндекса (или пусто)


# ============================================================
# Пресеты размеров
# ============================================================
SIZE_PRESETS = [
    ("1080×1080  (1:1)",     1080, 1080),
    ("1080×1350  (4:5)",     1080, 1350),
    ("1080×1440  (3:4)",     1080, 1440),
    ("1080×1620  (3:4.5)",   1080, 1620),
    ("1080×1800  (3:5)",     1080, 1800),
    ("1080×1920  (9:16)",    1080, 1920),
    ("1200×1600  (3:4 HD)",  1200, 1600),
    ("1440×1920  (3:4 FHD)", 1440, 1920),
]
SIZE_DEFAULT_IDX = 3  # 1080×1620 (3:4.5)


# ============================================================
# Дефолты по маркетплейсам
# ============================================================
DEFAULTS = {
    "ozon": {
        # Фото
        "photo_h":            1086,
        "round_radius":       0,
        "photo_margin":       40,

        # Цена (сверху блока)
        "price_font":         90,
        "price_top_pad":      12,
        "price_left_pad":     58,
        "old_price_font":     64,
        "discount_font":      50,
        "old_price_gap":      21,

        # Название (title_top_pad = gap от цены до названия)
        "title_font":         52,
        "title_line_h":       1.15,
        "title_top_pad":      0,
        "title_left_pad":     58,

        # Рейтинг
        "rating_font":        44,
        "rating_top_pad":     7,
        "star_size":          28,

        # Сердечко
        "heart_size":         42,
        "heart_right":        116,
        "heart_top":          106,
    },
    "yandex": {
        # Фото
        "photo_h":            1125,
        "round_radius":       81,
        "photo_margin":       40,

        # Название
        "title_font":         56,
        "title_line_h":       1.14,
        "title_top_pad":      24,
        "title_left_pad":     40,

        # Цена
        "price_font":         94,
        "price_top_gap":      4,

        # Пэй
        "pay_font":           60,
        "pay_bar_w":          78,
        "pay_bar_h":          40,
        "pay_gap_1":          28,
        "pay_gap_2":          18,

        # Сердечко
        "heart_size":         42,
        "heart_right":        116,
        "heart_top":          106,
    },
            "wildberries": {
        # Фото
        "photo_h":            1131,
        "round_radius":       36,
        "photo_margin":       25,

        # Цена
        "price_font":         77,
        "price_top_pad":      23,
        "price_left_pad":     47,
        "old_price_font":     46,
        "old_price_gap":      30,

        # Бренд + название
        "brand_font":         43,
        "title_font":         47,
        "title_line_h":       1.09,
        "title_top_pad":      14,
        "title_left_pad":     49,

        # Рейтинг
        "rating_font":        45,
        "rating_top_pad":     21,
        "star_size":          28,

        # Сердечко
        "heart_size":         42,
        "heart_right":        116,
        "heart_top":          106,
    },
    "aliexpress": {
        # Фото
        "photo_h":            1086,
        "round_radius":       36,
        "photo_margin":       40,

        # Старая цена + скидка (мелким, сверху)
        "old_price_font":     40,
        "discount_font":      40,
        "old_price_gap":      15,
        "old_row_top_pad":    25,

        # Цена (чёрная)
        "price_font":         90,
        "price_top_pad":      5,
        "price_left_pad":     58,

        # Бейджи рейтинга и покупателей
        "badge_font":         36,
        "badge_pad_x":        12,
        "badge_pad_y":        6,
        "badge_radius":       8,
        "badge_top_pad":      15,
        "badge_gap":          10,

        # Название
        "title_font":         45,
        "title_line_h":       1.15,
        "title_top_pad":      15,
        "title_left_pad":     58,

        # Доставка (зелёная)
        "delivery_font":      38,
        "delivery_top_pad":   12,

        # Сердечко
        "heart_size":         42,
        "heart_right":        116,
        "heart_top":          106,
    },
}


# Диапазоны ползунков по параметрам (один набор для всех)
SLIDER_RANGES = {
    # Фото
    "photo_h":         (300, 1600, 10),
    "round_radius":    (0,   120,  2),
    "photo_margin":    (0,   80,   2),
    # Цена
    "price_font":      (60,  220,  5),
    "price_top_pad":   (0,   200,  5),
    "price_left_pad":  (0, 200, 2),
    "price_top_gap":   (0,   200,  5),
    "old_price_font":  (20,  100,  2),
    "discount_font":   (20,  100,  2),
    "old_price_gap":   (0,   100,  2),
    # Название
    "title_font":      (30,  120,  2),
    "title_line_h":    (0.8, 2.0,  0.02),
    "title_top_pad":   (0,   200,  5),
    "title_left_pad":  (0,   200,  2),
    "brand_font":      (20,  100,  2),
    # AliExpress
    "old_row_top_pad": (0,   100,  2),
    "badge_font":      (20,  80,   2),
    "badge_pad_x":     (0,   40,   1),
    "badge_pad_y":     (0,   20,   1),
    "badge_radius":    (0,   20,   1),
    "badge_top_pad":   (0,   100,  2),
    "badge_gap":       (0,   60,   2),
    "delivery_font":   (20,  80,   2),
    "delivery_top_pad":(0,   100,  2),
    # Рейтинг
    "rating_font":     (20,  100,  2),
    "rating_top_pad":  (0,   200,  5),
    "star_size":       (10,  80,   2),
    # Пэй
    "pay_font":        (20,  100,  2),
    "pay_bar_w":       (20,  200,  2),
    "pay_bar_h":       (4,   40,   1),
    "pay_gap_1":       (0,   100,  2),
    "pay_gap_2":       (0,   100,  2),
    # Сердечко
    "heart_size":      (20,  120,  2),
    "heart_right":     (0,   300,  5),
    "heart_top":       (0,   300,  5),
}

# Заголовки блоков для группировки ползунков
SLIDER_GROUPS = {
    "ozon": [
        ("Фото", ["photo_h", "round_radius", "photo_margin"]),
        ("Цена", ["price_font", "price_top_pad", "price_left_pad"]),
        ("Старая цена и скидка", ["old_price_font", "discount_font", "old_price_gap"]),
        ("Название", ["title_font", "title_line_h", "title_top_pad", "title_left_pad"]),
        ("Рейтинг", ["rating_font", "rating_top_pad", "star_size"]),
        ("Сердечко", ["heart_size", "heart_right", "heart_top"]),
    ],
    "yandex": [
        ("Фото", ["photo_h", "round_radius", "photo_margin"]),
        ("Название", ["title_font", "title_line_h", "title_top_pad", "title_left_pad"]),
        ("Цена", ["price_font", "price_top_gap"]),
        ("Полоска «Пэй»", ["pay_font", "pay_bar_w", "pay_bar_h", "pay_gap_1", "pay_gap_2"]),
        ("Сердечко", ["heart_size", "heart_right", "heart_top"]),
    ],
    "wildberries": [
        ("Фото", ["photo_h", "round_radius", "photo_margin"]),
        ("Цена", ["price_font", "price_top_pad", "price_left_pad"]),
        ("Старая цена", ["old_price_font", "old_price_gap"]),
        ("Бренд и название", [
            "brand_font", "title_font", "title_line_h",
            "title_top_pad", "title_left_pad",
        ]),
        ("Рейтинг", ["rating_font", "rating_top_pad", "star_size"]),
        ("Сердечко", ["heart_size", "heart_right", "heart_top"]),
    ],
    "aliexpress": [
        ("Фото", ["photo_h", "round_radius", "photo_margin"]),
        ("Старая цена и скидка", [
            "old_price_font", "discount_font",
            "old_price_gap", "old_row_top_pad",
        ]),
        ("Цена", ["price_font", "price_top_pad", "price_left_pad"]),
        ("Бейджи", [
            "badge_font", "badge_pad_x", "badge_pad_y",
            "badge_radius", "badge_top_pad", "badge_gap",
        ]),
        ("Название", ["title_font", "title_line_h", "title_top_pad", "title_left_pad"]),
        ("Доставка", ["delivery_font", "delivery_top_pad"]),
        ("Сердечко", ["heart_size", "heart_right", "heart_top"]),
    ],
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


def _open_image(src):
    try:
        if isinstance(src, (bytes, bytearray)):
            return Image.open(io.BytesIO(src)).convert("RGB")
        return Image.open(src).convert("RGB")
    except Exception as e:
        print(f"overlay: не открыть источник: {e}")
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


def _fmt_price(v):
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


def _draw_strike_text(draw, x, y, text, font, color):
    draw.text((x, y), text, font=font, fill=color)
    bbox = draw.textbbox((x, y), text, font=font)
    strike_y = (bbox[1] + bbox[3]) // 2
    draw.line([(bbox[0], strike_y), (bbox[2], strike_y)], fill=color, width=3)


def _resolve_canvas_size(aspect: str):
    if aspect == "9:16":
        return 1080, 1920
    if aspect == "3:5":
        return 1080, 1800
    if aspect == "3:4.5":
        return 1080, 1620
    if aspect == "4:5":
        return 1080, 1350
    return 1080, 1440


# ============================================================
# Рендер: Ozon
# ============================================================

def render_ozon(src, params, aspect="3:4.5", W=None, H=None):
    """Карточка Ozon: цена (розовая) → название → рейтинг."""
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

    COLOR_PRICE_OZON = (241, 17, 126, 255)   # #F1117E
    COLOR_HEART_RED  = (248, 16, 75, 255)    # #F8104B
    COLOR_STAR_GOLD  = (255, 168, 0, 255)
    COLOR_TEXT_PRIMARY   = (25, 24, 23, 255)
    COLOR_TEXT_SECONDARY = (140, 138, 135, 255)

    img = _open_image(src)
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
    draw.polygon(
        _heart_polygon(W - heart_right, heart_top, heart_size),
        fill=COLOR_HEART_RED,
    )

    # Белый блок
    y = photo_margin + photo_h
    text_w = W - left_pad * 2

    # --- Цена ---
    y += price_top_pad
    price_left_pad = int(p["price_left_pad"] * s)  # ← отдельный отступ

    font_price = _font(om.FONT_BOLD, price_font_px)
    price_str = _fmt_price(1100.0)

    draw.text(
        (price_left_pad, y), price_str,
        font=font_price, fill=COLOR_PRICE_OZON,
    )
    price_w = draw.textlength(price_str, font=font_price)

    # Вертикальный центр основной цены — по нему выровняем старую и скидку
    bbox_main = draw.textbbox((price_left_pad, y), price_str, font=font_price)
    center_y = (bbox_main[1] + bbox_main[3]) // 2

    # Старая цена + скидка
    old = 5000.0
    if old > 1100.0:
        font_old = _font(om.FONT_REGULAR, old_font_px)
        font_disc = _font(om.FONT_BOLD, disc_font_px)
        old_str = _fmt_price(old)
        disc_pct = int(round((old - 1100.0) / old * 100))

        x_old = price_left_pad + int(price_w) + old_gap

        # Старая цена — зачёркнутая, серая, выровнена по центру
        draw.text(
            (x_old, center_y), old_str,
            font=font_old, fill=COLOR_TEXT_SECONDARY,
            anchor="lm",
        )
        bbox_old = draw.textbbox(
            (x_old, center_y), old_str, font=font_old, anchor="lm",
        )
        draw.line(
            [(bbox_old[0], center_y), (bbox_old[2], center_y)],
            fill=COLOR_TEXT_SECONDARY, width=3,
        )

        # Скидка −XX% — РОЗОВЫМ, выровнена по центру
        x_disc = bbox_old[2] + old_gap
        draw.text(
            (x_disc, center_y), f"−{disc_pct}%",
            font=font_disc, fill=COLOR_PRICE_OZON,
            anchor="lm",
        )

    y += int(price_font_px * 1.25)

    # --- Название ---
    y += title_top_pad
    font_title = _font(om.FONT_REGULAR, title_font_px)
    demo_title = "Наушники беспроводные PRO 2 с шумоподавлением. Наушники беспроводные для телефона"
    title_lines = _wrap(draw, demo_title, font_title, text_w, max_lines=2)
    line_h = int(title_font_px * p["title_line_h"])
    for line in title_lines:
        draw.text((left_pad, y), line, font=font_title, fill=COLOR_TEXT_PRIMARY)
        y += line_h

    # --- Рейтинг ---
    y += rating_top_pad
    font_rating = _font(om.FONT_REGULAR, rating_font_px)
    star_cx = left_pad + star_size
    star_cy = y + rating_font_px // 2
    _draw_star(draw, star_cx, star_cy, star_size, COLOR_STAR_GOLD)

    rating_str = "4,8"
    x_rating = left_pad + star_size * 2 + 10
    draw.text((x_rating, y), rating_str, font=font_rating, fill=COLOR_TEXT_PRIMARY)

    reviews_str = "· 2 591 отзывов"
    x_rev = x_rating + int(draw.textlength(rating_str, font=font_rating)) + 15
    draw.text((x_rev, y), reviews_str, font=font_rating, fill=COLOR_TEXT_SECONDARY)

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


# ============================================================
# Рендер: Yandex (копия overlay_maker)
# ============================================================

def render_yandex(src, params, aspect="3:4.5", W=None, H=None):
    """Карточка Яндекс.Маркета: название → цена (зелёная) + «Пэй»."""
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

    img = _open_image(src)
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
    draw.polygon(
        _heart_polygon(W - heart_right, heart_top, heart_size),
        fill=COLOR_HEART_RED,
    )

    # Название
    y = photo_margin + photo_h + title_top_pad
    text_w = W - left_pad * 2
    font_title = _font(om.FONT_REGULAR, title_font_px)
    demo_title = "Чёрная тушь BLACK MAMBA с эффектом накладных ресниц"
    title_lines = _wrap(draw, demo_title, font_title, text_w, max_lines=2)
    line_h = int(title_font_px * p["title_line_h"])
    for line in title_lines:
        draw.text((left_pad, y), line, font=font_title, fill=COLOR_TEXT_PRIMARY)
        y += line_h

    # Цена
    y += price_top_gap
    font_price = _font(om.FONT_BOLD, price_font_px)
    price_str = "888 ₽"
    draw.text((left_pad, y), price_str, font=font_price, fill=COLOR_PRICE_GREEN)
    price_w = draw.textlength(price_str, font=font_price)

    # Пэй
    font_pay = _font(om.FONT_REGULAR, pay_font_px)
    pay_text = "Пэй"
    pay_w = draw.textlength(pay_text, font=font_pay)
    bar_x = left_pad + int(price_w) + pay_gap_1
    bar_y = y + int(price_font_px * 0.45)
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + pay_bar_w, bar_y + pay_bar_h),
        radius=4, fill=COLOR_PRICE_GREEN,
    )
    pay_x = bar_x + pay_bar_w + pay_gap_2
    pay_y = bar_y - int(pay_font_px * 0.30)
    draw.text((pay_x, pay_y), pay_text, font=font_pay, fill=COLOR_PRICE_GREEN)

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


# ============================================================
# Рендер WB / AliExpress — заготовки
# ============================================================

def render_wb(src, params, aspect="3:4.5", W=None, H=None):
    """
    Карточка Wildberries:
      Цена красная + иконка кошелька → старая зачёркнутая
      Бренд / Название (в одну строку)
      ★ рейтинг · N оценок
    """
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

    # Цвета WB
    COLOR_PRICE_WB       = (255, 51, 51, 255)     # красный
    COLOR_HEART_WB       = (255, 51, 51, 255)     # красное (можно поменять на контур)
    COLOR_STAR_GOLD      = (255, 168, 0, 255)
    COLOR_TEXT_PRIMARY   = (25, 24, 23, 255)
    COLOR_TEXT_SECONDARY = (140, 138, 135, 255)
    COLOR_BRAND          = (60, 100, 180, 255)    # синий бренд (галочка)

    img = _open_image(src)
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

    # Сердечко (красное)
    draw.polygon(
        _heart_polygon(W - heart_right, heart_top, heart_size),
        fill=COLOR_HEART_WB,
    )

    y = photo_margin + photo_h

    # --- Цена с иконкой кошелька ---
    y += price_top_pad

    font_price = _font(om.FONT_BOLD, price_font_px)
    price_str = "903 ₽"

    # Вертикальный центр основной цены
    bbox_main = draw.textbbox((price_left_pad, y), price_str, font=font_price)
    center_y = (bbox_main[1] + bbox_main[3]) // 2

    # Иконка кошелька — выравниваем по центру цены
    icon_size = int(price_font_px * 0.60)
    icon_x = price_left_pad
    icon_y = center_y - icon_size // 2

    # Тело кошелька
    body_h = int(icon_size * 0.75)
    body_y = icon_y + (icon_size - body_h) // 2
    draw.rounded_rectangle(
        (icon_x, body_y, icon_x + icon_size, body_y + body_h),
        radius=3, fill=COLOR_PRICE_WB,
    )
    # Застёжка (белый кружок справа)
    clip_r = int(body_h * 0.30)
    clip_cx = icon_x + icon_size - int(icon_size * 0.15)
    clip_cy = body_y + body_h // 2
    draw.ellipse(
        (clip_cx - clip_r, clip_cy - clip_r,
         clip_cx + clip_r, clip_cy + clip_r),
        fill=(255, 255, 255),
        outline=COLOR_PRICE_WB, width=2,
    )

    # Цена — справа от иконки, центр по вертикали
    x_price = icon_x + icon_size + int(price_font_px * 0.20)
    draw.text(
        (x_price, center_y), price_str,
        font=font_price, fill=COLOR_PRICE_WB, anchor="lm",
    )
    price_w = draw.textlength(price_str, font=font_price)

    # Вертикальный центр для выравнивания старой цены
    bbox_main = draw.textbbox((x_price, y), price_str, font=font_price)
    center_y = (bbox_main[1] + bbox_main[3]) // 2

    # --- Старая цена зачёркнутая ---
    old = 1100.0
    if old > 903.0:
        font_old = _font(om.FONT_REGULAR, old_font_px)
        old_str = _fmt_price(old)
        x_old = x_price + int(price_w) + old_gap

        draw.text(
            (x_old, center_y), old_str,
            font=font_old, fill=COLOR_TEXT_SECONDARY, anchor="lm",
        )
        bbox_old = draw.textbbox(
            (x_old, center_y), old_str, font=font_old, anchor="lm",
        )
        draw.line(
            [(bbox_old[0], center_y), (bbox_old[2], center_y)],
            fill=COLOR_TEXT_SECONDARY, width=2,
        )

    y += int(price_font_px * 1.25)

    # --- Бренд / Название ---
    y += title_top_pad
    text_w = W - left_pad * 2

    # Бренд с галочкой — галочка СЛЕВА от названия
    font_brand = _font(om.FONT_REGULAR, brand_font_px)
    font_title = _font(om.FONT_REGULAR, title_font_px)

    # Синяя галочка — слева
    check_size = int(brand_font_px * 0.75)
    check_x = left_pad
    check_y = y + int(brand_font_px * 0.15)
    draw.ellipse(
        (check_x, check_y, check_x + check_size, check_y + check_size),
        fill=COLOR_BRAND,
    )
    # Белая галочка внутри круга
    cx = check_x + check_size // 2
    cy = check_y + check_size // 2
    draw.line(
        [(cx - check_size * 0.25, cy),
         (cx - check_size * 0.05, cy + check_size * 0.20),
         (cx + check_size * 0.25, cy - check_size * 0.20)],
        fill=(255, 255, 255), width=2,
    )

    # Бренд — справа от галочки
    x_brand = check_x + check_size + 8
    brand_str = "Alcon"
    draw.text((x_brand, y), brand_str, font=font_brand, fill=COLOR_TEXT_PRIMARY)
    brand_w = draw.textlength(brand_str, font=font_brand)

    # Слэш — после бренда
    x_slash = x_brand + int(brand_w) + 8
    draw.text((x_slash, y), "/", font=font_title, fill=COLOR_TEXT_SECONDARY)
    slash_w = draw.textlength("/", font=font_title)

    # Название — после слэша
    x_title = x_slash + int(slash_w) + 8
    name_str = "Контактные линзы -2,0 2 шт"
    remaining_w = W - x_title - left_pad
    title_lines = _wrap(draw, name_str, font_title, remaining_w, max_lines=1)
    if title_lines:
        draw.text(
            (x_title, y), title_lines[0],
            font=font_title, fill=COLOR_TEXT_SECONDARY,
        )

    y += int(max(brand_font_px, title_font_px) * 1.25)

    # --- Рейтинг ★ 4,8 · 5 371 оценка ---
    y += rating_top_pad
    font_rating = _font(om.FONT_REGULAR, rating_font_px)

    star_cx = left_pad + star_size
    star_cy = y + rating_font_px // 2
    _draw_star(draw, star_cx, star_cy, star_size, COLOR_STAR_GOLD)

    rating_str = "4,8"
    x_rating = left_pad + star_size * 2 + 10
    draw.text((x_rating, y), rating_str, font=font_rating, fill=COLOR_TEXT_PRIMARY)

    reviews_str = "· 5 371 оценка"
    x_rev = x_rating + int(draw.textlength(rating_str, font=font_rating)) + 12
    draw.text((x_rev, y), reviews_str, font=font_rating, fill=COLOR_TEXT_SECONDARY)

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


def render_aliexpress(src, params, aspect="3:4.5", W=None, H=None):
    """
    Карточка AliExpress:
      Старая цена + скидка → чёрная цена → бейджи ★ и «N купили»
      → название → зелёная доставка
    """
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
    delivery_font_px = int(p["delivery_font"] * s)
    delivery_top_pad = int(p["delivery_top_pad"] * s)
    heart_size      = int(p["heart_size"]     * s)
    heart_right     = int(p["heart_right"]    * s)
    heart_top       = int(p["heart_top"]      * s)

    # Цвета AliExpress
    COLOR_TEXT_PRIMARY   = (24, 24, 27, 255)      # почти чёрный
    COLOR_TEXT_SECONDARY = (137, 137, 147, 255)   # серый для старой цены
    COLOR_RATING_BG      = (255, 230, 212, 255)   # оранжевый фон бейджа
    COLOR_RATING_TEXT    = (255, 108, 2, 255)     # оранжевый текст
    COLOR_BUYERS_BG      = (243, 246, 249, 255)   # серо-голубой
    COLOR_BUYERS_TEXT    = (24, 24, 27, 255)      # чёрный текст
    COLOR_DELIVERY       = (0, 173, 48, 255)      # зелёный
    COLOR_HEART_RED      = (255, 71, 71, 255)     # красное сердечко
    COLOR_STAR_GOLD      = (255, 168, 0, 255)

    img = _open_image(src)
    if img is None:
        return None

    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    photo_w = W - photo_margin * 2
    photo = _crop_cover(img, photo_w, photo_h)
    mask = Image.new("L", (photo_w, photo_h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle(
        (0, 0, photo_w, photo_h), radius=round_radius, fill=255,
    )
    canvas.paste(photo, (photo_margin, photo_margin), mask)

    draw = ImageDraw.Draw(canvas)

    # Сердечко
    draw.polygon(
        _heart_polygon(W - heart_right, heart_top, heart_size),
        fill=COLOR_HEART_RED,
    )

    y = photo_margin + photo_h
    text_w = W - left_pad * 2

    # === 1. Старая цена + скидка (мелким, сверху) ===
    y += old_top_pad
    old = 231.0
    price_val = 185.0

    font_old  = _font(om.FONT_REGULAR, old_font_px)
    font_disc = _font(om.FONT_REGULAR, disc_font_px)

    x = price_left_pad
    old_str = _fmt_price(old)
    draw.text((x, y), old_str, font=font_old, fill=COLOR_TEXT_SECONDARY)
    bbox_old = draw.textbbox((x, y), old_str, font=font_old)
    strike_y = (bbox_old[1] + bbox_old[3]) // 2
    draw.line(
        [(bbox_old[0], strike_y), (bbox_old[2], strike_y)],
        fill=COLOR_TEXT_SECONDARY, width=2,
    )

    x_disc = bbox_old[2] + old_gap
    disc_pct = int(round((old - price_val) / old * 100))
    draw.text(
        (x_disc, y), f"-{disc_pct}%",
        font=font_disc, fill=COLOR_TEXT_SECONDARY,
    )

    y += int(old_font_px * 1.25)

    # === 2. Цена (чёрная, крупная) ===
    y += price_top_pad
    font_price = _font(om.FONT_BOLD, price_font_px)
    price_str = _fmt_price(price_val)
    draw.text(
        (price_left_pad, y), price_str,
        font=font_price, fill=COLOR_TEXT_PRIMARY,
    )
    y += int(price_font_px * 1.25)

    # === 3. Бейджи ★ и «N купили» ===
    y += badge_top_pad
    font_badge = _font(om.FONT_REGULAR, badge_font_px)

    # Бейдж рейтинга — оранжевый
    rating_text = "4.4"
    bbox = draw.textbbox((0, 0), rating_text, font=font_badge)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    star_size = int(badge_font_px * 0.9)
    star_gap = 4
    rating_w = badge_pad_x * 2 + star_size + star_gap + text_w
    rating_h = text_h + badge_pad_y * 2

    x_badge = price_left_pad
    draw.rounded_rectangle(
        (x_badge, y, x_badge + rating_w, y + rating_h),
        radius=badge_radius, fill=COLOR_RATING_BG,
    )
    # Звезда
    star_cx = x_badge + badge_pad_x + star_size // 2
    star_cy = y + rating_h // 2
    _draw_star(draw, star_cx, star_cy, star_size // 2, COLOR_STAR_GOLD)
    # Текст рейтинга
    x_text = x_badge + badge_pad_x + star_size + star_gap
    y_text = y + badge_pad_y - bbox[1]
    draw.text((x_text, y_text), rating_text, font=font_badge, fill=COLOR_RATING_TEXT)

    # Бейдж «N купили» — серо-голубой
    x_buyers = x_badge + rating_w + badge_gap
    buyers_text = "6 933 купили"
    bbox_b = draw.textbbox((0, 0), buyers_text, font=font_badge)
    buyers_w = (bbox_b[2] - bbox_b[0]) + badge_pad_x * 2
    buyers_h = (bbox_b[3] - bbox_b[1]) + badge_pad_y * 2

    draw.rounded_rectangle(
        (x_buyers, y, x_buyers + buyers_w, y + buyers_h),
        radius=badge_radius, fill=COLOR_BUYERS_BG,
    )
    draw.text(
        (x_buyers + badge_pad_x - bbox_b[0], y + badge_pad_y - bbox_b[1]),
        buyers_text, font=font_badge, fill=COLOR_BUYERS_TEXT,
    )

    y += rating_h

    # === 4. Название (2 строки) ===
    y += title_top_pad
    font_title = _font(om.FONT_REGULAR, title_font_px)
    name_str = "Серебряные серьги-гвоздики с кисточками и цирконом"
    title_lines = _wrap(draw, name_str, font_title, text_w, max_lines=2)
    line_h = int(title_font_px * p["title_line_h"])
    for line in title_lines:
        draw.text((left_pad, y), line, font=font_title, fill=COLOR_TEXT_PRIMARY)
        y += line_h

    # === 5. Доставка (зелёная) ===
    y += delivery_top_pad
    font_delivery = _font(om.FONT_REGULAR, delivery_font_px)
    delivery_str = "до 14 дней, от 46 ₽"
    draw.text(
        (left_pad, y), delivery_str,
        font=font_delivery, fill=COLOR_DELIVERY,
    )

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


RENDERERS = {
    "ozon":        render_ozon,
    "yandex":      render_yandex,
    "wildberries": render_wb,
    "aliexpress":  render_aliexpress,
}


# ============================================================
# Tkinter GUI
# ============================================================

class TunerApp:
    def __init__(self, src_path: Path):
        self.src_path = src_path

        # Текущий маркетплейс
        self.marketplace = "ozon"

        # Значения по всем маркетплейсам
        self.values = {mp: dict(vals) for mp, vals in DEFAULTS.items()}

        # Слайдеры (виджеты) — пересоздаются при переключении
        self.sliders = {}
        self.slider_container = None

        self.preview_photo = None
        self._update_job = None

        # Canvas размер
        self.canvas_w = 1080
        self.canvas_h = 1620

        # === Главное окно ===
        self.root = tk.Tk()
        self.root.title(f"Card Tuner — {src_path.name}")
        self.root.geometry("1500x950")

        # === Левая панель ===
        left = ttk.Frame(self.root, padding=10)
        left.pack(side="left", fill="y")

        scroll_canvas = tk.Canvas(left, width=430, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=scroll_canvas.yview)
        inner = ttk.Frame(scroll_canvas)
        scroll_canvas.create_window((0, 0), window=inner, anchor="nw")
        scroll_canvas.configure(yscrollcommand=scrollbar.set)
        scroll_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # === Блок выбора маркетплейса ===
        self._build_marketplace_block(inner)

        # === Блок размера карточки ===
        self._build_size_block(inner)

        # === Контейнер для слайдеров (пересоздаётся) ===
        self.slider_container = ttk.Frame(inner)
        self.slider_container.pack(fill="x", expand=True)

        inner.update_idletasks()
        scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))

        # === Кнопки ===
        btns = ttk.Frame(left, padding=(10, 6))
        btns.pack(side="bottom", fill="x")
        ttk.Button(btns, text="💾 Сохранить", command=self.save).pack(
            side="left", fill="x", expand=True, padx=2)
        ttk.Button(btns, text="↺ Сброс", command=self.reset).pack(
            side="left", fill="x", expand=True, padx=2)

        # === Правое превью ===
        right = ttk.Frame(self.root, padding=10)
        right.pack(side="right", fill="both", expand=True)

        self.info = ttk.Label(right, text="", font=("Arial", 10))
        self.info.pack(side="bottom", pady=4)

        self.preview_label = ttk.Label(right)
        self.preview_label.pack(fill="both", expand=True)

        # Первый рендер
        self._rebuild_sliders()
        self.schedule_update(immediate=True)

    # ============================================================
    # Блок: Маркетплейс
    # ============================================================

    def _build_marketplace_block(self, parent):
        lf = ttk.LabelFrame(parent, text="Маркетплейс", padding=8)
        lf.pack(fill="x", pady=6, padx=2)

        row = ttk.Frame(lf)
        row.pack(fill="x")
        ttk.Label(row, text="Рендер", width=10, anchor="w").pack(side="left")

        self.mp_var = tk.StringVar(value=self.marketplace)
        combo = ttk.Combobox(
            row, textvariable=self.mp_var, values=MARKETPLACES,
            state="readonly", width=20,
        )
        combo.pack(side="left", fill="x", expand=True)
        combo.bind("<<ComboboxSelected>>", self._on_marketplace_change)

    def _on_marketplace_change(self, event=None):
        new_mp = self.mp_var.get()
        if new_mp == self.marketplace:
            return
        self.marketplace = new_mp
        self._rebuild_sliders()
        self.schedule_update()

    # ============================================================
    # Блок: Размер карточки
    # ============================================================

    def _build_size_block(self, parent):
        lf = ttk.LabelFrame(parent, text="Размер карточки", padding=8)
        lf.pack(fill="x", pady=6, padx=2)

        # Пресет
        row = ttk.Frame(lf)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Пресет", width=15, anchor="w").pack(side="left")

        self.size_var = tk.StringVar(value=SIZE_PRESETS[SIZE_DEFAULT_IDX][0])
        combo = ttk.Combobox(
            row, textvariable=self.size_var,
            values=[p[0] for p in SIZE_PRESETS],
            state="readonly", width=22,
        )
        combo.pack(side="left", fill="x", expand=True)
        combo.bind("<<ComboboxSelected>>", self._on_preset)

        # Ширина
        row2 = ttk.Frame(lf)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="Ширина, px", width=15, anchor="w").pack(side="left")
        self.w_var = tk.IntVar(value=self.canvas_w)
        w_spin = ttk.Spinbox(
            row2, from_=200, to=4000, increment=10,
            textvariable=self.w_var, width=8,
            command=self._on_size_change,
        )
        w_spin.pack(side="left")
        w_spin.bind("<KeyRelease>", lambda e: self._on_size_change())

        # Высота
        row3 = ttk.Frame(lf)
        row3.pack(fill="x", pady=4)
        ttk.Label(row3, text="Высота, px", width=15, anchor="w").pack(side="left")
        self.h_var = tk.IntVar(value=self.canvas_h)
        h_spin = ttk.Spinbox(
            row3, from_=200, to=4000, increment=10,
            textvariable=self.h_var, width=8,
            command=self._on_size_change,
        )
        h_spin.pack(side="left")
        h_spin.bind("<KeyRelease>", lambda e: self._on_size_change())

        # Пропорция
        self.ratio_lbl = ttk.Label(lf, text="", foreground="gray")
        self.ratio_lbl.pack(anchor="w", pady=(4, 0))
        self._update_ratio_label()

    def _on_preset(self, event=None):
        for name, w, h in SIZE_PRESETS:
            if name == self.size_var.get():
                self.canvas_w = w
                self.canvas_h = h
                self.w_var.set(w)
                self.h_var.set(h)
                self._update_ratio_label()
                self.schedule_update()
                return

    def _on_size_change(self):
        try:
            self.canvas_w = int(self.w_var.get())
            self.canvas_h = int(self.h_var.get())
        except (ValueError, tk.TclError):
            return
        self._update_ratio_label()
        self.schedule_update()

    def _update_ratio_label(self):
        from math import gcd
        w, h = self.canvas_w, self.canvas_h
        g = gcd(w, h) or 1
        self.ratio_lbl.config(text=f"Пропорция: {w // g}:{h // g}  ({w}×{h})")

    # ============================================================
    # Слайдеры
    # ============================================================

    def _rebuild_sliders(self):
        # Удаляем всё
        for w in self.slider_container.winfo_children():
            w.destroy()
        self.sliders = {}

        groups = SLIDER_GROUPS.get(self.marketplace, [])
        values = self.values[self.marketplace]

        if not groups:
            ttk.Label(
                self.slider_container,
                text=f"Маркетплейс '{self.marketplace}' — заглушка.\n"
                     f"Параметры ещё не заданы.",
                foreground="gray",
            ).pack(pady=20, padx=10, anchor="w")
            return

        for title, keys in groups:
            lf = ttk.LabelFrame(self.slider_container, text=title, padding=8)
            lf.pack(fill="x", pady=6, padx=2)

            for key in keys:
                lo, hi, step = SLIDER_RANGES.get(key, (0, 200, 1))

                row = ttk.Frame(lf)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=key, width=17, anchor="w").pack(side="left")

                val_lbl = ttk.Label(row, width=6, anchor="e")
                val_lbl.pack(side="right")

                var = tk.DoubleVar(value=values.get(key, lo))
                scale = ttk.Scale(
                    row, from_=lo, to=hi, orient="horizontal",
                    variable=var,
                    command=lambda v, k=key: self._on_change(k, float(v)),
                )
                scale.pack(side="left", fill="x", expand=True, padx=4)

                self.sliders[key] = (var, val_lbl)
                val_lbl.config(text=self._fmt(values.get(key, lo)))

    def _fmt(self, v):
        if isinstance(v, float) and v < 10:
            return f"{v:.2f}"
        return f"{int(v)}"

    def _on_change(self, key, value):
        if key == "title_line_h":
            self.values[self.marketplace][key] = round(value, 2)
        else:
            self.values[self.marketplace][key] = int(round(value))
        self.sliders[key][1].config(text=self._fmt(self.values[self.marketplace][key]))
        self.schedule_update()

    # ============================================================
    # Рендер
    # ============================================================

    def schedule_update(self, immediate=False):
        if self._update_job:
            try:
                self.root.after_cancel(self._update_job)
            except Exception:
                pass
        delay = 0 if immediate else 120
        self._update_job = self.root.after(delay, self._render)

    def _render(self):
        try:
            render_fn = RENDERERS.get(self.marketplace, render_yandex)
            params = self.values[self.marketplace]

            data = render_fn(
                self.src_path, params,
                W=self.canvas_w, H=self.canvas_h,
            )
            if not data:
                self.info.config(text="Рендер вернул None")
                return

            img = Image.open(io.BytesIO(data))

            PREVIEW_W, PREVIEW_H = 400, 600
            ratio = min(PREVIEW_W / img.width, PREVIEW_H / img.height)
            new_w = int(img.width * ratio)
            new_h = int(img.height * ratio)
            preview = img.resize((new_w, new_h), Image.LANCZOS)

            self.preview_photo = ImageTk.PhotoImage(preview)
            self.preview_label.config(image=self.preview_photo)
            self.info.config(
                text=f"{self.marketplace.upper()}  •  {self.canvas_w}×{self.canvas_h}  •  "
                     f"preview {new_w}×{new_h}"
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.info.config(text=f"Ошибка рендера: {e}")

    # ============================================================
    # Кнопки
    # ============================================================

    def reset(self):
        self.values[self.marketplace] = dict(DEFAULTS[self.marketplace])
        self._rebuild_sliders()
        self.canvas_w = 1080
        self.canvas_h = 1620
        self.w_var.set(1080)
        self.h_var.set(1620)
        self.size_var.set(SIZE_PRESETS[SIZE_DEFAULT_IDX][0])
        self._update_ratio_label()
        self.schedule_update()

    def save(self):
        out_dir = PROJECT_ROOT / "scripts"
        out_dir.mkdir(exist_ok=True)

        default = out_dir / f"tuned_{self.marketplace}_{self.canvas_w}x{self.canvas_h}.jpg"

        path = filedialog.asksaveasfilename(
            initialdir=str(out_dir),
            initialfile=default.name,
            defaultextension=".jpg",
            filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")],
        )
        if not path:
            return

        render_fn = RENDERERS.get(self.marketplace, render_yandex)
        data = render_fn(
            self.src_path, self.values[self.marketplace],
            W=self.canvas_w, H=self.canvas_h,
        )
        if not data:
            messagebox.showerror("Ошибка", "Не удалось отрендерить")
            return

        Path(path).write_bytes(data)
        messagebox.showinfo("Сохранено", f"Файл:\n{path}")

        # Печатаем параметры в консоль
        print("\n" + "=" * 60)
        print(f"МАРКЕТПЛЕЙС: {self.marketplace.upper()}")
        print(f"РАЗМЕР:      {self.canvas_w} × {self.canvas_h}")
        print("=" * 60)
        print("ПАРАМЕТРЫ:")
        for k, v in self.values[self.marketplace].items():
            print(f'  "{k}": {v},')
        print("=" * 60)

    def run(self):
        self.root.mainloop()


# ============================================================
# main
# ============================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/card_tuner.py <image>")
        print("  Размер и маркетплейс выбираются внутри окна.")
        sys.exit(1)

    src = Path(sys.argv[1]).expanduser().resolve()
    if not src.exists():
        print(f"[ERR] Файл не найден: {src}")
        sys.exit(1)

    # Патчим шрифты
    ys_dir = _LOCAL_FONTS_DIR
    reg  = ys_dir / "YSText-Regular.ttf"
    bold = ys_dir / "YSText-Bold.ttf"
    ital = ys_dir / "YSText-BoldItalic.ttf"
    if all(p.exists() for p in (reg, bold, ital)):
        om.FONT_REGULAR     = str(reg)
        om.FONT_BOLD        = str(bold)
        om.FONT_BOLD_ITALIC = str(ital)
        print(f"Шрифты: YS Text ({ys_dir})")
    else:
        print("Шрифты: DejaVu (fallback)")

    app = TunerApp(src)
    app.run()


if __name__ == "__main__":
    main()