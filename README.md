# Marketplace Parser API

FastAPI-сервис для парсинга товаров с российских маркетплейсов через **HTTP API** (без Selenium).

## Установка

```bash
python -m venv .venv
source .venv/bin/activate      # Linux/WSL
# .venv\Scripts\activate       # Windows

pip install -r requirements.txt
```

## Запуск

```bash
python main.py
# или
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Открыть: http://localhost:8000/docs

## Эндпоинты

| Метод | Путь              | Описание                    |
|-------|-------------------|-----------------------------|
| GET   | `/`               | Информация о сервисе        |
| GET   | `/api/v1/health`  | Проверка работоспособности  |
| POST  | `/api/v1/parse`   | Спарсить товар по URL       |

## Пример запроса

```bash
curl -X POST http://localhost:8000/api/v1/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.ozon.ru/product/test-1234567890"}'
```

Ответ:

```json
{
  "success": true,
  "marketplace": "ozon",
  "sku": "1234567890",
  "title": "[OZON] Тестовый товар #1234567890",
  "discount_price": "1 990₽",
  "old_price": "2 500₽",
  "url": "https://www.ozon.ru/product/test-1234567890",
  "image_url": "https://picsum.photos/seed/1234567890/400/400",
  "post_text": "🔥 **[OZON] Тестовый товар #1234567890**\n\n..."
}
```

## Как заменить заглушку на реальный API

Все парсеры находятся в `parsers/`. У каждого есть 3 метода:

1. **`extract_sku(url)`** — вытащить SKU из URL (уже реализовано).
2. **`_build_api_url(sku)`** — собрать URL запроса к API (уже реализовано).
3. **`_parse_api_response(data, url)`** — распарсить JSON (уже реализовано).

Осталось только **раскомментировать** в `parsers/base.py` реальный `httpx.get()`:

```python
try:
    api_url = self._build_api_url(sku)
    response = self.client.get(api_url)
    response.raise_for_status()
    data = response.json()
    return self._parse_api_response(data, url)
except httpx.HTTPError as e:
    logger.error(f"[{self.marketplace}] HTTP error: {e}")
    return ProductData(url=url, marketplace=self.marketplace, success=False, error=str(e))
```

## Структура

```
core/       — модели и форматирование
parsers/    — парсеры маркетплейсов (API)
services/   — фабрика маркетплейсов
routers/    — FastAPI эндпоинты
config/     — настройки
```

## Лицензия

MIT