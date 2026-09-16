# Marketplace Parser API

FastAPI-сервис для парсинга товаров с российских и китайских маркетплейсов с **асинхронной обработкой через RabbitMQ**, **сохранением в PostgreSQL** и **React-дашбордом**.

**Поддерживается 5 маркетплейсов:**

| Маркетплейс | Метод | Что доступно |
|-------------|-------|--------------|
| 🟦 **Ozon** | Chrome CDP + composer-api.bx | Карточки, цены, рейтинги |
| 🟪 **Wildberries** | Chrome CDP + card.wb.ru v4 | Карточки, цены, рейтинги |
| 🟧 **AliExpress** | Chrome CDP + чтение DOM | Карточки, цены, рейтинги |
| 🟨 **Яндекс Маркет** | SSR (widget state) | Карточки, цены |
| 🟨 **Яндекс Маркет поиск** | SSR + fallback на карточку | Первый товар из выдачи |

Сервис возвращает цены (обычная + подписочная + зачёркнутая), рейтинги, продавцов, бренды, картинки и **готовый пост для Telegram** — в одном JSON-ответе.

---

## 🏗️ Архитектура

```
┌────────────────────┐
│   React Dashboard  │  http://localhost:3000
│   (nginx)          │
└──────────┬─────────┘
           │ POST /api/v1/queue/enqueue
           │ GET  /api/v1/results
           ▼
┌────────────────────┐
│   FastAPI Parser   │  http://localhost:8000
│   + Swagger        │  /docs
└──────────┬─────────┘
           │ publish
           ▼
┌────────────────────┐
│   RabbitMQ         │  http://localhost:15672
│   parser.incoming  │  guest/guest
└──────────┬─────────┘
           │ consume
           ▼
┌────────────────────┐
│   Worker           │
│   httpx → CDP      │
│   parse → publish  │
└──────────┬─────────┘
           │ publish + save
           ▼
┌────────────────────┐     ┌──────────────────┐
│   RabbitMQ         │     │   PostgreSQL     │
│   parser.results   │     │   products       │
└────────────────────┘     └──────────────────┘
                                    ▲
                                    │ SELECT
                                    │
                           ┌────────┴─────────┐
                           │   Dashboard      │
                           │   GET /results   │
                           └──────────────────┘
```

---

## 📋 Требования

- **Docker Desktop** (Windows / macOS) или **Docker + Docker Compose** (Linux)
- **Google Chrome** или **Microsoft Edge** (для CDP на хосте)
- **8 GB RAM** минимум, **16 GB** рекомендуется
- Свободное место: ~3 GB

---

## ⚡ Быстрый старт

### 1. Клонировать репозиторий

```bash
git clone <your-repo-url>
cd channel_promo
```

### 2. Запустить Chrome с CDP

Chrome с CDP нужен для обхода Cloudflare на Ozon, Wildberries и AliExpress.

**Windows** — создайте `start-chrome-cdp.bat`:

```batch
@echo off
echo Starting Chrome with CDP on port 9222...

set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" (
    echo ERROR: Chrome not found.
    exit /b 1
)

if not exist "%USERPROFILE%\chrome-scraping" mkdir "%USERPROFILE%\chrome-scraping"

start "" "%CHROME%" ^
    --remote-debugging-port=9222 ^
    --remote-debugging-address=0.0.0.0 ^
    --remote-allow-origins=* ^
    --user-data-dir="%USERPROFILE%\chrome-scraping" ^
    --no-first-run ^
    --no-default-browser-check

echo Waiting for CDP...
timeout /t 5 /nobreak >nul

curl -s http://127.0.0.1:9222/json/version
```

**Linux / macOS** — создайте `start-chrome-cdp.sh`:

```bash
#!/usr/bin/env bash
CHROME="${CHROME_BINARY:-google-chrome}"
PROFILE="$HOME/.local/share/chrome-scraping"

mkdir -p "$PROFILE"
"$CHROME" \
    --remote-debugging-port=9222 \
    --remote-debugging-address=0.0.0.0 \
    --remote-allow-origins='*' \
    --user-data-dir="$PROFILE" \
    --no-first-run \
    --no-default-browser-check &

sleep 5
curl -s http://127.0.0.1:9222/json/version
```

