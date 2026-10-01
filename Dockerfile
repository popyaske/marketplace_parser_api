# Dockerfile (универсальный)
# ============================================================
# Dockerfile для Marketplace Parser (parser + worker)
# ============================================================
FROM python:3.12-slim

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        ffmpeg \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright + Chromium
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium
RUN chmod -R 755 /ms-playwright

# Код
COPY . .

# Непривилегированный пользователь + каталоги медиа
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/media/photos /app/media/videos /app/media/music \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# По умолчанию — parser. Для worker переопределяем CMD в compose.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]