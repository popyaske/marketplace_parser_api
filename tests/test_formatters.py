"""Тесты PriceFormatter и TextGenerator."""
import pytest
from core.models import ProductData
from core.formatters import PriceFormatter, TextGenerator


class TestPriceFormatter:
    def test_thousands(self):
        assert PriceFormatter.format_price("1213") == "1 213₽"

    def test_with_spaces(self):
        assert PriceFormatter.format_price("2 499 ₽") == "2 499₽"

    def test_small(self):
        assert PriceFormatter.format_price("99") == "99₽"

    def test_empty(self):
        assert PriceFormatter.format_price("") == ""

    def test_garbage(self):
        assert PriceFormatter.format_price("abc") == "abc"

    def test_zero(self):
        assert PriceFormatter.format_price("0") == "0"

    def test_negative(self):
        assert PriceFormatter.format_price("-100") == "100₽"


class TestTextGenerator:
    def test_basic_ozon(self):
        p = ProductData(
            title="Наушники",
            url="https://ozon.ru/product/123",
            marketplace="ozon",
            price_rub=1500,
        )
        text = TextGenerator.generate(p)
        assert "Наушники" in text
        assert "1 500₽" in text
        assert "#Ozon" in text
        assert "ozon.ru/product/123" in text

    def test_old_and_new(self):
        p = ProductData(
            title="X", url="u", marketplace="wildberries",
            price_rub=1000, price_original_rub=2000,
        )
        text = TextGenerator.generate(p)
        assert "Было: 2 000₽" in text
        assert "Стало: **1 000₽**" in text

    def test_plus_only(self):
        p = ProductData(
            title="X", url="u", marketplace="yandex",
            price_with_plus=500,
        )
        text = TextGenerator.generate(p)
        assert "500₽" in text
        assert "Было" not in text

    def test_yandex_uses_share_url(self):
        p = ProductData(
            title="X",
            url="https://market.yandex.ru/product--x/123",
            share_url="https://yandex.ru/share/abc",
            marketplace="yandex",
            price_rub=100,
        )
        text = TextGenerator.generate(p)
        assert "yandex.ru/share/abc" in text
        assert "product--x/123" not in text

    def test_no_price(self):
        p = ProductData(title="X", url="u", marketplace="ozon")
        text = TextGenerator.generate(p)
        assert "Не найдено" in text

    def test_unknown_marketplace_no_hashtag(self):
        p = ProductData(title="X", url="https://shop.example", price_rub=100)
        text = TextGenerator.generate(p)
        assert "#" not in text.split("Чат")[0]  # нет тега маркетплейса

    def test_fallback_hashtag_from_url(self):
        p = ProductData(
            title="X",
            url="https://www.wildberries.ru/catalog/123",
            marketplace="unknown",
            price_rub=100,
        )
        text = TextGenerator.generate(p)
        assert "#Wildberries" in text