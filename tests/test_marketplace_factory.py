"""Тесты фабрики маркетплейсов."""
import pytest
from services.marketplace import MarketplaceFactory


class TestIdentify:
    @pytest.mark.parametrize("url,expected", [
        ("https://market.yandex.ru/product--x/123", "yandex"),
        ("https://market.yandex.ru/search?text=x", "yandex"),
        ("https://www.ozon.ru/product/123", "ozon"),
        ("https://ozon.ru/product/123", "ozon"),
        ("https://www.wildberries.ru/catalog/123/detail.aspx", "wildberries"),
        ("https://aliexpress.ru/item/123.html", "aliexpress"),
        ("https://aliexpress.com/item/123.html", "aliexpress"),
        ("https://example.com/product/1", "unknown"),
        ("not a url", "unknown"),
    ])
    def test_identify(self, url, expected):
        assert MarketplaceFactory().identify(url) == expected


class TestGetParser:
    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            MarketplaceFactory().get_parser("https://example.com/")

    def test_caching_same_instance(self):
        f = MarketplaceFactory()
        k1, p1 = f.get_parser("https://www.ozon.ru/product/1")
        k2, p2 = f.get_parser("https://www.ozon.ru/product/2")
        assert k1 == k2 == "ozon"
        assert p1 is p2
        f.close_all()

    def test_close_all_clears(self):
        f = MarketplaceFactory()
        f.get_parser("https://www.ozon.ru/product/1")
        f.close_all()
        assert f._parsers == {}