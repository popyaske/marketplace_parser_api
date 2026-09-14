"""Базовый класс парсера"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

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