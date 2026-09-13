"""Настройки приложения"""
import os


class Settings:
    APP_NAME = "Marketplace Parser API"
    VERSION = "1.0.0"
    DESCRIPTION = "API для парсинга товаров с маркетплейсов"

    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8000"))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # Таймауты для API-запросов (в секундах)
    API_TIMEOUT = 15
    API_RETRIES = 3

    # User-Agent для запросов
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # CORS
    ALLOWED_ORIGINS = ["*"]


settings = Settings()