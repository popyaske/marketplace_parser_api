"""Базовый класс парсера"""

import time
import json
import threading
from cachetools import TTLCache
import logging
from abc import ABC, abstractmethod
from typing import Optional
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
    _cache: TTLCache
    _cache_lock: threading.Lock

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # каждому подклассу — свой кэш и свой лок
        cls._cache = TTLCache(maxsize=500, ttl=300)
        cls._cache_lock = threading.Lock()

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
        key = hashlib.md5(url.encode()).hexdigest()
        with self._cache_lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self.fetch(url)
        if result.success:
            with self._cache_lock:
                self._cache[key] = result
        return result


def cdp_wait_for_load(ws, *, timeout_s: float = 20.0,
                      url_contains: str | None = None) -> bool:
    """
    Читает поток WS и ждёт Page.loadEventFired (или совпадения URL во frameNavigated).
    Не спит фиксированные секунды — выходит как только страница готова.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ws.settimeout(max(0.1, min(0.5, deadline - time.monotonic())))
        try:
            raw = ws.recv()
        except Exception:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = msg.get("method", "")
        if method == "Page.loadEventFired":
            return True
        if url_contains and method == "Page.frameNavigated":
            frame = (msg.get("params") or {}).get("frame") or {}
            if url_contains in (frame.get("url") or ""):
                return True
    return False