"""Точка входа FastAPI"""
import logging
from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from routers.parse import router as parse_router
from routers.queue import router as queue_router
from routers.parse import factory as marketplace_factory
from broker.connection import broker


logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Запуск {settings.APP_NAME} v{settings.VERSION}")

    # Подключаемся к RabbitMQ с retry (не блокируем старт при недоступности)
    try:
        await asyncio.wait_for(broker.connect(), timeout=60.0)
        logger.info("RabbitMQ подключён")
    except Exception as e:
        logger.warning(f"RabbitMQ недоступен: {e}. Queue API будет возвращать ошибки.")

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
    description=settings.DESCRIPTION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(parse_router)
app.include_router(queue_router)


@app.get("/", tags=["root"])
async def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "health": "/api/v1/health",
        "queue": "/api/v1/queue/status",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
        log_level=settings.LOG_LEVEL.lower(),
    )