**Обязательные флаги:**

| Флаг | Зачем |
|------|-------|
| `--remote-debugging-port=9222` | Включает CDP на порту 9222 |
| `--remote-debugging-address=0.0.0.0` | **Обязательно для Docker** — слушать все интерфейсы |
| `--remote-allow-origins=*` | **Обязательно** — без него WebSocket вернёт 403 |
| `--user-data-dir=...` | Изолированный профиль (не трогает личный Chrome) |
| `--no-first-run` | Пропустить приветствие |

### 3. Прогреть сессии в Chrome

В **открывшемся** Chrome (отдельный профиль, не ваш личный) откройте по очереди:

```
https://www.ozon.ru/          — подождать 5–10 сек
https://www.wildberries.ru/   — подождать 5–10 сек
https://aliexpress.ru/        — пройти x5sec, если появится
```

Если увидите капчу — **пройдите вручную**. Chrome **не закрывайте** — он нужен для работы парсера.

### 4. Узнать IP хоста для Docker

**Windows**: адрес хоста для Docker Desktop обычно `192.168.65.254`.

Проверить можно так:

```cmd
docker compose exec parser cat /etc/hosts | findstr host.docker.internal
```

Первая строка с IPv4 — это адрес хоста. Запишите его.

### 5. Настроить `docker-compose.yml`

**Откройте `docker-compose.yml`**, найдите блок `parser` → `environment`:

```yaml
    environment:
      CDP_HOST: 192.168.65.254   # ← ваш IP хоста
      CDP_PORT: 9222
```

Замените `192.168.65.254` на ваш IP (из шага 4).

### 6. Запустить всё одной командой

```cmd
docker compose up -d --build
```

Первый запуск займёт **2–3 минуты** (сборка образов).

### 7. Проверить

```cmd
docker compose ps
```

**Ожидаемо:**

```
NAME                    STATUS              PORTS
marketplace-frontend    Up                  0.0.0.0:3000->80/tcp
marketplace-parser      Up (healthy)        0.0.0.0:8000->8000/tcp
marketplace-postgres    Up (healthy)        5432/tcp
marketplace-rabbitmq    Up (healthy)        0.0.0.0:5672->5672/tcp, 0.0.0.0:15672->15672/tcp
marketplace-worker      Up
```

---

## 🌐 Что доступно

| Сервис | URL | Логин |
|--------|-----|-------|
| **Дашборд** | http://localhost:3000 | — |
| **API + Swagger** | http://localhost:8000/docs | — |
| **RabbitMQ UI** | http://localhost:15672 | guest / guest |
| **PostgreSQL** | `localhost:5432` | parser / parser |
| **Chrome CDP** | http://localhost:9222 | — |

---

## 📡 API Endpoints

### Парсинг (синхронно)

| Метод | Путь | Описание |
|-------|------|----------|
| `POST` | `/api/v1/parse` | Спарсить URL, вернуть результат сразу |

### Очередь (асинхронно)

| Метод | Путь | Описание |
|-------|------|----------|
| `POST` | `/api/v1/queue/enqueue` | Отправить URL в очередь |
| `POST` | `/api/v1/queue/enqueue-batch` | Отправить список URL |
| `GET` | `/api/v1/queue/status` | Статус очередей |

### Результаты (для дашборда)

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/api/v1/results` | Список результатов с фильтрами |
| `GET` | `/api/v1/results/stats` | Статистика |
| `GET` | `/api/v1/results/{id}` | Один товар |
| `DELETE` | `/api/v1/results/{id}` | Удалить товар |

### Служебные

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/` | Информация о сервисе |
| `GET` | `/api/v1/health` | Проверка работоспособности |
| `GET` | `/api/v1/cdp-check` | Проверка доступности Chrome CDP |

---

## 🧪 Примеры запросов

### Синхронный парсинг (сразу ответ)

```bash
curl -X POST http://localhost:8000/api/v1/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.ozon.ru/product/142701653/"}'
```

### Асинхронный парсинг (через очередь)

