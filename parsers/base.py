"""Базовый класс парсера"""
import logging
from abc import ABC, abstractmethod
from typing import Optional
from cachetools import TTLCache
import hashlib

from core.models import ProductData

logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """
    Базовый класс для парсеров маркетплейсов.

    Каждый парсер реализует extract_sku() и fetch().
    fetch() — главный метод, возвращает ProductData.
    """

    marketplace: str = "unknown"
    base_url: str = ""
    _cache: TTLCache = TTLCache(maxsize=500, ttl=300)

    @abstractmethod
    def extract_sku(self, url: str) -> Optional[str]:
        """Извлечь SKU из URL"""
        ...

    @abstractmethod
    def fetch(self, url: str) -> ProductData:
        """Получить данные товара по URL"""
        ...

    def close(self):
        pass

    def fetch_cached(self, url: str) -> ProductData:
        """Fetch с TTL-кэшем (5 минут)."""
        key = hashlib.md5(url.encode()).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self.fetch(url)
        if result.success:
            self._cache[key] = result
        return result