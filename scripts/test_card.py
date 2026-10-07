"""
Тест рендера карточки через overlay_maker (без патчей).

Просто вызывает services.overlay_maker.render_and_save().

Запуск:
    python scripts/test_card.py test.jpg
    python scripts/test_card.py test.jpg out/card.jpg
    python scripts/test_card.py test.jpg out/card.jpg 3:4.5
    python scripts/test_card.py test.jpg out/card.jpg 1080 1620

Формат аргументов:
    <image>                — обязательно
    [output]               — путь к результату
    [aspect|W]             — 3:4 | 3:4.5 | 3:5 | 4:5 | 9:16
                             или ширина в px (если передан 4-й аргумент)
    [H]                    — высота в px (опционально)
"""
import os
import sys
from pathlib import Path

# === Автодобавление корня проекта в sys.path ===
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Папка шрифтов — локальная ./fonts
_LOCAL_FONTS_DIR = Path(os.getenv("FONTS_DIR") or (PROJECT_ROOT / "fonts"))
os.environ["FONTS_DIR"] = str(_LOCAL_FONTS_DIR)

from services.overlay_maker import (   # noqa: E402
    render_and_save,
    FONT_REGULAR, FONT_BOLD, FONT_BOLD_ITALIC,
)


# ============================================================
# Тестовые данные
# ============================================================
TEST_TITLE      = "Чёрная тушь BLACK MAMBA с эффектом накладных ресниц от..."
TEST_PRICE      = 1100.0
TEST_PRICE_PLUS = 1100.0    # None — убрать «Пэй»
TEST_PRICE_OLD  = 1500.0    # None — убрать бейдж «НИЖЕ РЫНКА»
TEST_ASPECT     = "3:4.5"


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_card.py <image> [output] [aspect | W] [H]")
        print("  aspect: 3:4 | 3:4.5 | 3:5 | 4:5 | 9:16")
        sys.exit(1)

    src = Path(sys.argv[1]).expanduser().resolve()
    if not src.exists():
        print(f"[ERR] Файл не найден: {src}")
        sys.exit(1)

    aspect = TEST_ASPECT
    if len(sys.argv) > 3:
        arg3 = sys.argv[3]
        if arg3.isdigit() and len(sys.argv) > 4 and sys.argv[4].isdigit():
            aspect = f"{arg3}x{sys.argv[4]}"     # ручной размер
        else:
            aspect = arg3

    if len(sys.argv) > 2:
        dst = Path(sys.argv[2]).expanduser().resolve()
    else:
        out_dir = PROJECT_ROOT / "out"
        out_dir.mkdir(exist_ok=True)
        safe = aspect.replace(":", "x")
        dst = out_dir / f"test_card_{safe}.jpg"

    dst.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print(f"  Шрифт regular:  {FONT_REGULAR}")
    print(f"  Шрифт bold:     {FONT_BOLD}")
    print(f"  Шрифт italic:   {FONT_BOLD_ITALIC}")
    print("-" * 68)
    print(f"  Источник:       {src}")
    print(f"  Результат:      {dst}")
    print(f"  Формат:         {aspect}")
    print("-" * 68)
    print(f"  Название:       {TEST_TITLE}")
    print(f"  Цена:           {TEST_PRICE} ₽")
    print(f"  Цена с Пэй:     {TEST_PRICE_PLUS if TEST_PRICE_PLUS else '—'}")
    print(f"  Старая цена:    {TEST_PRICE_OLD if TEST_PRICE_OLD else '—'}")
    print("=" * 68)

    ok = render_and_save(
        str(src), str(dst),
        title=TEST_TITLE,
        price_rub=TEST_PRICE,
        price_with_plus=TEST_PRICE_PLUS,
        price_original_rub=TEST_PRICE_OLD,
        aspect=aspect,
    )

    if ok:
        print(f"[OK] Готово — {dst.stat().st_size / 1024:.1f} KB")
        print(f"Открой: {dst}")
    else:
        print("[FAIL] Рендер не удался")
        sys.exit(2)


if __name__ == "__main__":
    main()