"""Точка входа FastAPI"""
import os
import logging
from contextlib import asynccontextmanager

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from routers.parse import router as parse_router
from routers.queue import router as queue_router
from routers.results import router as results_router
from routers.compare import router as compare_router
from routers.parse import factory as marketplace_factory
from routers.messages import router as messages_router
from routers.referral import router as referral_router
from broker.connection import broker
from db import create_tables


logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Запуск {settings.APP_NAME} v{settings.VERSION}")

    # В тестах не подключаемся к БД/RabbitMQ
    if os.getenv("TESTING") == "1":
        logger.info("TESTING=1 — пропускаю подключения к БД и RabbitMQ")
        yield
        return

    try:
        await create_tables()
        logger.info("Таблицы БД готовы")
    except Exception as e:
        logger.warning(f"Не удалось создать таблицы: {e}")

    try:
        await broker.connect()
        logger.info("RabbitMQ подключён")
    except Exception as e:
        logger.warning(f"RabbitMQ недоступен: {e}")

    yield

    logger.info("Остановка, закрываем соединения")
    try:
        await broker.close()
    except Exception:
        pass
    marketplace_factory.close_all()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(parse_router)
app.include_router(queue_router)
app.include_router(results_router)
app.include_router(compare_router)
app.include_router(messages_router)
app.include_router(referral_router)


limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.get("/", tags=["root"])
async def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "dashboard": "/api/v1/results",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )