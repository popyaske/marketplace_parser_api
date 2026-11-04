"""Юнит-тесты чистых функций парсеров (без сети)."""
import json
import pytest

from parsers.wildberries import (
    _kopeck_to_rub, _extract_price_rub, _decode_mojibake, _coerce_int,
)
from parsers.ozon import _coerce_price, _parse_widgets


# ---------- Wildberries ----------

class TestWbKopeck:
    def test_int(self):
        assert _kopeck_to_rub(123456) == 1234.56

    def test_str(self):
        assert _kopeck_to_rub("123456") == 1234.56

    def test_zero(self):
        assert _kopeck_to_rub(0) is None

    def test_negative(self):
        assert _kopeck_to_rub(-100) is None

    def test_bool_is_none(self):
        assert _kopeck_to_rub(True) is None

    def test_none(self):
        assert _kopeck_to_rub(None) is None

    def test_non_digit_str(self):
        assert _kopeck_to_rub("abc") is None


class TestWbExtractPrice:
    def test_from_sizes(self):
        p = {"sizes": [{"price": {"product": 100000, "basic": 200000}}]}
        cur, orig = _extract_price_rub(p)
        assert cur == 1000.0
        assert orig == 2000.0

    def test_fallback_priceU(self):
        p = {"salePriceU": 100000, "priceU": 200000}
        cur, orig = _extract_price_rub(p)
        assert cur == 1000.0
        assert orig == 2000.0

    def test_empty(self):
        assert _extract_price_rub({}) == (None, None)

    def test_not_dict(self):
        assert _extract_price_rub("x") == (None, None)


class TestWbMojibake:
    def test_fix_double_encoded(self):
        mojibake = "Тест".encode("utf-8").decode("latin-1")
        assert _decode_mojibake(mojibake) == "Тест"

    def test_clean_unchanged(self):
        assert _decode_mojibake("Тест") == "Тест"

    def test_none(self):
        assert _decode_mojibake(None) == ""


class TestWbInt:
    def test_int(self):
        assert _coerce_int(5) == 5

    def test_str(self):
        assert _coerce_int("42") == 42

    def test_float(self):
        assert _coerce_int(3.7) == 3

    def test_bool_is_none(self):
        assert _coerce_int(True) is None

    def test_garbage(self):
        assert _coerce_int("abc") is None


# ---------- Ozon ----------

class TestOzonCoercePrice:
    def test_int(self):
        assert _coerce_price(1500) == 1500.0

    def test_str_with_spaces(self):
        assert _coerce_price("1 500 ₽") == 1500.0

    def test_zero_is_none(self):
        assert _coerce_price(0) is None

    def test_bool_is_none(self):
        assert _coerce_price(False) is None

    def test_none(self):
        assert _coerce_price(None) is None


class TestOzonParseWidgets:
    def test_full_payload(self):
        payload = {
            "widgetStates": {
                "webPrice-123": json.dumps({
                    "price": "1 500 ₽",
                    "cardPrice": "1 200 ₽",
                    "originalPrice": "2 000 ₽",
                    "isAvailable": True,
                }),
                "webProductHeading-1": json.dumps({
                    "title": "Наушники Sony",
                    "seller": {"name": "М.Видео"},
                }),
                "webGallery-1": json.dumps({
                    "coverImage": "https://cdn.ozon.ru/img.jpg",
                }),
                "webReviewProductScore-1": json.dumps({
                    "totalScore": 4.8,
                    "reviewsCount": 1234,
                }),
            }
        }
        out = _parse_widgets(payload)
        assert out["price"] == 1500.0
        assert out["card_price"] == 1200.0
        assert out["price_original"] == 2000.0
        assert out["title"] == "Наушники Sony"
        assert out["seller"] == "М.Видео"
        assert out["rating_score"] == 4.8
        assert out["rating_count"] == 1234
        assert out["image_url"] == "https://cdn.ozon.ru/img.jpg"
        assert out["is_available"] is True

    def test_empty(self):
        assert _parse_widgets({"widgetStates": {}}) == {}

    def test_missing_widgets(self):
        assert _parse_widgets({}) == {}

    def test_broken_json_skipped(self):
        payload = {"widgetStates": {"webPrice-1": "not a json"}}
        assert _parse_widgets(payload) == {}