```bash
curl -X POST http://localhost:8000/api/v1/queue/enqueue \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.ozon.ru/product/142701653/"}'
```

### Batch-отправка

```bash
curl -X POST http://localhost:8000/api/v1/queue/enqueue-batch \
  -H "Content-Type: application/json" \
  -d '{"urls": [
    "https://www.ozon.ru/product/142701653/",
    "https://www.wildberries.ru/catalog/219498821/detail.aspx",
    "https://market.yandex.ru/product/6113276549"
  ]}'
```

### Список результатов с фильтрами

```bash
# Все товары
curl http://localhost:8000/api/v1/results

# Только Wildberries
curl "http://localhost:8000/api/v1/results?marketplace=wildberries"

# Поиск по названию
curl "http://localhost:8000/api/v1/results?search=масло"

# Диапазон цен
curl "http://localhost:8000/api/v1/results?min_price=100&max_price=2000"

# Пагинация
curl "http://localhost:8000/api/v1/results?page=2&per_page=10"
```

### Windows CMD — экранирование кавычек

```cmd
curl -X POST http://localhost:8000/api/v1/parse -H "Content-Type: application/json" -d "{\"url\": \"https://www.ozon.ru/product/142701653/\"}"
```

Если URL содержит `&` — экранируйте через `^&`:

```cmd
-d "{\"url\": \"https://www.ozon.ru/product/123/?at=xxx^&sh=yyy\"}"
```

---

## 📤 Формат ответа

### `/api/v1/parse` (синхронный)

```json
{
  "success": true,
  "marketplace": "ozon",
  "sku": "142701653",
  "title": "Наполнитель глиняный для кошачьего туалета Pi-Pi Bent Profi",
  "price_rub": 1147.0,
  "price_with_plus": 1095.0,
  "price_original_rub": 1220.0,
  "seller": "Ozon Россия",
  "brand": "",
  "rating": 4.9,
  "rating_count": 204983,
  "in_stock": true,
  "image_url": "https://ir.ozone.ru/s3/multimedia-1-i/7587071586.jpg",
  "url": "https://www.ozon.ru/product/142701653/",
  "tier_used": "cdp",
  "post_text": "🔥 **Наполнитель глиняный...**\n\nСтарая цена — 1 220₽\nНовая цена — 1 147₽\n\n🙈 https://www.ozon.ru/product/142701653/\n\n🤫 [Чат](https://t.me/+uVvV1JcRjF03YjIy)",
  "error": null
}
```

### `/api/v1/results` (с фильтрами)

```json
{
  "items": [
    {
      "id": 1,
      "url": "https://www.ozon.ru/product/142701653/",
      "marketplace": "ozon",
      "sku": "142701653",
      "title": "Наполнитель глиняный...",
      "price_rub": 1147.0,
      "price_with_plus": 1041.0,
      "price_original_rub": 1220.0,
      "seller": "Ozon Россия",
      "rating": 4.9,
      "rating_count": 206669,
      "in_stock": true,
      "image_url": "https://ir.ozone.ru/s3/multimedia-1-i/7587071586.jpg",
      "tier_used": "cdp",
      "post_text": "🔥 **Наполнитель глиняный...**",
      "success": true,
      "error": null,
      "created_at": "2026-09-16T18:42:24.190268Z"
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 20,
  "pages": 1
}
```

### Поля ответа

| Поле | Описание |
|------|----------|
| `success` | `true` если товар получен |
| `marketplace` | `ozon` / `wildberries` / `aliexpress` / `yandex` |
| `sku` | Артикул товара |
| `title` | Название |
| `price_rub` | **Обычная цена** — что платит любой покупатель |
| `price_with_plus` | Цена с подпиской (Ozon Card / Яндекс Плюс) |
| `price_original_rub` | Зачёркнутая цена «до скидки» |
| `seller` | Продавец |
| `brand` | Бренд |
| `rating` | Рейтинг (1–5) |
| `rating_count` | Количество оценок |
| `in_stock` | Есть ли в наличии |
| `image_url` | Главная картинка товара |
| `tier_used` | `cdp` / `cdp_dom` / `ssr` / `ssr_card` |
| `post_text` | Готовый пост для Telegram |
| `created_at` | Дата парсинга (UTC ISO-8601) |
| `error` | Текст ошибки при `success: false` |

