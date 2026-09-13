"""Фабрика маркетплейсов"""
import re
import logging
from typing import Tuple

from parsers import YandexParser, OzonParser, WildberriesParser, AliexpressParser
from parsers.base import BaseParser

logger = logging.getLogger(__name__)


class MarketplaceFactory:
    """Определяет маркетплейс по URL и возвращает парсер"""

    PATTERNS = {
        "yandex": r"market\.yandex",
        "ozon": r"ozon\.ru",
        "wildberries": r"wildberries\.ru",
        "aliexpress": r"aliexpress\.(ru|com)",
    }

    CREATORS = {
        "yandex": YandexParser,
        "ozon": OzonParser,
        "wildberries": WildberriesParser,
        "aliexpress": AliexpressParser,
    }

    def __init__(self):
        self._parsers: dict[str, BaseParser] = {}

    def identify(self, url: str) -> str:
        for key, pattern in self.PATTERNS.items():
            if re.search(pattern, url, re.IGNORECASE):
                return key
        return "unknown"

    def get_parser(self, url: str) -> Tuple[str, BaseParser]:
        key = self.identify(url)
        if key == "unknown":
            raise ValueError(f"Неизвестный маркетплейс для URL: {url}")
        if key not in self._parsers:
            self._parsers[key] = self.CREATORS[key]()
        return key, self._parsers[key]

    def close_all(self):
        for p in self._parsers.values():
            try:
                p.close()
            except Exception as e:
                logger.warning(f"Ошибка закрытия парсера: {e}")
        self._parsers.clear()