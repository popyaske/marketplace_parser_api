"""
Визуальный тюнер карточки с ползунками.

Запуск:
    python scripts/card_tuner_old.py test.jpg
    python scripts/card_tuner_old.py test.jpg 3:4.5

Что делает:
  • Слева — блок выбора размера карточки (пресеты + ручные W/H).
  • Слева ниже — ползунки всех параметров.
  • Справа — живое превью 400×600.
  • Кнопка «Сохранить» — сохраняет в out/tuned_<W>x<H>.jpg.
  • Кнопка «Сброс» — возвращает значения по умолчанию.

overlay_maker.py не трогаем — рендер делается здесь же.
"""
import io
import math
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageDraw, ImageTk


# ============================================================
# Пути и импорт overlay_maker
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_LOCAL_FONTS_DIR = Path(os.getenv("FONTS_DIR") or (PROJECT_ROOT / "fonts"))
os.environ["FONTS_DIR"] = str(_LOCAL_FONTS_DIR)

import services.overlay_maker as om  # noqa: E402


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

SIZE_DEFAULT_IDX = 3  #1080×1620 (3:4.5)


# ============================================================
# Значения по умолчанию для ползунков
# ============================================================
DEFAULTS = {
    # Фото
    "photo_h":            1125,
    "round_radius":       81,
    "photo_margin":       40,

    # Название
    "title_font":         56,
    "title_line_h":       1.14,
    "title_top_pad":      24,
    "title_left_pad":     56,

    # Цена
    "price_font":         94,
    "price_top_gap":      4,
    "price_align_left":   1,

    # Полоска «Пэй»
    "pay_font":           60,
    "pay_bar_w":          78,
    "pay_bar_h":          40,
    "pay_gap_1":          28,
    "pay_gap_2":          18,

    # Бейдж «НИЖЕ РЫНКА»
    "badge_font":         57,
    "badge_left":         37,
    "badge_bottom":       108,

    # Сердечко
    "heart_size":         42,
    "heart_outline":      0,
    "heart_right":        116,
    "heart_top":          106,
}

BASE_H = 1440


# ============================================================
# Рендер (своя копия, с параметрами из GUI)
# ============================================================

def _heart_polygon(cx, cy, size, steps=200):
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