---

## 🛠️ Архитектура парсинга

| Маркетплейс | Стратегия | tier_used |
|-------------|-----------|-----------|
| **Ozon** | `httpx` → Chrome CDP через `composer-api.bx` | `cdp` |
| **Wildberries** | `httpx` → Chrome CDP через `card.wb.ru` | `cdp` |
| **AliExpress** | Chrome CDP → чтение DOM карточки | `cdp_dom` |
| **Yandex карточка** | SSR (server-rendered HTML с widget state) | `ssr` |
| **Yandex поиск** | SSR → fallback на карточку при degraded | `ssr` / `ssr_card` |

### Двухуровневая стратегия

1. **Tier 1** — обычный `httpx`-запрос с реалистичными заголовками. Работает с резидентного IP.
2. **Tier 2** — если Tier 1 получил 403 (Cloudflare/антибот), запрос идёт через Chrome CDP.

### Chrome CDP — что это

CDP (Chrome DevTools Protocol) — протокол, через который можно управлять Chrome:

1. Открыть вкладку с сайтом маркетплейса.
2. Дождаться, пока Cloudflare проверит браузер.
3. Выполнить `fetch()` к API маркетплейса **изнутри этой вкладки** — с её cookies.
4. Получить ответ как обычный пользователь.

**Почему это работает:** Cloudflare проверяет TLS-отпечаток и JS-окружение. Chrome — настоящий браузер с настоящим отпечатком, поэтому его пропускают.

**Что важно:**
- Chrome должен быть запущен с `--remote-debugging-address=0.0.0.0` и `--remote-allow-origins=*`.
- Вкладка маркетплейса должна быть **прогрета** (открыта вручную).
- Нельзя закрывать Chrome во время работы парсера.

---

## 📁 Структура проекта

```
channel_promo/
├── main.py                      # Точка входа FastAPI
├── worker.py                    # Worker: парсит URL + сохраняет в БД
├── db.py                        # SQLAlchemy модели + функции БД
├── requirements.txt
├── requirements-test.txt
├── Dockerfile
├── docker-compose.yml
├── start-chrome-cdp.bat         # Запуск Chrome с CDP (Windows)
├── start-chrome-cdp.sh          # Запуск Chrome с CDP (Linux/macOS)
├── README.md
├── core/
│   ├── __init__.py
│   ├── models.py                # ProductData
│   └── formatters.py            # PriceFormatter, TextGenerator
├── parsers/
│   ├── __init__.py
│   ├── base.py                  # BaseParser
│   ├── ozon.py                  # OzonParser (httpx + CDP)
│   ├── wildberries.py           # WildberriesParser (httpx + CDP)
│   ├── aliexpress.py            # AliexpressParser (CDP DOM)
│   └── yandex.py                # YandexParser (SSR)
├── services/
│   ├── __init__.py
│   └── marketplace.py           # MarketplaceFactory
├── routers/
│   ├── __init__.py
│   ├── parse.py                 # POST /parse, GET /cdp-check
│   ├── queue.py                 # POST /enqueue, GET /status
│   └── results.py               # GET /results (для дашборда)
├── broker/
│   ├── __init__.py
│   ├── connection.py            # Подключение к RabbitMQ
│   ├── producer.py              # Отправка URL в очередь
│   ├── consumer.py              # Чтение URL, парсинг, публикация результата
│   └── storage_consumer.py      # Сохранение результатов в БД
├── config/
│   ├── __init__.py
│   └── settings.py
├── frontend/                    # React-дашборд
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api.ts
│       └── styles.css
├── scripts/
│   ├── read_result.py           # Прочитать один результат из очереди
│   └── send_urls.py             # Массовая отправка URL
└── tests/
    ├── conftest.py              # Pytest fixtures
    ├── test_models.py           # Unit: модели
    ├── test_formatters.py       # Unit: форматтеры
    ├── test_parsers.py          # Unit: extract_sku
    ├── test_api.py              # Unit: FastAPI
    ├── test_integration.py      # Integration: реальные запросы
    ├── test_cdp.py              # Проверка Chrome CDP
    ├── run_all.bat              # Запуск всех тестов
    └── smoke_test.bat           # Smoke-тест через curl
```

