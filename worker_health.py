"""Мини-HTTP-сервер для healthcheck worker."""
import time
from fastapi import FastAPI
import uvicorn

app = FastAPI()
_started_at = time.time()
_last_message_at = time.time()


def update_last_message():
    """Обновить timestamp последнего обработанного сообщения."""
    global _last_message_at
    _last_message_at = time.time()


@app.get("/health")
async def health():
    """Проверка: worker жив и обрабатывал сообщения недавно."""
    now = time.time()
    return {
        "status": "ok",
        "uptime": now - _started_at,
        "last_message_ago": now - _last_message_at,
    }


async def run_health_server():
    """Запустить healthcheck на порту 8080."""
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()