def render_card(src, params: dict, aspect="3:4",
                W: int = None, H: int = None):
    # Приоритет: явные W/H → иначе аспект
    if W is None or H is None:
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
    p = params

    photo_h        = int(p["photo_h"]         * s)
    round_radius   = int(p["round_radius"]    * s)
    photo_margin   = int(p["photo_margin"]    * s)
    title_font_px  = int(p["title_font"]      * s)
    title_top_pad  = int(p["title_top_pad"]   * s)
    left_pad       = int(p["title_left_pad"]  * s)
    price_font_px  = int(p["price_font"]      * s)
    price_top_gap  = int(p["price_top_gap"]   * s)
    pay_font_px    = int(p["pay_font"]        * s)
    pay_bar_w      = int(p["pay_bar_w"]       * s)
    pay_bar_h      = max(4, int(p["pay_bar_h"] * s))
    pay_gap_1      = int(p["pay_gap_1"]       * s)
    pay_gap_2      = int(p["pay_gap_2"]       * s)
    badge_font_px  = int(p["badge_font"]      * s)
    badge_left     = int(p["badge_left"]      * s)
    badge_bottom   = int(p["badge_bottom"]    * s)
    heart_size     = int(p["heart_size"]      * s)
    heart_outline  = max(2, int(p["heart_outline"] * s))
    heart_right    = int(p["heart_right"]     * s)
    heart_top      = int(p["heart_top"]       * s)

    img = om._open_image(src)
    if img is None:
        return None

    # Фон
    canvas = Image.new("RGB", (W, H), om.COLOR_BG)

    # Фото — с отступом от краёв или впритык
    photo_w = W - photo_margin * 2
    photo = om._crop_cover(img, photo_w, photo_h)

    # Скругление: если photo_margin > 0 — все 4 угла, иначе только верхние
    if photo_margin > 0:
        mask = Image.new("L", (photo_w, photo_h), 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.rounded_rectangle(
            (0, 0, photo_w, photo_h),
            radius=round_radius, fill=255,
        )
    else:
        mask = om._rounded_top_mask(photo_w, photo_h, radius=round_radius)

    canvas.paste(photo, (photo_margin, photo_margin), mask)

    draw = ImageDraw.Draw(canvas)

    # Сердечко
    _draw_heart_smooth(
        draw,
        cx=W - heart_right,
        cy=heart_top,
        size=heart_size,
        fill_color=om.COLOR_HEART_RED,
        outline_color=om.COLOR_HEART_OUTLINE,
        outline_w=heart_outline,
    )

    # Демо-данные (фиксированные — тестовая карточка)
    primary = 1100.0
    old = 1500.0

    # Бейдж «НИЖЕ РЫНКА» — прижат к нижнему краю фото
    if old > primary:
        font_badge = om._font(om.FONT_BOLD_ITALIC, badge_font_px)
        badge_y = photo_margin + photo_h - badge_bottom
        om._draw_nizhe_rynka_badge(
            draw, x=photo_margin + badge_left, y=badge_y, font=font_badge,
        )

    # Белый блок
    block_y = photo_margin + photo_h
    text_w = W - left_pad * 2

    # Название
    font_title = om._font(om.FONT_REGULAR, title_font_px)
    title_text = "Чёрная тушь BLACK MAMBA с эффектом накладных ресниц от..."
    title_lines = om._wrap(draw, title_text, font_title, text_w, max_lines=2)
    y = block_y + title_top_pad
    line_h = int(title_font_px * p["title_line_h"])
    for line in title_lines:
        draw.text((left_pad, y), line, font=font_title,
                  fill=om.COLOR_TEXT_PRIMARY)
        y += line_h

    # Цена
    y += price_top_gap
    font_price = om._font(om.FONT_BOLD, price_font_px)
    price_str = om._fmt_price(primary)
    price_w = draw.textlength(price_str, font=font_price)

    font_pay = om._font(om.FONT_REGULAR, pay_font_px)
    pay_text = "Пэй"
    pay_w = draw.textlength(pay_text, font=font_pay)

    total_w = price_w + pay_gap_1 + pay_bar_w + pay_gap_2 + pay_w
    start_x = left_pad if p["price_align_left"] else (W - total_w) / 2

    draw.text((start_x, y), price_str, font=font_price,
              fill=om.COLOR_PRICE_GREEN)

    bar_x = start_x + price_w + pay_gap_1
    bar_y = y + int(price_font_px * 0.45)
    om._draw_pay_bar(draw, bar_x, bar_y, width=pay_bar_w, height=pay_bar_h)

    pay_x = bar_x + pay_bar_w + pay_gap_2
    pay_y = bar_y - int(pay_font_px * 0.30)
    draw.text((pay_x, pay_y), pay_text, font=font_pay,
              fill=om.COLOR_PRICE_GREEN)

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


# ============================================================
# Tkinter GUI
# ============================================================

class TunerApp:
    def __init__(self, src_path: Path, aspect: str = "3:4"):
        self.src_path = src_path
        self.aspect = aspect
        self.values = dict(DEFAULTS)
        self.sliders = {}
        self.preview_photo = None
        self._update_job = None

        # Текущий размер canvas (управляется блоком «Размер карточки»)
        self.canvas_w = 1080
        self.canvas_h = 1620

        self.root = tk.Tk()
        self.root.title(f"Card Tuner — {src_path.name}")
        self.root.geometry("1500x950")

        # ============================================================
        # Левая панель — настройки (прокручиваемая)
        # ============================================================
        left = ttk.Frame(self.root, padding=10)
        left.pack(side="left", fill="y")

        canvas = tk.Canvas(left, width=430, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # === Размер карточки (блок наверху) ===
        self._build_size_block(inner)

        # === Ползунки ===
        self._build_group(inner, "Фото", [
            ("photo_h",        300, 1600, 10),
            ("round_radius",   0,   120,  2),
            ("photo_margin",   0,   80,   2),
        ])
        self._build_group(inner, "Название", [
            ("title_font",     30,  120,  2),
            ("title_line_h",   0.8, 2.0,  0.02),
            ("title_top_pad",  0,   200,  5),
            ("title_left_pad", 0,   200,  2),
        ])
        self._build_group(inner, "Цена", [
            ("price_font",     60,  220,  5),
            ("price_top_gap",  0,   200,  5),
            ("price_align_left", 0, 1,   1),
        ])
        self._build_group(inner, "Полоска «Пэй»", [
            ("pay_font",       20,  100,  2),
            ("pay_bar_w",      20,  200,  2),
            ("pay_bar_h",      4,   40,   1),
            ("pay_gap_1",      0,   100,  2),
            ("pay_gap_2",      0,   100,  2),
        ])
        self._build_group(inner, "Бейдж «НИЖЕ РЫНКА»", [
            ("badge_font",     20,  100,  2),
            ("badge_left",     0,   200,  2),
            ("badge_bottom",   0,   400,  5),
        ])
        self._build_group(inner, "Сердечко", [
            ("heart_size",     20,  120,  2),
            ("heart_outline",  0,   30,   1),
            ("heart_right",    0,   300,  5),
            ("heart_top",      0,   300,  5),
        ])

        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))

        # === Кнопки ===
        btns = ttk.Frame(left, padding=(10, 6))
        btns.pack(side="bottom", fill="x")
        ttk.Button(btns, text="💾 Сохранить", command=self.save).pack(
            side="left", fill="x", expand=True, padx=2)
        ttk.Button(btns, text="↺ Сброс", command=self.reset).pack(
            side="left", fill="x", expand=True, padx=2)

        # ============================================================
        # Правая панель — превью
        # ============================================================
        right = ttk.Frame(self.root, padding=10)
        right.pack(side="right", fill="both", expand=True)

        self.info = ttk.Label(right, text="", font=("Arial", 10))
        self.info.pack(side="bottom", pady=4)

        self.preview_label = ttk.Label(right)
        self.preview_label.pack(fill="both", expand=True)

        # Первый рендер
        self.schedule_update(immediate=True)

    # ============================================================
    # UI: блок «Размер карточки»
    # ============================================================

    def _build_size_block(self, parent):
        lf = ttk.LabelFrame(parent, text="Размер карточки", padding=8)
        lf.pack(fill="x", pady=6, padx=2)

        # --- Пресет ---
        row = ttk.Frame(lf)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Пресет", width=17, anchor="w").pack(side="left")

        self.size_var = tk.StringVar(value=SIZE_PRESETS[SIZE_DEFAULT_IDX][0])
        combo = ttk.Combobox(
            row, textvariable=self.size_var,
            values=[p[0] for p in SIZE_PRESETS],
            state="readonly", width=22,
        )
        combo.pack(side="left", fill="x", expand=True)
        combo.bind("<<ComboboxSelected>>", self._on_preset)

        # --- Ширина ---
        row2 = ttk.Frame(lf)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="Ширина, px", width=17, anchor="w").pack(side="left")

        self.w_var = tk.IntVar(value=self.canvas_w)
        w_spin = ttk.Spinbox(
            row2, from_=200, to=4000, increment=10,
            textvariable=self.w_var, width=8,
            command=self._on_size_change,
        )
        w_spin.pack(side="left")
        w_spin.bind("<KeyRelease>", lambda e: self._on_size_change())

        # --- Высота ---
        row3 = ttk.Frame(lf)
        row3.pack(fill="x", pady=4)
        ttk.Label(row3, text="Высота, px", width=17, anchor="w").pack(side="left")

        self.h_var = tk.IntVar(value=self.canvas_h)
        h_spin = ttk.Spinbox(
            row3, from_=200, to=4000, increment=10,
            textvariable=self.h_var, width=8,
            command=self._on_size_change,
        )
        h_spin.pack(side="left")
        h_spin.bind("<KeyRelease>", lambda e: self._on_size_change())

        # --- Пропорция ---
        self.ratio_lbl = ttk.Label(lf, text="", foreground="gray")
        self.ratio_lbl.pack(anchor="w", pady=(4, 0))
        self._update_ratio_label()

    def _on_preset(self, event=None):
        label = self.size_var.get()
        for name, w, h in SIZE_PRESETS:
            if name == label:
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
        ratio = f"{w // g}:{h // g}"
        self.ratio_lbl.config(text=f"Пропорция: {ratio}  ({w}×{h})")

    def _get_canvas_size(self):
        return self.canvas_w, self.canvas_h

    # ============================================================
    # UI: блоки ползунков
    # ============================================================

    def _build_group(self, parent, title, sliders):
        lf = ttk.LabelFrame(parent, text=title, padding=8)
        lf.pack(fill="x", pady=6, padx=2)

        for key, lo, hi, step in sliders:
            row = ttk.Frame(lf)
            row.pack(fill="x", pady=2)

            ttk.Label(row, text=key, width=17, anchor="w").pack(side="left")

            val_lbl = ttk.Label(row, width=6, anchor="e")
            val_lbl.pack(side="right")

            var = tk.DoubleVar(value=self.values[key])
            scale = ttk.Scale(
                row, from_=lo, to=hi, orient="horizontal",
                variable=var,
                command=lambda v, k=key: self._on_change(k, float(v)),
            )
            scale.pack(side="left", fill="x", expand=True, padx=4)

            self.sliders[key] = (var, val_lbl)
            val_lbl.config(text=self._fmt(self.values[key]))

    def _fmt(self, v):
        if isinstance(v, float) and v < 10:
            return f"{v:.2f}"
        return f"{int(v)}"

    def _on_change(self, key, value):
        if key in ("title_line_h",):
            self.values[key] = round(value, 2)
        else:
            self.values[key] = int(round(value))
        self.sliders[key][1].config(text=self._fmt(self.values[key]))
        self.schedule_update()

    # ============================================================
    # Рендер + превью
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
            W, H = self._get_canvas_size()
            data = render_card(self.src_path, self.values, self.aspect,
                               W=W, H=H)
            if not data:
                return
            img = Image.open(io.BytesIO(data))

            # Превью 400×600 (fit, сохраняя пропорции)
            PREVIEW_W, PREVIEW_H = 400, 600
            ratio = min(PREVIEW_W / img.width, PREVIEW_H / img.height)
            new_w = int(img.width * ratio)
            new_h = int(img.height * ratio)
            preview = img.resize((new_w, new_h), Image.LANCZOS)

            self.preview_photo = ImageTk.PhotoImage(preview)
            self.preview_label.config(image=self.preview_photo)
            self.info.config(
                text=f"{self.src_path.name}  •  canvas {W}×{H}  •  "
                     f"preview {new_w}×{new_h}"
            )
        except Exception as e:
            self.info.config(text=f"Ошибка рендера: {e}")

    # ============================================================
    # Кнопки
    # ============================================================

    def reset(self):
        for key, val in DEFAULTS.items():
            self.values[key] = val
            var, lbl = self.sliders[key]
            var.set(val)
            lbl.config(text=self._fmt(val))
        # Сброс размера
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

        W, H = self._get_canvas_size()
        default = out_dir / f"tuned_{W}x{H}.jpg"

        path = filedialog.asksaveasfilename(
            initialdir=str(out_dir),
            initialfile=default.name,
            defaultextension=".jpg",
            filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")],
        )
        if not path:
            return

        data = render_card(self.src_path, self.values, self.aspect,
                           W=W, H=H)
        if not data:
            messagebox.showerror("Ошибка", "Не удалось отрендерить")
            return

        Path(path).write_bytes(data)
        messagebox.showinfo("Сохранено", f"Файл:\n{path}")

        # Печатаем текущие значения в консоль
        print("\n" + "=" * 60)
        print("РАЗМЕР КАРТОЧКИ")
        print("=" * 60)
        print(f"  W × H               = {W} × {H}")
        print()
        print("ПАРАМЕТРЫ (для переноса в overlay_maker.py):")
        print("=" * 60)
        for k, v in self.values.items():
            print(f'  {k:22s} = {v}')
        print("=" * 60)

    def run(self):
        self.root.mainloop()


# ============================================================
# main
# ============================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/card_tuner_old.py <image>")
        print("  (размер карточки выбирается в самом окне)")
        sys.exit(1)

    src = Path(sys.argv[1]).expanduser().resolve()
    if not src.exists():
        print(f"[ERR] Файл не найден: {src}")
        sys.exit(1)

    # Определяем аспект по 2-му аргументу (только для стартового заголовка)
    aspect = sys.argv[2] if len(sys.argv) > 2 else "3:4.5"

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
        print("Шрифты: DejaVu (fallback — YS Text не найден)")

    app = TunerApp(src, aspect=aspect)
    app.run()


if __name__ == "__main__":
    main()