---

## ⚙️ Конфигурация

Все настройки — через переменные окружения в `docker-compose.yml`.

### Основные

```yaml
environment:
  # Chrome CDP — IP хоста, где запущен Chrome
  CDP_HOST: 192.168.65.254
  CDP_PORT: 9222

  # RabbitMQ
  RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/

  # PostgreSQL
  DATABASE_URL: postgresql+asyncpg://parser:parser@postgres:5432/parser

  # Логирование
  LOG_LEVEL: INFO
```

### Таймауты

```yaml
  YANDEX_TIMEOUT: 40
  OZON_TIMEOUT: 30
  WB_TIMEOUT: 15
  ALI_TIMEOUT: 30
```

### Rate limiting

```yaml
  YANDEX_MIN_GAP: 1.5
  OZON_MIN_GAP: 2.5
  WB_MIN_GAP: 1.5
  ALI_MIN_GAP: 3.0
```

### Прокси (опционально)

```yaml
  # OZON_PROXY: http://user:pass@residential-proxy:port
  # YANDEX_PROXY: http://user:pass@residential-proxy:port
  # WB_PROXY: http://user:pass@residential-proxy:port
  # ALI_PROXY: http://user:pass@residential-proxy:port
```

---

## 🧪 Тестирование

Проект покрыт **33 тестами**: 28 unit + 5 integration.

### Установка зависимостей для тестов

```bash
pip install pytest pytest-asyncio
```

### Unit-тесты (без сети, без сервера)

```bash
python -m pytest tests/test_models.py tests/test_formatters.py tests/test_parsers.py tests/test_api.py -v
```

**Ожидаемо:** `28 passed in ~2s`.

**Что покрыто:**

| Файл | Что тестируется | Тестов |
|------|----------------|--------|
| `test_models.py` | `ProductData`, `MarketplaceType` | 4 |
| `test_formatters.py` | `PriceFormatter`, `TextGenerator` | 7 |
| `test_parsers.py` | `extract_sku` для всех 4 парсеров | 13 |
| `test_api.py` | Endpoints, валидация, схемы | 4 |

### Integration-тесты (нужен сервер + Chrome CDP)

**Шаг 1.** Запустить Chrome CDP:

```cmd
start-chrome-cdp.bat
```

**Шаг 2.** Прогреть маркетплейсы в Chrome CDP:
- `https://www.ozon.ru/`
- `https://www.wildberries.ru/`
- `https://aliexpress.ru/`

**Шаг 3.** Запустить все сервисы:

```cmd
docker compose up -d
```

**Шаг 4.** Запустить integration-тесты:

```bash
python -m pytest tests/test_integration.py -v -s
```

**Ожидаемо:** `5 passed in ~19s`.

**Что покрыто:**

| Тест | Что проверяет |
|------|---------------|
| `test_yandex_card` | Yandex карточка (SSR) |
| `test_yandex_search` | Yandex поиск (SSR + fallback) |
| `test_ozon_card` | Ozon через CDP |
| `test_wb_card` | Wildberries через CDP |
| `test_ali_card` | AliExpress через DOM |

### Все тесты сразу

```cmd
tests\run_all.bat
```

### Smoke-тест через curl

```cmd
tests\smoke_test.bat
```

---

## 🐳 Docker

### Команды

```cmd
# Запуск
docker compose up -d

# Сборка с нуля
docker compose build --no-cache

# Логи
docker compose logs -f parser
docker compose logs -f worker

# Перезапуск одного сервиса
docker compose restart parser

# Остановка
docker compose down

# Остановка + удаление volumes (БД, RabbitMQ)
docker compose down -v

# Внутри контейнера
docker compose exec parser bash
docker compose exec postgres psql -U parser -d parser

# Статистика
docker stats
```

### Проверка БД

