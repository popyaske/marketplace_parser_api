"""Форматирование цен и генерация текста поста"""
import re
from .models import ProductData


class PriceFormatter:
    """Утилиты для форматирования цен"""

    @staticmethod
    def format_price(price_str: str) -> str:
        """Форматирует цену с разделителем тысяч и знаком ₽."""
        if not price_str or price_str in ("Не найдено", "Цена со скидкой", "Цена без скидки"):
            return price_str

        # Убираем всё, кроме цифр и точки
        cleaned = re.sub(r'[^\d.]', '', str(price_str))
        if not cleaned:
            return price_str

        try:
            number = float(cleaned)
        except ValueError:
            return price_str

        if number <= 0:
            return price_str

        # Форматируем с разделителем тысяч (без копеек)
        if number >= 1000:
            formatted = f"{int(number):,}".replace(',', ' ')
        else:
            formatted = str(int(number))

        return f"{formatted}₽"


class TextGenerator:
    """Генератор текста для Telegram-постов"""

    # Хештеги по маркетплейсам
    HASHTAGS = {
        "wildberries": "#Wildberries",
        "ozon": "#Ozon",
        "yandex": "#ЯндексМаркет",
        "aliexpress": "#AliExpress",
    }

    TEMPLATE = (
        "🔥 **{title}**\n\n"
        "{old_price_line}"
        "Стало: **{discount_price}** 🔥\n\n"
        "⚠️ Итоговая цена зависит от аккаунта\n\n"
        "🛒 [Забрать товар]({url})\n\n"
        "💬 [Чат для обсуждений](https://t.me/PromoMP1_chat)"
    )

    @classmethod
    def _get_hashtag(cls, product: ProductData) -> str:
        """
        Определяет хештег по маркетплейсу.
        Сначала по полю product.marketplace, затем по URL (fallback).
        """
        # 1. По полю marketplace (парсеры его заполняют)
        marketplace = (product.marketplace or "").lower()
        hashtag = cls.HASHTAGS.get(marketplace)
        if hashtag:
            return hashtag

        # 2. Fallback — по URL
        url = (product.url or "").lower()
        if "wildberries" in url:
            return cls.HASHTAGS["wildberries"]
        if "ozon" in url:
            return cls.HASHTAGS["ozon"]
        if "yandex" in url or "market.yandex" in url:
            return cls.HASHTAGS["yandex"]
        if "aliexpress" in url:
            return cls.HASHTAGS["aliexpress"]

        return ""

    @staticmethod
    def _fmt(price) -> str | None:
        """Форматирует число в '1 213₽'. Возвращает None, если цены нет."""
        if price is None:
            return None
        try:
            return f"{int(price):,}".replace(",", " ") + "₽"
        except (TypeError, ValueError):
            return None

    @classmethod
    def generate(cls, product: ProductData) -> str:
        """
        Генерация готового текста поста.

        Логика «Было / Стало»:
          1. Если есть зачёркнутая цена (price_original_rub):
             Было = зачёркнутая, Стало = обычная (price_rub)
          2. Если зачёркнутой нет, но есть цена с картой (price_with_plus):
             Было = обычная, Стало = с картой
          3. Если есть только обычная:
             Стало = обычная, без строки «Было»
          4. Если есть только с картой:
             Стало = с картой, без строки «Было»
          5. Если ничего — «Стало: Не найдено»
        """
        price_rub = cls._fmt(product.price_rub)
        price_plus = cls._fmt(product.price_with_plus)
        price_original = cls._fmt(product.price_original_rub)

        # Определяем "Было" и "Стало"
        if price_original and price_rub:
            old_price_line = f"Было: {price_original}\n"
            discount_price = price_rub
        elif price_rub and price_plus:
            old_price_line = f"Было: {price_rub}\n"
            discount_price = price_plus
        elif price_rub:
            old_price_line = ""
            discount_price = price_rub
        elif price_plus:
            old_price_line = ""
            discount_price = price_plus
        else:
            old_price_line = ""
            discount_price = "Не найдено"

        marketplace = (product.marketplace or "").lower()
        if marketplace == "yandex" and getattr(product, "share_url", None):
            url_for_post = product.share_url
        else:
            url_for_post = product.url

        # Собираем текст
        text = cls.TEMPLATE.format(
            title=product.title,
            old_price_line=old_price_line,
            discount_price=discount_price,
            url=url_for_post,
        )

        # Хештег в конце
        hashtag = cls._get_hashtag(product)
        if hashtag:
            text += f"\n\n{hashtag}"

        return text