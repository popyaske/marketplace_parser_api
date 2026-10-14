"""Точка входа FastAPI"""
import os
import logging
from contextlib import asynccontextmanager
import asyncio

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from core.logging_setup import setup_logging
from routers.parse import router as parse_router
from routers.queue import router as queue_router
from routers.results import router as results_router
from routers.compare import router as compare_router
from routers.parse import factory as marketplace_factory
from routers.messages import router as messages_router
from routers.referral import router as referral_router
from broker.connection import broker
from db import init_engine
from sqlalchemy import text as _sql_text


DB_RETRIES = int(os.getenv("DB_CONNECT_RETRIES", "10"))
DB_RETRY_DELAY = float(os.getenv("DB_CONNECT_DELAY", "3.0"))
RMQ_RETRIES = int(os.getenv("RMQ_CONNECT_RETRIES", "10"))
RMQ_RETRY_DELAY = float(os.getenv("RMQ_CONNECT_DELAY", "3.0"))


setup_logging()
logger = logging.getLogger("app")


async def _retry(coro_factory, name: str, retries: int, delay: float):
    last_err = None
    for i in range(1, retries + 1):
        try:
            await coro_factory()
            logger.info("%s: подключено (попытка %d)", name, i)
            return
        except Exception as e:
            last_err = e
            logger.warning("%s: попытка %d/%d — %s", name, i, retries, e)
            if i < retries:
                await asyncio.sleep(delay)
    raise RuntimeError(f"{name} недоступен после {retries} попыток: {last_err}")


async def _check_db():
    """Просто пингуем БД — миграции применяет alembic."""
    engine = init_engine()
    async with engine.connect() as conn:
        await conn.execute(_sql_text("SELECT 1"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Запуск {settings.APP_NAME} v{settings.VERSION}")
    if os.getenv("TESTING") == "1":
        yield
        return

    await _retry(_check_db, "PostgreSQL", DB_RETRIES, DB_RETRY_DELAY)
    await _retry(broker.connect, "RabbitMQ", RMQ_RETRIES, RMQ_RETRY_DELAY)

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
    allow_credentials=False,
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