```cmd
# Все товары
docker compose exec postgres psql -U parser -d parser -c "SELECT id, marketplace, price_rub, LEFT(title, 40) FROM products ORDER BY created_at DESC LIMIT 10;"

# Статистика по маркетплейсам
docker compose exec postgres psql -U parser -d parser -c "SELECT marketplace, COUNT(*) FROM products GROUP BY marketplace ORDER BY marketplace;"

# Очистить БД
docker compose exec postgres psql -U parser -d parser -c "TRUNCATE TABLE products RESTART IDENTITY;"
```

### Проверка очередей

```cmd
# Состояние очередей
docker compose exec rabbitmq rabbitmqctl list_queues name messages

# Через Management API
curl -u guest:guest "http://localhost:15672/api/queues?columns=name,messages"
```

### Чтение результатов из очереди

```cmd
python scripts\read_result.py
```

---

## ⚠️ Известные ограничения

### Яндекс поиск — degraded-выдача

Яндекс для датацентровых IP отдаёт **урезанную страницу** (`ok_ldjson_only`), где есть только `price_with_plus`, но нет `price_rub`.

**Что делает парсер:**
1. Ищет товар с `price_rub` в выдаче.
2. Если у всех `None` — запрашивает карточку первого товара.
3. Если карточка тоже degraded (`empty_product_shell`) — показывает `price_with_plus` с пометкой:

```
Новая цена — 276₽ (с подпиской Яндекс Плюс)
```

**Как получить обычную цену:**
- Использовать резидентный прокси: `YANDEX_PROXY=...`
- Или запрашивать **карточку** вместо поиска.

### AliExpress — x5sec

AliExpress использует x5sec (JS risk control Alibaba). Анонимный HTTP получает challenge-страницу.

**Что нужно:**
1. Открыть `aliexpress.ru` в Chrome CDP.
2. Пройти проверку вручную (если появится).
3. После прохождения — 30–60 минут работает без капчи.

### Wildberries — временные 403

WB иногда отдаёт `HTTP 403` как временный rate-limit. Обычно отпускает через 15–30 минут.

**Решение:**
- Подождать.
- Или использовать Chrome CDP (парсер автоматически переключится).

### Ozon — обязателен Chrome CDP

Ozon блокирует датацентровые IP через Cloudflare. Без Chrome CDP вернёт `HTTP 403`.

**Решение:**
- Запустить Chrome с `--remote-debugging-address=0.0.0.0`.
- Прогреть `ozon.ru` в этом Chrome.

### Товары без цены

Если товар **закончился** — `price_rub` будет `null`, а `in_stock: false`. Это **не баг**, а правильное поведение: `0` в качестве цены попал бы в «самый дешёвый» и испортил бы сортировку.

---

## 🐛 Отладка

### Проверить CDP изнутри Docker

```cmd
# Chrome доступен
docker compose exec parser python -c "import httpx; r = httpx.get('http://192.168.65.254:9222/json/version', timeout=3); print(r.status_code); print(r.text[:200])"

# Список вкладок
docker compose exec parser python -c "import httpx; r = httpx.get('http://192.168.65.254:9222/json/list', timeout=3); print([t['url'] for t in r.json() if t.get('type') == 'page'])"
```

### Проверить через эндпоинт

```bash
curl http://localhost:8000/api/v1/cdp-check
```

**Ожидаемый ответ:**

```json
{
  "available": true,
  "host": "192.168.65.254",
  "port": 9222,
  "browser": "Chrome/153.0.8010.48",
  "ws_url": "ws://192.168.65.254:9222/devtools/browser/..."
}
```

### Логи

```bash
# Все логи
docker compose logs -f

# Только worker
docker compose logs -f worker

# Только parser
docker compose logs -f parser
```

**В логах worker при парсинге видны строки:**

```
ozon.cdp | INFO | Ищу вкладку ozon.ru через /json/list...
ozon.cdp | INFO | Использую вкладку: id=73A74633...
ozon.cdp | INFO | WebSocket подключён
ozon.cdp | INFO | fetch вернул: status=200, body_len=151997
broker.storage_consumer | INFO | Сохранено в БД: id=1, ozon, Наполнитель глиняный...
```

---

## 🔧 Устранение неполадок

### `ModuleNotFoundError: No module named 'sqlalchemy'`

