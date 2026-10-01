# Marketplace Parser API

Система для парсинга товаров с российских маркетплейсов (**Ozon**, **Wildberries**, **AliExpress**, **Яндекс.Маркет**) с сохранением результатов в PostgreSQL, автоматической публикацией в Telegram и веб-дашбордом.

---

## Содержание

- [Возможности](#возможности)
- [Архитектура](#архитектура)
- [Стек технологий](#стек-технологий)
- [Структура проекта](#структура-проекта)
- [Быстрый старт](#быстрый-старт)
- [Переменные окружения](#переменные-окружения)
- [Chrome CDP](#chrome-cdp)
- [Источники публикаций](#источники-публикаций)
- [API](#api)
- [Парсеры](#парсеры)
- [Очереди RabbitMQ](#очереди-rabbitmq)
- [Разработка и тесты](#разработка-и-тесты)

---

## Возможности

- 🔍 **Парсинг 4 маркетплейсов**: Ozon, Wildberries, AliExpress, Яндекс.Маркет
- 🐇 **Асинхронная обработка** через RabbitMQ (incoming / results / errors)
- 🗄 **PostgreSQL** + история цен (`PriceHistory`)
- 📢 **Telegram-публикация** — три независимых канала:
  - результаты парсинга URL из очереди (`parser.results.telegram`);
  - **ежедневная публикация 1 товара из каталога Такпродам** (`daily_publisher`);
  - **публикация каждые 10 минут из реферальной программы Яндекс.Маркета** (`yandex_referral_publisher`).
- 🔗 **Автополучение реферальной ссылки** Яндекс.Маркета через CDP — клик «Поделиться» → перехват `clipboard.writeText` → ссылка `/cc/xxxxx`.
- 🚫 **Защита от дублей**: `published_products` (Такпродам) и `published_referral_products` (Яндекс).
- 📊 **React-дашборд**: фильтры, поиск, статистика, SSE-обновления, превью постов.
- 🌐 **REST API** на FastAPI: парсинг, очередь, результаты, сравнение цен, экспорт CSV.
- 🧩 **Chrome CDP** для обхода антибот-защиты (x5sec, SmartCaptcha, реферальные попапы).

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
                                        └────────┬─────────┘
                                                 │
                    ┌────────────────────────────┼──────────────────────────┐
                    │                            │                          │
                    ▼                            ▼                          ▼
             parser.results.storage      parser.results.telegram     parser.errors
                    │                            │
                    ▼                            ▼
             ┌─────────────┐              ┌──────────────┐
             │ PostgreSQL  │              │  Telegram    │
             └─────────────┘              └──────────────┘
                    ▲                            ▲
                    │                            │
                    │                       daily_publisher
                    │                       yandex_referral_publisher
                    │                            │
             ┌──────┴───────┐         ┌──────────┴──────────┐
             │  frontend    │         │  Такпродам API      │
             │  (React)     │         │  Яндекс.Маркет CDP  │
             └──────────────┘         └─────────────────────┘
```

- **parser** — FastAPI, принимает URL, публикует в `parser.incoming`.
- **worker** — 4 консьюмера + 2 планировщика публикаций.
- **storage_consumer** — сохраняет в PostgreSQL.
- **telegram_consumer** — публикует результаты обычного парсинга.
- **daily_publisher** — раз в сутки 1 товар из каталога Такпродам.
- **yandex_referral_publisher** — каждые 10 минут 1 товар из рефералки Яндекс.Маркета.
- **frontend** — React-дашборд, проксирует `/api/*` → `parser:8000`.

---

## Стек технологий

| Слой | Технологии |
|------|-----------|
| Backend | Python 3.12, FastAPI, uvicorn, Pydantic |
| Брокер | RabbitMQ 3.13, `aio-pika` |
| БД | PostgreSQL 16, SQLAlchemy 2 (async), asyncpg |
| Парсинг | httpx, Playwright, `websocket-client` (Chrome CDP) |
| Telegram | aiogram 3, aiohttp (SOCKS5-прокси) |
| Внешние API | Такпродам (`api.takprodam.ru`), Яндекс.Маркет (SSR + CDP) |
| Frontend | React 18, TypeScript, Vite 5, nginx |
| Инфра | Docker, Docker Compose |

---

## Структура проекта

```
.
├── main.py                        # FastAPI приложение (parser)
├── worker.py                      # Воркер: консьюмеры + 2 планировщика
├── worker_health.py               # /health на :8080 для воркера
├── db.py                          # SQLAlchemy модели + репозиторий
├── Dockerfile                     # Универсальный образ (parser + worker)
├── docker-compose.yml
├── requirements.txt
├── requirements-test.txt
├── start-chrome-cdp.bat           # Запуск Chrome с CDP на Windows
│
├── broker/
│   ├── connection.py              # Broker (aio-pika) + очереди
│   ├── producer.py                # send_url / send_urls
│   ├── consumer.py                # incoming → parse → results
│   ├── storage_consumer.py        # results → PostgreSQL
│   ├── telegram_consumer.py       # results → Telegram
│   ├── daily_publisher.py         # Такпродам → Telegram (раз в сутки)
│   └── yandex_referral_publisher.py  # Яндекс.Маркет → Telegram (каждые 10 мин)
│
├── parsers/
│   ├── base.py                    # BaseParser + TTLCache
│   ├── ozon.py                    # Ozon (composer-api + CDP)
│   ├── wildberries.py             # WB (internal v4 + CDP/Playwright)
│   ├── aliexpress.py              # AliExpress (CDP + DOM)
│   └── yandex.py                  # Яндекс.Маркет (SSR + CDP + реф. ссылки)
│
├── services/
│   ├── marketplace.py             # MarketplaceFactory (URL → парсер)
│   └── takprodam.py               # Клиент API Такпродам
│
├── routers/
│   ├── parse.py                   # POST /parse, /health, /cdp-check
│   ├── queue.py                   # POST /queue/enqueue(-batch), /queue/status
│   ├── results.py                 # GET /results, /stats, /stream, /export/csv
│   └── compare.py                 # POST /compare
│
├── core/
│   ├── models.py                  # ProductData, MarketplaceType
│   └── formatters.py              # PriceFormatter, TextGenerator
│
├── config/settings.py             # CDP_HOST, CDP_PORT, LOG_LEVEL
│
└── frontend/                      # React + Vite + nginx
    ├── Dockerfile
    ├── nginx.conf
    └── src/
        ├── App.tsx
        ├── api.ts
        └── styles.css
```

---

## Быстрый старт

### 1. Chrome с CDP (обязательно для 4 парсеров)

**Windows** — `start-chrome-cdp.bat`.
**Вручную:**
```bash
chrome --remote-debugging-port=9222 \
       --remote-debugging-address=0.0.0.0 \
       --remote-allow-origins=* \
       --user-data-dir=/path/to/chrome-scraping
```

В этом Chrome:
1. Войти в `ozon.ru`, `wildberries.ru`, `market.yandex.ru`, `aliexpress.ru`.
2. Пройти капчу/x5sec вручную, если появится.
3. Оставить вкладку `market.yandex.ru` открытой — нужна для получения реферальных ссылок.

### 2. Запуск стека

```bash
docker compose up -d --build
```

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

Результат появится в дашборде через ~10 сек.

---

## Переменные окружения

### parser

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `CDP_HOST` | `127.0.0.1` | Хост Chrome CDP (в Docker — IP хоста) |
| `CDP_PORT` | `9222` | Порт Chrome CDP |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | Адрес RabbitMQ |
| `DATABASE_URL` | `postgresql+asyncpg://parser:parser@postgres:5432/parser` | DSN PostgreSQL |
| `LOG_LEVEL` | `INFO` | Уровень логирования |

### worker (дополнительно)

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен бота |
| `TELEGRAM_CHANNEL_ID` | ID канала для обычных результатов |
| `TELEGRAM_GROUP_ID` | ID группы для daily_publisher и yandex_referral (fallback = CHANNEL_ID) |
| `TELEGRAM_PROXY_URL` | SOCKS5/HTTP-прокси (`socks5://user:pass@host:1080`) |
| `TAKPRODAM_API_TOKEN` | Токен из ЛК Такпродам |
| `TAKPRODAM_SOURCE_ID` | id подтверждённой площадки (`status=approved`) |
| `TAKPRODAM_MARKETPLACE` | Опциональный фильтр: `Ozon`/`Wildberries`/`Avito`/`Aliexpress` |
| `TAKPRODAM_SUBID` | Метка трафика (по умолчанию `tg_daily`) |
| `PUBLISH_HOUR` | Час публикации Такпродам по локали (по умолчанию `20`) |
| `PUBLISH_TZ_OFFSET` | Смещение таймзоны, МСК = `3` |
| `YANDEX_REFERRAL_INTERVAL` | Интервал публикации рефералки Яндекса в секундах (по умолчанию `600`) |

Пример `.env`:
```env
TELEGRAM_BOT_TOKEN=123456:AAA...
TELEGRAM_CHANNEL_ID=@my_channel
TELEGRAM_GROUP_ID=-1001234567890
TELEGRAM_PROXY_URL=socks5://user:pass@host:1080

TAKPRODAM_API_TOKEN=xxxx-xxxx-xxxx
TAKPRODAM_SOURCE_ID=12345
TAKPRODAM_MARKETPLACE=Ozon
TAKPRODAM_SUBID=tg_daily
PUBLISH_HOUR=20
PUBLISH_TZ_OFFSET=3

YANDEX_REFERRAL_INTERVAL=600
```

---

## Chrome CDP

Три из четырёх парсеров используют Chrome DevTools Protocol:

| Парсер | Основной источник | Роль CDP |
|--------|-------------------|----------|
| **Ozon** | composer-api.bx (httpx) | Tier 2: fetch из залогиненной вкладки + чтение DOM |
| **Wildberries** | `__internal/card/cards/v4/detail` | Tier 1: токен/deviceid/xinfo из localStorage + fetch |
| **AliExpress** | DOM открытой вкладки | Единственный источник данных |
| **Яндекс.Маркет** | SSR `data-apiary="patch"` | Фолбэк + получение реферальной ссылки |

### Получение реферальной ссылки Яндекса

Ключевая логика (`YandexParser._cdp_get_referral_link`):

1. Навигация на карточку товара.
2. Установка перехватчиков `navigator.clipboard.writeText`, `fetch`, `XMLHttpRequest`.
3. Поиск и клик по кнопке «Поделиться» (`data-zone-name="customSharingPopup"`, `aria-label`, текст).
4. Клик по «Копировать ссылку» (`data-zone-name="copyLinkButton"`).
5. Возврат ссылки `/cc/xxxxx` из перехваченного буфера + HTML карточки за тот же рейс.

**Требования:** залогиненный Яндекс ID в Chrome + открытая вкладка `market.yandex.ru`.

**В Docker** для доступа к Chrome на хосте задано `extra_hosts: host.docker.internal:host-gateway`. На Linux замените `CDP_HOST` на IP хоста в вашей сети.

---

## Источники публикаций

В проекте работают три независимых канала публикации в Telegram:

### 1. Обычный парсинг (`telegram_consumer.py`)

- **Триггер:** URL в очереди `parser.incoming`.
- **Логика:** парсинг → результат в fanout → storage + telegram.
- **Фильтры:** `success`, `price_rub`/`price_with_plus`, `in_stock`.
- **Целевой чат:** `TELEGRAM_CHANNEL_ID`.

### 2. Такпродам (`daily_publisher.py`)

- **Триггер:** раз в сутки в `PUBLISH_HOUR:00` (МСК).
- **Источник:** `GET /v2/publisher/product/?source_id=...` из API Такпродам.
- **Логика:** фильтр по `published_products` → случайный товар → публикация → отметка в БД.
- **Текст:** `TextGenerator.generate()` + `legal_text` из ответа.
- **Целевой чат:** `TELEGRAM_GROUP_ID`.

### 3. Рефералка Яндекс.Маркета (`yandex_referral_publisher.py`)

- **Триггер:** раз в `YANDEX_REFERRAL_INTERVAL` секунд (по умолчанию 600 = 10 минут).
- **Источник:** `https://market.yandex.ru/page/referral_products?tabId=TOP_REWARD`.
- **Логика:**
  1. CDP → загрузка страницы → `parse_search()` (SSR-состояние).
  2. Фильтр по `published_referral_products` + наличию картинки.
  3. До 5 попыток на товар — берётся тот, у которого получилось достать реферальную ссылку.
  4. Для найденного товара — `_cdp_get_referral_link()` → клик → ссылка `/cc/xxx` + HTML карточки.
  5. `_card_from_html()` парсит HTML → цены (Pay / обычная / зачёркнутая).
  6. `save_product()` → товар появляется в дашборде.
  7. `_send_to_telegram()` → пост с реферальной ссылкой.
  8. `mark_referral_published()` → защита от повторной публикации.
- **Целевой чат:** `TELEGRAM_GROUP_ID`.

**Особенности текста поста:** цена Яндекс Пэй показывается как основная, обычная (или зачёркнутая) — как «Было».

---

## API

Все эндпоинты — с префиксом `/api/v1`.

### Парсинг (синхронный)

**`POST /parse`**
```json
{"url": "https://www.ozon.ru/product/1234567890/"}
```

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
| GET | `/health` | Healthcheck |
| GET | `/cdp-check` | Диагностика Chrome CDP |
| POST | `/compare` | Сравнение цен по 4 маркетплейсам |

---

## Парсеры

### Ozon (`parsers/ozon.py`)

- **Tier 1:** `composer-api.bx` через httpx.
- **Tier 2:** CDP fetch из залогиненной вкладки.
- **Fallback:** чтение цен из DOM (`.pdp_b2h`, `.pdp_b2i`).

### Wildberries (`parsers/wildberries.py`)

- **Tier 1 (CDP):** `Bearer`-токен + `deviceid` + `xinfo` из localStorage → `fetch` на `__internal/card/cards/v4/detail`.
- **Tier 2 (Playwright):** `page.request.get` со теми же заголовками.

### AliExpress (`parsers/aliexpress.py`)

- Чтение DOM уже открытой вкладки `/item/<id>.html`.
- Селекторы `HazeProductPrice_SnowPrice__mainM/S`, апгрейд картинок до 960x960.

### Яндекс.Маркет (`parsers/yandex.py`)

- **SSR:** `<noframes data-apiary="patch">` → `parse_search()` / `parse_card()`.
- **CDP-фолбэк:** hard reload → ожидание `allPrices` → `outerHTML` → `parse_card()`.
- **Реферальные ссылки:** `_cdp_get_referral_link()` — клик + перехват `clipboard`.

---

## Очереди RabbitMQ

| Очередь | Тип | Назначение |
|---------|-----|------------|
| `parser.incoming` | direct | Входящие URL |
| `parser.errors` | direct | Ошибки парсинга |
| `parser.results.storage` | fanout | Результаты → PostgreSQL |
| `parser.results.telegram` | fanout | Результаты → Telegram |

Exchange `parser.results.fanout` — публикует в обе очереди одновременно.

---

## Разработка и тесты

### Локальный запуск

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

docker compose up -d postgres rabbitmq

uvicorn main:app --reload
python worker.py   # в отдельном окне
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
npm run dev      # :5173
npm run build
```

### Разовые тесты публикаторов

```bash
# Такпродам — опубликовать 1 товар сейчас
docker compose exec worker python -c "
import asyncio
from broker.daily_publisher import _publish_one
asyncio.run(_publish_one())
"

# Яндекс.рефералка — опубликовать 1 товар сейчас
docker compose exec worker python -c "
import asyncio
from broker.yandex_referral_publisher import _publish_one
asyncio.run(_publish_one())
"

# Получить реферальную ссылку без публикации
docker compose exec worker python -c "
from parsers.yandex import YandexParser
p = YandexParser()
print(p._cdp_get_referral_link('https://market.yandex.ru/card/.../1234567890'))
"
```

---

## Особенности эксплуатации

- **CDP-вкладки должны быть залогинены.** Без этого Яндекс не покажет `copyLinkButton`, WB не отдаст данные, AliExpress покажет x5sec.
- **Реферальная ссылка не у всех товаров.** Некоторые товары на `referral_products` не участвуют в программе — `_publish_one` пробует до 5 штук и пропускает «пустые».
- **Сессии живут ограниченно.** Токены WB кэшируются 5 минут (`_creds_ttl`).
- **TTL-кэш результатов:** `fetch_cached` кэширует успешный парсинг на 5 минут.
- **Rate-limit** на `/queue/enqueue` — 30/мин с IP (slowapi).
- **Auto-reconnect:** воркер перезапускается через 5 секунд при падении.
- **История цен:** каждое сохранение пишет в `price_history`, даже для существующих товаров (upsert по `url + marketplace`).
- **Дедупликация публикаций:**
  - Такпродам: `UNIQUE(published_products.takprodam_id)`.
  - Яндекс: `UNIQUE(published_referral_products.product_id)`.

---

## Лицензия

Проект распространяется «как есть» для личного использования. При парсинге маркетплейсов соблюдайте их Terms of Service.
