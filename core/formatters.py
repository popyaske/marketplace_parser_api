"""Форматирование цен и генерация текста поста"""
import re
from .models import ProductData


class PriceFormatter:
    """Утилиты для форматирования цен"""

    @staticmethod
    def format_price(price_str: str) -> str:
        """Форматирует цену с разделителем тысяч и знаком ₽"""
        if not price_str or price_str in ("Не найдено", "Цена со скидкой", "Цена без скидки"):
            return price_str

        # Убираем все лишнее, оставляем цифры
        digits = re.sub(r'\D', '', price_str)
        if not digits:
            return price_str

        try:
            number = int(digits)
        except ValueError:
            return price_str

        if number >= 1000:
            formatted = f"{number:,}".replace(',', ' ')
        else:
            formatted = str(number)

        return f"{formatted}₽"


class TextGenerator:
    """Генератор текста для Telegram-постов"""

    TEMPLATE = (
        "🔥 **{title}**\n\n"
        "Цена 🔥 зависит от аккаунта❗️\n\n"
        "Старая цена — {old_price}\n"
        "Новая цена — {discount_price}\n\n"
        "🙈 {url}\n\n"
        "🤫 [Чат](https://t.me/+uVvV1JcRjF03YjIy)"
    )

    @classmethod
    def generate(cls, product: ProductData) -> str:
        """Генерация готового текста поста"""
        fmt = PriceFormatter.format_price

        if product.price_rub is not None:
            new_price_str = fmt(str(product.price_rub))
        elif product.price_with_plus is not None:
            new_price_str = f"{fmt(str(product.price_with_plus))} (с подпиской)"
        else:
            new_price_str = "Не найдено"

        return cls.TEMPLATE.format(
            title=product.title,
            old_price=fmt(str(product.price_original_rub)) if product.price_original_rub else "Не найдено",
            discount_price=new_price_str,
            url=product.url,
        )