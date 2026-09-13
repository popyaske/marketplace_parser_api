"""Базовый класс парсера через API"""
import logging
import httpx
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from core.models import ProductData
from config.settings import settings

logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """
    Базовый класс для парсеров, работающих через HTTP API.

    Наследники должны реализовать метод fetch() — реальный запрос
    к API маркетплейса. Пока это заглушки.
    """

    marketplace: str = "unknown"
    base_url: str = ""

    def __init__(self):
        self.client = httpx.Client(
            timeout=settings.API_TIMEOUT,
            headers={
                "User-Agent": settings.USER_AGENT,
                "Accept": "application/json",
            },
        )

    @abstractmethod
    def _build_api_url(self, sku: str) -> str:
        """Собрать URL API-запроса по SKU товара"""
        ...

    @abstractmethod
    def _parse_api_response(self, data: Dict[str, Any], url: str) -> ProductData:
        """Преобразовать ответ API в ProductData"""
        ...

    def extract_sku(self, url: str) -> Optional[str]:
        """Извлечь SKU из URL товара"""
        raise NotImplementedError

    def fetch(self, url: str) -> ProductData:
        """
        Основной метод: получить данные товара по URL.

        Сейчас это ЗАГЛУШКА — возвращает тестовые данные.
        Замените тело метода на реальный вызов self.client.get(...).
        """
        sku = self.extract_sku(url) or "000000"

        # === ЗАГЛУШКА ===
        # Реальный код должен быть:
        #
        # try:
        #     api_url = self._build_api_url(sku)
        #     response = self.client.get(api_url)
        #     response.raise_for_status()
        #     data = response.json()
        #     return self._parse_api_response(data, url)
        # except httpx.HTTPError as e:
        #     logger.error(f"[{self.marketplace}] HTTP error: {e}")
        #     return ProductData(
        #         url=url, marketplace=self.marketplace,
        #         success=False, error=str(e),
        #     )
        # === /ЗАГЛУШКА ===

        return self._mock_response(sku, url)

    def _mock_response(self, sku: str, url: str) -> ProductData:
        """Возвращает заглушечные данные товара"""
        return ProductData(
            title=f"[{self.marketplace.upper()}] Тестовый товар #{sku}",
            discount_price="1 990",
            old_price="2 500",
            url=url,
            marketplace=self.marketplace,
            sku=sku,
            image_url=f"https://picsum.photos/seed/{sku}/400/400",
            success=True,
        )

    def close(self):
        self.client.close()