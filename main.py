"""Точка входа FastAPI"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from routers.parse import router as parse_router
from routers.queue import router as queue_router
from routers.results import router as results_router
from routers.parse import factory as marketplace_factory
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

    # БД
    try:
        await create_tables()
        logger.info("Таблицы БД готовы")
    except Exception as e:
        logger.warning(f"Не удалось создать таблицы: {e}")

    # RabbitMQ
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