**Причина:** образ не пересобран после изменения `requirements.txt`.

**Решение:**

```cmd
docker compose down
docker compose build --no-cache parser worker
docker compose up -d
```

### `Handshake status 403` при подключении к WebSocket

**Причина:** Chrome запущен без `--remote-allow-origins=*`.

**Решение:**

```cmd
taskkill /F /IM chrome.exe
timeout /t 3
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
    --remote-debugging-port=9222 ^
    --remote-debugging-address=0.0.0.0 ^
    --remote-allow-origins=* ^
    --user-data-dir=C:\chrome-scraping
```

### `Network is unreachable` при CDP из Docker

**Причина:** `host.docker.internal` резолвится в IPv6, а Chrome слушает IPv4.

**Решение:** использовать IP хоста напрямую:

```yaml
CDP_HOST: 192.168.65.254
```

Узнать IP:

```cmd
docker compose exec parser cat /etc/hosts | findstr host.docker.internal
```

### `HTTP 403 — Ozon блокирует IP`

**Причина:** Chrome CDP не запущен или вкладка `ozon.ru` не открыта.

**Решение:**
1. Запустить Chrome с CDP.
2. Открыть `https://www.ozon.ru/` в этом Chrome.
3. Подождать 10 секунд.
4. Повторить запрос.

### `empty_product_shell` от Яндекса

**Причина:** Яндекс отдаёт пустой каркас страницы (degraded serving).

**Решение:** использовать карточку вместо поиска, или добавить резидентный прокси.

### Worker постоянно рестартует

**Причина:** RabbitMQ ещё не готов принять соединения.

**Решение:** в `broker/connection.py` уже есть retry. Если не помогает — увеличьте `max_retries`:

```python
async def connect(self, max_retries: int = 30, retry_delay: float = 3.0):
```

### `Broker не подключён` в `/queue/status`

**Причина:** parser не успел подключиться к RabbitMQ при старте.

**Решение:** перезапустить parser:

```cmd
docker compose restart parser
```

---

## 📌 Roadmap

- [x] **5 маркетплейсов** — Ozon, WB, AliExpress, Yandex × 2
- [x] **REST API** — FastAPI + Swagger
- [x] **RabbitMQ** — асинхронная очередь
- [x] **PostgreSQL** — хранение результатов
- [x] **Worker** — асинхронный парсинг
- [x] **React-дашборд** — просмотр результатов
- [x] **Docker Compose** — 5 контейнеров
- [x] **Тесты** — 33 (28 unit + 5 integration)
- [ ] **Telegram-бот** — постит `post_text` в канал
- [ ] **`/api/v1/compare`** — сравнение цен
- [ ] **Автообновление дашборда** — SSE/WebSocket
- [ ] **Экспорт CSV/Excel** — кнопка в дашборде
- [ ] **Расписание** — APScheduler для регулярного парсинга
- [ ] **Авторизация** — JWT для дашборда
- [ ] **GitHub Actions** — CI для тестов

---

## 🤝 Вклад

Проект использует идеи и подходы из открытых источников:

- **ru-marketplace-mcp** ([github.com/Vladimir-Human](https://github.com/Vladimir-Human/ru-marketplace-mcp)) — архитектура SSR-парсинга Yandex и composer-api Ozon
- **Chrome DevTools Protocol** — [chromedevtools.github.io/devtools-protocol](https://chromedevtools.github.io/devtools-protocol/)
- **FastAPI** — [fastapi.tiangolo.com](https://fastapi.tiangolo.com/)
- **aio-pika** — [aio-pika.readthedocs.io](https://aio-pika.readthedocs.io/)
- **SQLAlchemy** — [sqlalchemy.org](https://www.sqlalchemy.org/)

---

## 📄 Лицензия

MIT

---

## ⚖️ Дисклеймер

Проект предназначен для **личного использования и исследований**. Условия маркетплейсов, как правило, запрещают неофициальный парсинг.

Используйте на своё усмотрение, соблюдайте вежливый темп запросов (паузы между вызовами — часть конструкции, не убирайте их ради скорости). Данные не предназначены для перепродажи или массового сбора.