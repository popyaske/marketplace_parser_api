# ============================================================
# Dockerfile для Marketplace Parser API
# ============================================================
FROM python:3.12-slim

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Сначала зависимости (для кэша слоёв)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir websocket-client

# Копируем код
COPY . .

# Непривилегированный пользователь
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Порт FastAPI
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/health || exit 1

# Запуск
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]