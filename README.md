# Marketplace Parser API

Система для парсинга товаров с российских маркетплейсов (**Ozon**, **Wildberries**, **AliExpress**, **Яндекс.Маркет**) с сохранением результатов в PostgreSQL, постингом в Telegram и веб-дашбордом.

---

## Содержание

- [Возможности](#возможности)
- [Архитектура](#архитектура)
- [Стек технологий](#стек-технологий)
- [Структура проекта](#структура-проекта)
- [Быстрый старт](#быстрый-старт)
- [Переменные окружения](#переменные-окружения)
- [Chrome CDP](#chrome-cdp)
- [API](#api)
- [Парсеры](#парсеры)
- [Очереди RabbitMQ](#очереди-rabbitmq)
- [Разработка и тесты](#разработка-и-тесты)

---

## Возможности

- 🔍 **Парсинг 4 маркетплейсов**: Ozon, Wildberries, AliExpress, Яндекс.Маркет
- 🐇 **Асинхронная обработка** через RabbitMQ (очереди incoming / results / errors)
- 🗄 **Хранение в PostgreSQL** с историей цен (`PriceHistory`)
- 📢 **Автопостинг в Telegram-канал** с картинкой и текстом (aiogram + прокси)
- 📊 **Веб-дашборд** (React + Vite): фильтры, поиск, статистика, SSE-обновления
- 🌐 **REST API** на FastAPI: парсинг, очередь, результаты, сравнение цен, экспорт CSV
- 🧩 **Chrome CDP-фолбэк** для обхода антибот-защиты (x5sec, SmartCaptcha)
- 🐳 **Docker Compose**: postgres, rabbitmq, parser, worker, frontend

---

## Архитектура

```
                 ┌────────────┐         ┌─────────────────┐
   HTTP ──────▶ │  parser    │ ──────▶ │  RabbitMQ       │
  (FastAPI)      │  (main.py) │         │  parser.incoming│
                 └────────────┘         └────────┬────────┘
                                                 │
                                                 ▼
                                        ┌──────────────────┐
                                        │  worker.py       │
                                        │  (consumers)     │
                                        └────────┬─────────┘
                                                 │
                       parser.results.fanout     │
                    ┌────────────────────────────┼───────────────────┐
                    ▼                            ▼                   ▼
            parser.results.storage       parser.results.telegram   parser.errors
                    │                            │
                    ▼                            ▼
             ┌─────────────┐              ┌──────────────┐
             │ PostgreSQL  │              │  Telegram    │
             └─────────────┘              └──────────────┘
                    ▲
                    │  SSE / REST
             ┌──────┴───────┐
             │  frontend    │
             │  (React)     │
             └──────────────┘
```

- **parser** — FastAPI-сервис, принимает URL, публикует в очередь `parser.incoming`.
- **worker** — консьюмеры: парсит URL, публикует результат в **fanout** exchange `parser.results.fanout`.
- **storage_consumer** — сохраняет результат в PostgreSQL + пишет историю цен.
- **telegram_consumer** — постит товар в канал (фото + текст).
- **frontend** — React-дашборд, проксирует `/api/*` на `parser:8000`.

---

## Стек технологий

| Слой | Технологии |
|------|-----------|
| Backend | Python 3.12, FastAPI, uvicorn, Pydantic |
| Брокер | RabbitMQ 3.13, `aio-pika` |
| БД | PostgreSQL 16, SQLAlchemy 2 (async), asyncpg |
| Парсинг | httpx, Playwright, `websocket-client` (Chrome CDP) |
| Telegram | aiogram 3, aiohttp (socks proxy) |
| Frontend | React 18, TypeScript, Vite 5, nginx |
| Инфра | Docker, Docker Compose |

---

## Структура проекта

```
.
├── main.py                       # FastAPI приложение (parser)
├── worker.py                     # Воркер: консьюмеры RabbitMQ
├── worker_health.py              # /health на :8080 для воркера
├── db.py                         # SQLAlchemy модели + репозиторий
├── Dockerfile                    # Универсальный образ (parser + worker)
├── docker-compose.yml
├── requirements.txt
├── requirements-test.txt
├── start-chrome-cdp.bat          # Запуск Chrome с CDP на Windows
│
├── broker/
│   ├── connection.py             # Broker (aio-pika) + объявление очередей
│   ├── producer.py               # send_url / send_urls
│   ├── consumer.py               # читает incoming, парсит, публикует results
│   ├── storage_consumer.py       # сохраняет results в PostgreSQL
│   └── telegram_consumer.py      # постит results в Telegram
│
├── parsers/
│   ├── base.py                   # BaseParser (fetch_cached, TTLCache)
│   ├── ozon.py                   # Ozon (composer-api + CDP)
│   ├── wildberries.py            # WB (internal API v4 + CDP/Playwright)
│   ├── aliexpress.py             # AliExpress (CDP + DOM)
│   └── yandex.py                 # Яндекс.Маркет (SSR state + CDP)
│
├── routers/
│   ├── parse.py                  # POST /parse, GET /health, /cdp-check
│   ├── queue.py                  # POST /queue/enqueue(-batch), /queue/status
│   ├── results.py                # GET /results, /stats, /stream, /export/csv
│   └── compare.py                # POST /compare
│
├── services/
│   └── marketplace.py            # MarketplaceFactory (URL → парсер)
│
├── core/
│   ├── models.py                 # ProductData, MarketplaceType
│   └── formatters.py             # PriceFormatter, TextGenerator
│
├── config/
│   └── settings.py               # CDP_HOST, CDP_PORT, LOG_LEVEL, ...
│
├── scripts/
│   └── read_result.py            # Утилита для чтения из results.storage
│
└── frontend/                     # React + Vite + nginx
    ├── Dockerfile
    ├── nginx.conf
    └── src/
        ├── App.tsx
        ├── api.ts
        └── styles.css
```

---

## Быстрый старт

### 1. Запуск Chrome с CDP (для обхода антибота)

**Windows** — запустить `start-chrome-cdp.bat`.

**Вручную:**
```bash
chrome --remote-debugging-port=9222 \
       --remote-debugging-address=0.0.0.0 \
       --remote-allow-origins=* \
       --user-data-dir=/path/to/chrome-scraping
```

В этом Chrome:
1. Открыть и залогиниться на `ozon.ru`, `wildberries.ru`, `market.yandex.ru`, `aliexpress.ru`.
2. Пройти капчу/x5sec вручную, если появится.
3. Оставить вкладки открытыми — парсеры будут их переиспользовать.

### 2. Запуск стека

```bash
docker compose up -d --build
```

Сервисы:

| Сервис | URL |
|--------|-----|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000/api/v1/health |
| RabbitMQ UI | http://localhost:15672 (guest/guest) |
| PostgreSQL | `localhost:5432` (parser/parser) |

### 3. Проверка

```bash
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/cdp-check
```

### 4. Отправить URL на парсинг

```bash
curl -X POST http://localhost:8000/api/v1/queue/enqueue \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.ozon.ru/product/1234567890/"}'
```

Через ~10 секунд результат появится в дашборде или в БД.

---

## Переменные окружения

### parser

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `CDP_HOST` | `127.0.0.1` | Хост Chrome CDP. В Docker — `host.docker.internal` или IP хоста |
| `CDP_PORT` | `9222` | Порт Chrome CDP |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | Адрес RabbitMQ |
| `DATABASE_URL` | `postgresql+asyncpg://parser:parser@postgres:5432/parser` | DSN PostgreSQL |
| `LOG_LEVEL` | `INFO` | Уровень логирования |

### worker (дополнительно)

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен бота для постинга |
| `TELEGRAM_CHANNEL_ID` | ID канала (например `@my_channel` или `-1001234567890`) |
| `TELEGRAM_PROXY_URL` | Прокси (например `socks5://user:pass@host:1080`) |

---

## Chrome CDP

Три из четырёх маркетплейсов используют Chrome DevTools Protocol как фолбэк или как основной источник:

| Парсер | Основной источник | CDP-роль |
|--------|-------------------|----------|
| **Ozon** | composer-api.bx (httpx) | Tier 2: fetch из залогиненной вкладки + чтение DOM цен |
| **Wildberries** | `__internal/card/cards/v4/detail` | Tier 1: чтение токена/deviceid/xinfo из localStorage + fetch. Tier 2: Playwright |
| **AliExpress** | — | Основной: чтение DOM открытой вкладки `/item/<id>.html` |
| **Яндекс.Маркет** | SSR `data-apiary="patch"` | Фолбэк: если SSR пустой — hard reload + чтение outerHTML/цен из DOM |

**Ключевое условие:** во вкладке Chrome должен быть **залогиненный пользователь** с открытой страницей маркетплейса. Парсеры переиспользуют cookies, localStorage и fingerprint.

В `docker-compose.yml` для контейнеров задано:
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```
Это позволяет обращаться к Chrome на хосте. **На Linux** замените `CDP_HOST: 192.168.65.254` на IP хоста в локальной сети (например `172.17.0.1`).

---

## API

Все эндпоинты — с префиксом `/api/v1`.

### Парсинг (синхронный)

**`POST /parse`**
```json
{"url": "https://www.ozon.ru/product/1234567890/"}
```
Возвращает `ProductResponse` с полями товара и сгенерированным `post_text`.

### Очередь (асинхронный)

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| POST | `/queue/enqueue` | Добавить URL (rate-limit 30/min) |
| POST | `/queue/enqueue-batch` | Добавить список URL |
| GET | `/queue/status` | Статус очередей RabbitMQ |

### Результаты

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/results` | Список с фильтрами `marketplace`, `search`, `min_price`, `max_price`, `page`, `per_page` |
| GET | `/results/stats` | Всего / по маркетплейсам / за 24ч |
| GET | `/results/{id}` | Один товар |
| GET | `/results/{id}/history` | История цен |
| DELETE | `/results/{id}` | Удалить |
| GET | `/results/stream` | SSE-поток новых товаров |
| GET | `/results/export/csv` | Экспорт CSV |

### Прочее

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/health` | Healthcheck парсера |
| GET | `/cdp-check` | Диагностика доступности Chrome CDP |
| POST | `/compare` | Сравнение цен по 4 маркетплейсам |

---

## Парсеры

### Ozon (`parsers/ozon.py`)

1. **Tier 1:** `https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/<sku>/` через httpx с реалистичными заголовками.
2. **Tier 2:** если HTTP 403 — fetch изнутри залогиненной вкладки `ozon.ru` через CDP.
3. **Fallback:** цены читаются прямо из DOM (`.pdp_b2h`, `.pdp_b2i`).

### Wildberries (`parsers/wildberries.py`)

С ноября 2026 WB закрыл анонимный доступ к `card.wb.ru`. Работает только авторизованный `__internal/card/cards/v4/detail`.

1. **Tier 1 (CDP):** из localStorage вкладки берутся `wbid-oauth-sdk-access-token`, `wbx__sessionID`, `xinfo`; выполняется `fetch` с `Bearer`-токеном.
2. **Tier 2 (Playwright):** тот же запрос через `page.request.get` в свежем Chromium.

Картинка товара ищется двумя способами: regex по HTML карточки → навигация на карточку + DOM-селектор.

### AliExpress (`parsers/aliexpress.py`)

Читает DOM **уже открытой** вкладки `/item/<id>.html`. Селекторы классов:
- Цена: `HazeProductPrice_SnowPrice__mainM/S`
- Старая цена: `HazeProductPrice_SnowPrice__secondPrice`
- Картинка: `img[src*="aliexpress-media"]` (апгрейд до 960x960)

### Яндекс.Маркет (`parsers/yandex.py`)

1. **SSR:** httpx получает HTML, ищет `<noframes data-apiary="patch">` (JSON-стейт).
2. **CDP-фолбэк:** при `empty_product_shell` / `ok_ldjson_only` — жёсткий reload через CDP, ожидание появления `allPrices`/`baobab`, чтение `outerHTML`.
3. **DOM-цены:** если SSR-стейта нет, JS извлекает все `₽`-цены из DOM и встраивает в `<script id="__cdp_prices__">`.

`share_url` берётся из коллекции `share` и подставляется в пост для Яндекса.

---

## Очереди RabbitMQ

| Очередь | Тип | Назначение |
|---------|-----|------------|
| `parser.incoming` | direct | Входящие URL |
| `parser.errors` | direct | Ошибки парсинга |
| `parser.results.storage` | fanout | Результаты → PostgreSQL |
| `parser.results.telegram` | fanout | Результаты → Telegram |

Exchange `parser.results.fanout` (тип `fanout`) — публикует результат в обе очереди одновременно.

---

## Разработка и тесты

### Локальный запуск (без Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# поднять postgres и rabbitmq (например, в докере)
docker compose up -d postgres rabbitmq

# запустить API
uvicorn main:app --reload

# в отдельном окне — воркер
python worker.py
```

### Тесты

```bash
pip install -r requirements-test.txt
pytest
```

### Обновление фронтенда

```bash
cd frontend
npm install
npm run dev      # dev на :5173
npm run build    # production сборка
```

---

## Особенности эксплуатации

- **CDP-вкладки должны быть залогинены.** Если WB/AliExpress/Ozon показывают капчу — откройте вкладку вручную и пройдите проверку.
- **Сессии живут ограниченно.** Токены WB хранятся в кэше парсера 5 минут (`_creds_ttl`).
- **TTL-кэш результатов:** `fetch_cached` кэширует успешные результаты на 5 минут (`TTLCache(maxsize=500, ttl=300)`).
- **Rate-limit** на `/queue/enqueue` — 30 запросов/минуту с IP (slowapi).
- **Auto-reconnect:** воркер при падении перезапускается через 5 секунд (см. `worker.py`).
- **История цен:** каждое сохранение товара добавляет запись в `price_history`, даже если товар уже есть (upsert по `url + marketplace`).

---

## Лицензия

Проект распространяется «как есть» для личного использования. При парсинге маркетплейсов соблюдайте их Terms of Service.