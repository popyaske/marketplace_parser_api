# Marketplace Parser API

Система для парсинга товаров с российских маркетплейсов (**Ozon**, **Wildberries**, **AliExpress**, **Яндекс.Маркет**) с сохранением в PostgreSQL, публикацией в **Telegram** и **VK**, веб-дашбордом и автоматической сборкой вертикальных клипов для VK.

---

## Содержание

1. [Что умеет](#что-умеет)
2. [Архитектура](#архитектура)
3. [Стек](#стек)
4. [Структура проекта](#структура-проекта)
5. [Быстрый старт](#быстрый-старт)
6. [Настройка Chrome CDP](#настройка-chrome-cdp)
7. [Переменные окружения](#переменные-окружения)
8. [RabbitMQ: очереди и потоки](#rabbitmq-очереди-и-потоки)
9. [Источники публикаций](#источники-публикаций)
10. [Парсеры](#парсеры)
11. [VK Digest](#vk-digest)
12. [REST API](#rest-api)
13. [База данных и Alembic](#база-данных-и-alembic)
14. [Логирование](#логирование)
15. [Скрипты](#скрипты)
16. [Разработка](#разработка)
17. [Эксплуатация и troubleshooting](#эксплуатация-и-troubleshooting)
18. [Лицензия](#лицензия)

---

## Что умеет

- 🔍 **4 маркетплейса** — Ozon, Wildberries, AliExpress, Яндекс.Маркет, единый интерфейс `BaseParser` с per-class TTL-кэшем (5 мин).
- 🐇 **Асинхронная очередь** на RabbitMQ: `incoming` → fanout `results.storage` / `results.telegram`, отдельно `referral.manual` и `errors`.
- 🗄 **PostgreSQL 16 + Alembic** — история цен, дедупликация, миграции при старте.
- 📢 **Три потока публикаций в Telegram**:
  - обычный парсинг URL (`telegram_consumer`);
  - ежедневная публикация из каталога Такпродам (`daily_publisher`, по умолчанию выключен);
  - рефералка Яндекс.Маркета (`yandex_referral_publisher` — планировщик + ручной консьюмер).
- 🔁 **Автороутинг Яндекс-ссылок** — ссылки `market.yandex.ru/*` (кроме `/search`) из `incoming` автоматически перекладываются в `referral.manual`.
- 📨 **Учёт опубликованных сообщений** — таблица `published_messages`, вкладка «Сообщения» в дашборде с кнопкой «Копировать ссылку».
- 🎬 **VK Digest** — раз в сутки собирает 5 фото в вертикальный клип 9:16 (Ken Burns + xfade + музыка) с живыми хештегами из `vk.ru/trending` и публикует в VK Клипы.
- 🎨 **Стилизация карточек** через Pillow (`overlay_maker`): «НИЖЕ РЫНКА», зелёная цена + Пэй, бренд, юр. текст. Ручная настройка через `scripts/card_tuner.py`.
- 🔗 **Автополучение реферальных ссылок Яндекса** через CDP — клик «Поделиться» → перехват `clipboard.writeText` → `/cc/xxxxx`.
- 🎵 **Трендовые хештеги VK** — скрапинг `vk.ru/trending` раз в 6 часов.
- 🚫 **Защита от дублей** — `UNIQUE(sku, marketplace)`, `published_products`, `published_referral_products`.
- 📊 **React-дашборд** — вкладки «Товары» / «Сообщения», фильтры, SSE-обновления, превью постов, кнопка публикации рефералки.
- 🌐 **REST API** на FastAPI — очередь, результаты, история, статистика, CSV, сравнение цен, диагностика CDP.
- 🧩 **Chrome CDP** для обхода антибот-защиты (x5sec, SmartCaptcha, реферальные попапы).
- 🎟 **Промокоды** в постах (`promo_code` / `promo_percent`).
- 📝 **JSON-логи** — готовность к Loki/ELK.

---

## Архитектура

```
HTTP  ──▶  parser (FastAPI)  ──▶  RabbitMQ  ──▶  worker (consumer)
                                                   │
              ┌────────────────────────┬───────────┼────────────┬──────────────┐
              ▼                        ▼           ▼            ▼              ▼
     results.storage         results.telegram   errors   referral.manual  VK / media
              │                        │                        │              │
              ▼                        ▼                        ▼              ▼
       ┌─────────────┐         ┌──────────────┐         ┌──────────────┐ ┌──────────────┐
       │ PostgreSQL  │         │  Telegram    │         │  Yandex      │ │  media/      │
       │ (Alembic)   │         │  (group)     │         │  referral    │ │  photos/     │
       └──────┬──────┘         └──────────────┘         └──────────────┘ │  videos/     │
              │                                                          │  music/      │
              ▼                                                          └──────┬───────┘
       ┌──────────────┐                                                       ▼
       │  frontend    │                                              ┌──────────────────┐
       │  (React)     │                                              │  VK API          │
       └──────────────┘                                              │  video.save      │
                                                                     │  wall.post       │
                                                                     │  stories         │
                                                                     └──────────────────┘
```

**Сервисы** (один общий Docker-образ, разные `command`):

| Сервис | Назначение |
|--------|------------|
| `parser` | FastAPI + `alembic upgrade head` перед стартом |
| `worker` | 5 консьюмеров + 3 планировщика + health-сервер на `:8080` |
| `postgres` | PostgreSQL 16 |
| `rabbitmq` | RabbitMQ 3.13 (management) |
| `frontend` | nginx, проксирует `/api/*` на parser |

---

## Стек

| Слой | Технологии |
|------|-----------|
| Backend | Python 3.12, FastAPI, uvicorn, Pydantic |
| Брокер | RabbitMQ 3.13, `aio-pika` 10 |
| БД | PostgreSQL 16, SQLAlchemy 2 (async), asyncpg, Alembic |
| Парсинг | httpx, `websocket-client` (CDP), `curl_cffi` |
| Telegram | aiogram 3, `aiohttp_socks` (SOCKS5/HTTP-прокси) |
| VK | VK API 5.199 (`video.save`, `wall.post`, `stories`) |
| Медиа | Pillow (карточки), ffmpeg (видео 9:16) |
| Frontend | React 18, TypeScript, Vite 5, nginx |
| Инфра | Docker, Docker Compose |
| Логи | JSON-форматтер (`core/logging_setup.py`) |

---

## Структура проекта

```
.
├── main.py                        # FastAPI (parser)
├── worker.py                      # Воркер: консьюмеры + планировщики
├── worker_health.py               # /health :8080
├── db.py                          # SQLAlchemy модели + репозиторий
├── Dockerfile                     # общий образ для parser/worker
├── docker-compose.yml
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
├── requirements.txt
├── requirements-test.txt
├── start-chrome-cdp.bat
│
├── broker/
│   ├── connection.py              # aio-pika + очереди + fanout
│   ├── producer.py                # send_url / send_urls
│   ├── consumer.py                # incoming → parse → fanout / referral
│   ├── storage_consumer.py        # results.storage → БД
│   ├── telegram_consumer.py       # results.telegram → TG + media + message
│   ├── daily_publisher.py         # Такпродам → TG (выключен)
│   ├── yandex_referral_publisher.py
│   └── vk_digest_publisher.py
│
├── parsers/
│   ├── base.py                    # BaseParser + per-class кэш + cdp_wait_for_load
│   ├── ozon.py                    # httpx composer-api + CDP + DOM
│   ├── wildberries.py             # __internal v4 + localStorage creds
│   ├── aliexpress.py              # CDP + DOM
│   └── yandex.py                  # SSR + CDP + реф. ссылки
│
├── services/
│   ├── marketplace.py             # MarketplaceFactory
│   ├── takprodam.py
│   ├── media_storage.py           # save/delete photos + free-space check
│   ├── overlay_maker.py
│   ├── video_maker.py             # ffmpeg: 9:16 + Ken Burns + xfade + музыка
│   ├── vk_client.py
│   └── vk_trending.py
│
├── routers/
│   ├── parse.py                   # /parse, /health, /cdp-check
│   ├── queue.py                   # /queue/*
│   ├── results.py                 # /results/*
│   ├── messages.py                # /messages
│   ├── referral.py                # /referral/publish
│   └── compare.py                 # /compare
│
├── core/
│   ├── models.py                  # ProductData, MarketplaceType
│   ├── formatters.py              # PriceFormatter, TextGenerator
│   ├── telegram_links.py          # build_message_link
│   └── logging_setup.py           # setup_logging (JSON)
│
├── config/settings.py
│
├── scripts/
│   ├── card_tuner.py              # GUI-тюнер карточки
│   ├── check_admitad.py
│   ├── check_aliexpress.py
│   ├── read_result.py
│   └── test_card.py
│
└── frontend/
    ├── Dockerfile
    ├── nginx.conf
    └── src/
        ├── App.tsx
        ├── MessagesPage.tsx
        ├── api.ts
        └── styles.css
```

---

## Быстрый старт

### 1. Chrome с CDP

**Windows** — запустить `start-chrome-cdp.bat`.

**Вручную:**

```bash
chrome --remote-debugging-port=9222 \
       --remote-debugging-address=0.0.0.0 \
       --remote-allow-origins=* \
       --user-data-dir=/path/to/chrome-scraping
```

Открыть и залогиниться в:

1. `ozon.ru`
2. `market.yandex.ru`
3. `wildberries.ru`
4. `aliexpress.ru`
5. `vk.ru/trending`

Пройти капчи вручную. **Оставить вкладки открытыми** — воркер их использует.

### 2. Узнать IP хоста из контейнера

```bash
docker compose exec parser getent hosts host.docker.internal
# → 192.168.65.254    host.docker.internal
```

Взять **IPv4** (не `fd...`) и вписать в `docker-compose.yml`:

```yaml
CDP_HOST: 192.168.65.254
```

### 3. `.env`

Скопировать из `.env.example`, заполнить минимум:

```env
POSTGRES_USER=parser
POSTGRES_PASSWORD=parser
POSTGRES_DB=parser

RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

TELEGRAM_BOT_TOKEN=123456:AAA...
TELEGRAM_GROUP_ID=-1001234567890

VK_ACCESS_TOKEN=vk1.a.xxxxx
VK_USER_ID=1234567
```

### 4. Музыка для VK

Положить 3–10 `mp3`/`m4a` в `./music/`.

### 5. Запуск

```bash
docker compose up -d --build
```

| Сервис | URL |
|--------|-----|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000/api/v1/health |
| RabbitMQ UI | http://localhost:15672 |
| PostgreSQL | `localhost:5432` (только с хоста, если раскомментирован `ports`) |

### 6. Проверка

```bash
docker compose ps
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/cdp-check
curl http://localhost:8000/api/v1/results/stats
```

### 7. Первый парсинг

```bash
curl -X POST http://localhost:8000/api/v1/queue/enqueue \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.ozon.ru/product/1234567890/"}'
```

Результат появится в дашборде через ~10 секунд, пост уйдёт в Telegram.

---

## Настройка Chrome CDP

Все 4 парсера используют **Chrome DevTools Protocol**.

| Парсер | Основной источник | Роль CDP |
|--------|-------------------|----------|
| **Ozon** | composer-api.bx | Tier 2: fetch из вкладки + DOM цен |
| **Wildberries** | `__internal/card/cards/v4/detail` | Tier 1: Bearer + deviceid + xinfo из localStorage |
| **AliExpress** | DOM вкладки | Единственный источник |
| **Яндекс.Маркет** | SSR `<noframes data-apiary="patch">` | Фолбэк + реф. ссылка |
| **VK Trending** | iframe `vklanding.com` | Извлечение хештегов |

### ⚠️ Важно про `host.docker.internal` на Windows

Chrome **не принимает hostname в Host-заголовке** (возвращает HTTP 500). Поэтому в `CDP_HOST` обязательно указывать **IPv4-адрес** (`192.168.65.254`), а не `host.docker.internal`. Значение может меняться после обновления Docker Desktop — проверяйте через `getent hosts host.docker.internal`.

### Как работает реф. ссылка Яндекса

`YandexParser._cdp_get_referral_link`:

1. Установка перехватчиков `navigator.clipboard.writeText`, `fetch`, `XMLHttpRequest`.
2. Клик по «Поделиться» (по `data-zone-name` / `data-baobab-name` / `aria-label`).
3. Клик по «Копировать ссылку».
4. Возврат ссылки `/cc/xxxxx` + HTML карточки за один рейс.

### Как работает скрапинг хештегов VK

`services/vk_trending.py`:

1. Найти iframe с `vklanding.com` в `/json/list`.
2. Подключиться к `webSocketDebuggerUrl`.
3. Дождаться ссылок `/hashtag/`, извлечь хештеги.
4. Кликнуть «вперёд» для следующей страницы.

---

## Переменные окружения

### parser

| Переменная | По умолчанию | Описание |
|-----------|--------------|----------|
| `CDP_HOST` | `192.168.65.254` | IPv4 хоста с Chrome CDP |
| `CDP_PORT` | `9222` | Порт CDP |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | Адрес RabbitMQ |
| `DATABASE_URL` | `postgresql+asyncpg://...` | DSN PostgreSQL |
| `LOG_LEVEL` | `INFO` | Уровень логов |
| `DB_CONNECT_RETRIES` | `10` | Попытки подключения к БД при старте |
| `DB_CONNECT_DELAY` | `3.0` | Пауза между попытками (сек) |
| `RMQ_CONNECT_RETRIES` | `10` | Попытки подключения к RabbitMQ |
| `RMQ_CONNECT_DELAY` | `3.0` | Пауза (сек) |

### worker — Telegram

| Переменная | Описание |
|-----------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен бота |
| `TELEGRAM_CHANNEL_ID` | Канал для обычных результатов |
| `TELEGRAM_GROUP_ID` | Группа для всех (fallback = channel) |
| `TELEGRAM_PROXY_URL` | `socks5://user:pass@host:1080` |

### worker — Такпродам

| Переменная | Описание |
|-----------|----------|
| `TAKPRODAM_API_TOKEN` | Токен API |
| `TAKPRODAM_SOURCE_ID` | ID подтверждённой площадки |
| `TAKPRODAM_MARKETPLACE` | Фильтр: `Ozon` / `Wildberries` / `Avito` / `Aliexpress` |
| `TAKPRODAM_SUBID` | Метка трафика (по умолчанию `tg_daily`) |
| `TAKPRODAM_PUBLISH_INTERVAL` | Интервал публикации (сек, по умолчанию 120) |
| `TAKPRODAM_EMPTY_INTERVAL` | Пауза при пустом каталоге (сек, 600) |
| `TAKPRODAM_MAX_PAGES` | Макс. страниц при поиске свежего (20) |

### worker — Яндекс.рефералка

| Переменная | По умолчанию | Описание |
|-----------|--------------|----------|
| `YANDEX_AUTO_REFERRAL` | `1` | Автороутинг Яндекс-ссылок |
| `YANDEX_REFERRAL_INTERVAL` | `3600` | Интервал цикла (сек) |
| `YANDEX_REFERRAL_MAX_PAGES` | `100` | Макс. страниц пагинации |
| `YANDEX_REFERRAL_EMPTY_STREAK` | `3` | Пустых подряд → стоп |
| `YANDEX_REFERRAL_TARGET_FRESH` | `12` | Сколько свежих набрать в DOM |
| `YANDEX_REFERRAL_PUBLISH_LIMIT` | `10` | Макс. публикаций за цикл |
| `YANDEX_REFERRAL_PUBLISH_DELAY` | `6.0` | Пауза между постами (сек) |
| `YANDEX_REFERRAL_SCROLL_MAX_SECONDS` | `120.0` | Лимит времени на скролл |
| `YANDEX_REFERRAL_SCROLL_NO_GROWTH` | `10` | Проверок без прироста → стоп |

### worker — VK Digest

| Переменная | По умолчанию | Описание |
|-----------|--------------|----------|
| `VK_ACCESS_TOKEN` | — | Токен VK (`video`, `wall`, `offline`) |
| `VK_USER_ID` | `0` | Числовой ID |
| `VK_DIGEST_INTERVAL` | `86400` | Интервал клипа (сек) |
| `VK_PHOTOS_PER_VIDEO` | `5` | Фото в клипе |
| `VK_PHOTO_DURATION` | `3.0` | Длительность кадра (сек) |
| `VK_VIDEO_WIDTH` / `HEIGHT` | `1080` / `1920` | Размер |
| `VK_HASHTAGS` | — | Env-фолбэк хештегов |
| `VK_HASHTAGS_COUNT` | `3` | Сколько хештегов в посте |
| `VK_HASHTAGS_SOURCE` | `trending` | `trending` / `env` |
| `VK_TRENDING_PAGES` | `2` | Страниц трендов |
| `VK_TRENDING_REFRESH_SEC` | `21600` | Обновление трендов |
| `VK_CHANNEL_LINK` | — | Ссылка на TG в VK-посте |

**Тонкая настройка видео:**

| Переменная | По умолчанию |
|-----------|--------------|
| `VK_VIDEO_FPS` | `30` |
| `VK_BG_BLUR` | `55` |
| `VK_BG_BRIGHTNESS` | `-0.22` |
| `VK_BG_SATURATION` | `1.4` |
| `VK_FG_FIT_WIDTH` / `HEIGHT` | `0.88` / `0.85` |
| `VK_FG_BORDER` / `COLOR` | `8` / `white` |
| `VK_KB_ZOOM` / `SPEED` | `1.08` / `0.0006` |
| `VK_TRANSITIONS` | `fade,slideleft,slideright,wipeleft,circleopen` |

### worker — VK Stories (опционально)

| Переменная | По умолчанию | Описание |
|-----------|--------------|----------|
| `VK_PUBLISH_STORY` | `0` | `1` — публиковать сторис |
| `VK_STORY_GROUP_ID` | `0` | `0` — от себя |
| `VK_STORY_LINK_TEXT` | `more` | Текст кнопки |
| `VK_STORY_LINK_URL` | = `VK_CHANNEL_LINK` | URL кнопки |

### worker — Медиа

| Переменная | По умолчанию |
|-----------|--------------|
| `MEDIA_ROOT` | `/app/media` |

---

## RabbitMQ: очереди и потоки

| Очередь | Тип | Назначение |
|---------|-----|------------|
| `parser.incoming` | direct | Входящие URL |
| `parser.errors` | direct | Ошибки парсинга |
| `parser.results.storage` | fanout | Результаты → PostgreSQL |
| `parser.results.telegram` | fanout | Результаты → Telegram |
| `parser.referral.manual` | direct | Ручные Яндекс-ссылки |

Exchange `parser.results.fanout` публикует в `storage` и `telegram` одновременно.

**Поток данных:**

```
POST /queue/enqueue
    └─▶ parser.incoming
            └─▶ consumer парсит URL
                    ├─▶ parser.results.fanout ──▶ storage_consumer → PostgreSQL
                    │                          └─▶ telegram_consumer → TG + media
                    ├─▶ parser.errors (при ошибке)
                    └─▶ parser.referral.manual (если Yandex-ссылка, автороутинг)
```

---

## Источники публикаций

### 1. Обычный парсинг (`telegram_consumer.py`)

- **Триггер:** URL в `parser.incoming`.
- **Фильтры:** `success=true`, есть цена, `in_stock != false`, `already_exists=false`.
- **Действия:**
  1. Скачать фото.
  2. Отрендерить карточку (`render_product_card`).
  3. Сохранить в `media/photos/` и в `published_media`.
  4. Отправить `send_photo` в Telegram-группу.
  5. Сохранить ссылку на пост в `published_messages`.

### 2. Такпродам (`daily_publisher.py`) — отключён в `worker.py`

Раскомментируйте `run_daily_publisher()` в `worker.py`, если нужно.

Логика: раз в `TAKPRODAM_PUBLISH_INTERVAL` секунд запрашивает каталог, обогащает недостающие поля (зачёркнутая цена, рейтинг, бренд) из CDP-парсера, рендерит карточку, публикует. **Если рендер упал — пост НЕ публикуется** (товар не помечается, попытка повторится).

### 3. Рефералка Яндекс.Маркета (`yandex_referral_publisher.py`)

**A. Планировщик** (`run_yandex_referral_publisher`):

1. CDP → `/page/referral_products?tabId=...` (ротация по 12 табам).
2. Human-like скролл до `TARGET_FRESH` неопубликованных товаров.
3. `parse_search()` / DOM-сниппеты → список товаров.
4. Фильтр по `published_referral_products` + картинке.
5. Для каждого свежего: `_cdp_get_referral_link()` → `/cc/xxx` + HTML.
6. `_card_from_html()` → цены.
7. `save_product()` + `save_media()`.
8. `_send_to_telegram()` → пост с карточкой.
9. `mark_referral_published()`.

**B. Ручной консьюмер** (`run_referral_manual_consumer`):

- Слушает `parser.referral.manual`.
- Туда попадают URL от `POST /api/v1/referral/publish` и авто-роутинг Яндекс-ссылок.
- Общий `_cdp_lock` с планировщиком — вкладка не ломается при параллельном запуске.

---

## Парсеры

### Ozon (`parsers/ozon.py`)

- **Tier 1:** `composer-api.bx` через httpx с реалистичными заголовками.
- **Tier 2:** CDP fetch из уже открытой вкладки `ozon.ru` (новая вкладка не успевает получить cookies и получает 403).
- **Fallback:** DOM-цены (`.pdp_b2h`, `.pdp_b2i`, `.pdp_b2d`).
- **Цены:** все три поля заполняются честно — `price_rub` (обычная), `price_with_plus` (Ozon Card), `price_original_rub` (зачёркнутая).

### Wildberries (`parsers/wildberries.py`)

- **Только CDP.** API живёт на `wildberries.ru/__internal/card/cards/v4/detail`.
- **Credentials** собираются **на каждый запрос** из localStorage: `Bearer` + `deviceid` + `xinfo` (кэша нет — токен может меняться).
- **Изображение:** regex по HTML карточки → навигация на карточку → DOM `querySelector`.

### AliExpress (`parsers/aliexpress.py`)

- **Только CDP.** Читает DOM открытой вкладки `/item/<id>.html`.
- Селекторы `HazeProductPrice_*`, апгрейд картинок до 960×960.
- Автосоздание вкладки, если карточки нет.
- Проверка на x5sec challenge (`punish: true`).

### Яндекс.Маркет (`parsers/yandex.py`)

- **SSR:** `<noframes data-apiary="patch">` — полностью server-rendered widget state.
- **CDP-фолбэк:** hard reload → ожидание `allPrices` → `outerHTML`.
- **DOM-фолбэк:** цены вытягиваются JS → `<script id="__cdp_prices__">`.
- **Реферальные ссылки:** `_cdp_get_referral_link()` — клик «Поделиться» + перехват `clipboard.writeText`.

### Базовая логика (`parsers/base.py`)

- **`BaseParser`** — абстрактный класс с `extract_sku()` + `fetch()`.
- **Per-class TTL-кэш** на 5 минут (`fetch_cached`) — у каждого парсера свой.
- **`cdp_wait_for_load`** — ждёт `Page.loadEventFired` или `Page.frameNavigated`, не спит фиксированные секунды.

---

## VK Digest

`vk_digest_publisher.py` раз в `VK_DIGEST_INTERVAL` (по умолчанию — сутки):

1. Обновить хештеги из `vk.ru/trending` (раз в 6 ч). Если trending недоступен — env-фолбэк.
2. Взять 5 неиспользованных фото (`video_id IS NULL`).
3. `ffmpeg` собирает клип:
   - `scale + crop + gblur` для фона 9:16;
   - `scale` для переднего плана + `overlay` по центру;
   - Ken Burns + xfade;
   - случайный трек из `media/music/`.
4. `VKClient.upload_video` → `video.save` + POST + `wallpost=1`.
5. Удалить фото и записи `published_media`.
6. Записать в `published_videos`.
7. Раз в 30 дней — `cleanup_old_media(days=30)`.

Минимум `MIN_PHOTOS = 3`, фактически ждёт `VK_PHOTOS_PER_VIDEO`.

---

## REST API

Все эндпоинты — `/api/v1`.

### Парсинг

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| POST | `/parse` | Синхронный парсинг одного URL |

### Очередь

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| POST | `/queue/enqueue` | Добавить URL (rate-limit 30/min) |
| POST | `/queue/enqueue-batch` | Список URL |
| GET | `/queue/status` | Статус очередей RabbitMQ |

### Результаты

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/results` | Список с фильтрами |
| GET | `/results/stats` | Статистика |
| GET | `/results/{id}` | Один товар |
| GET | `/results/{id}/history` | История цен |
| DELETE | `/results/{id}` | Удалить |
| GET | `/results/stream` | SSE-поток новых товаров |
| GET | `/results/export/csv` | Экспорт CSV |

### Сообщения

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/messages` | Список опубликованных сообщений |
| DELETE | `/messages/{id}` | Удалить (`delete_from_telegram=true` — и в TG) |

### Рефералка

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| POST | `/referral/publish` | Поставить URL в `referral.manual` |

### Прочее

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/health` | Healthcheck |
| GET | `/cdp-check` | Диагностика Chrome CDP |
| POST | `/compare` | Сравнение цен 4 маркетплейсов |

---

## База данных и Alembic

Схема управляется **Alembic**. При старте `parser` выполняет:

```bash
alembic upgrade head
```

Затем запускается uvicorn. `worker` ждёт `parser: service_healthy`.

### Модели

| Таблица | Назначение |
|---------|------------|
| `products` | Результаты парсинга. UNIQUE(`sku`, `marketplace`) |
| `price_history` | История цен — новая запись при каждом парсинге |
| `published_products` | Дедупликация Такпродама. UNIQUE(`takprodam_id`) |
| `published_referral_products` | Дедупликация Яндекс.рефералки (TTL 30 дней) |
| `published_media` | Фото для VK-клипов (со стилизованными карточками) |
| `published_videos` | Собранные клипы |
| `published_messages` | Ссылки на посты в Telegram |

### Изменить схему

```bash
docker compose run --rm parser alembic revision --autogenerate -m "add field X"
# проверь файл в alembic/versions/
docker compose run --rm parser alembic upgrade head
docker compose restart parser worker
```

### Полезные команды

| Команда | Что делает |
|---------|-----------|
| `alembic current` | Текущая ревизия |
| `alembic history` | Все миграции |
| `alembic upgrade head` | Применить всё |
| `alembic downgrade -1` | Откатить последнюю |
| `alembic stamp head` | Пометить текущую схему как актуальную |

> **Важно:** `alembic/versions/*.py` коммитятся в git. `__pycache__/` — в `.gitignore`.

> **Volume:** папка `./alembic` монтируется в оба сервиса — миграции, созданные внутри контейнера, сохраняются на хосте.

---

## Логирование

`core/logging_setup.py` настраивает JSON-формат на root-логгере:

```json
{"ts":"2026-10-14T11:02:17+0000","level":"INFO","logger":"app","msg":"Запуск Marketplace Parser API v1.0.0"}
```

В `main.py` и `worker.py` вызывается `setup_logging()` вместо `basicConfig`. Остальные модули используют `logging.getLogger(__name__)` без изменений.

Уровень задаётся через `LOG_LEVEL`. Для Loki/ELK парсинг JSON тривиален.

---

## Скрипты

| Файл | Назначение |
|------|-----------|
| `card_tuner.py` | GUI-тюнер карточки (Ozon/YM/WB/Ali). `python scripts/card_tuner.py test.jpg` |
| `check_admitad.py` | Проверка Admitad-ссылок |
| `check_aliexpress.py` | Быстрый тест AliExpress |
| `read_result.py` | Вывод последнего товара из БД |
| `test_card.py` | Прогон рендера на тестовом фото |

---

## Разработка

### Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows
pip install -r requirements.txt

docker compose up -d postgres rabbitmq
alembic upgrade head
uvicorn main:app --reload
python worker.py                   # в отдельном окне
```

### Тесты

```bash
pip install -r requirements-test.txt
pytest
```

### Фронтенд

```bash
cd frontend
npm install
npm run dev      # :5173
npm run build
```

### Разовые задачи

```bash
# Опубликовать 1 товар Такпродама
docker compose exec worker python -c "import asyncio; from broker.daily_publisher import _publish_one; asyncio.run(_publish_one())"

# Опубликовать 1 реферальный товар Яндекса
docker compose exec worker python -c "import asyncio; from broker.yandex_referral_publisher import _publish_one; asyncio.run(_publish_one())"

# Ручная публикация конкретного URL
docker compose exec worker python -c "import asyncio; from broker.yandex_referral_publisher import _publish_one_url; asyncio.run(_publish_one_url('https://market.yandex.ru/card/.../1234567890'))"

# Собрать VK-клип сейчас
docker compose exec worker python -c "import asyncio; from broker.vk_digest_publisher import _make_and_upload_one; asyncio.run(_make_and_upload_one())"

# Обновить хештеги VK
docker compose exec worker python -c "import asyncio; from broker.vk_digest_publisher import _refresh_trending_hashtags; asyncio.run(_refresh_trending_hashtags())"
```

---

## Эксплуатация и troubleshooting

### ⚠️ Обязательные условия

- **CDP-вкладки должны быть залогинены.** Без этого Яндекс не покажет кнопку «Поделиться», WB не отдаст данные, AliExpress покажет x5sec.
- **`CDP_HOST` = IPv4, а не hostname.** Chrome не принимает `host.docker.internal` в Host-заголовке.

### Что как работает внутри

- **`_cdp_lock`** — один глобальный lock для ручного и автоматического реферального пайплайна. Параллельные запросы к одной вкладке не ломают её.
- **TTL-кэш результатов:** `fetch_cached` кэширует успешный парсинг на 5 минут. Кэш — per-class.
- **Rate-limit** `/queue/enqueue` — 30/мин с IP (slowapi).
- **Auto-reconnect worker:** при падении любой задачи все консьюмеры перезапускаются через 5 секунд (без накопления зомби-задач).
- **История цен:** каждое сохранение пишет в `price_history`, даже для существующих товаров.
- **Free-space check:** `save_photo` пропускает запись, если <500 MB свободно.
- **Health worker:** `/health` возвращает `uptime` и `last_message_ago`. `update_last_message()` вызывается в каждом консьюмере.

### Дедупликация

- `UNIQUE(sku, marketplace)` в `products`;
- `UNIQUE(takprodam_id)` в `published_products`;
- `UNIQUE(product_id)` в `published_referral_products`.

### Очистка

- медиа — раз в 30 дней (`cleanup_old_media`);
- рефералка — раз в 30 дней (`cleanup_old_referral_publications`);
- орфанные фото — `cleanup_orphan_photos()` вручную.

### Частые проблемы

| Симптом | Причина / решение |
|---------|-------------------|
| Ozon HTTP 403 | Блокировка IP. Проверить `CDP_HOST` и что вкладка `ozon.ru` залогинена. |
| WB HTTP 401 | Истёк `Bearer` токен. Перезайти на `wildberries.ru` в Chrome. |
| AliExpress x5sec | Пройти challenge вручную в Chrome, оставить вкладку. |
| Яндекс «урезанная страница» | Датацентровый IP. CDP-фолбэк должен спасти; проверить, что вкладка `market.yandex.ru` открыта. |
| `parser_drift` (Ozon) | API сменил формат. Для daily_publisher работает salvage через CDP + DOM. |
| VK hashtags = env-фолбэк | `vk.ru/trending` недоступен. Проверить вкладку и `VK_TRENDING_PAGES`. |
| Docker перезапустился — CDP отвалился | Проверить `CDP_HOST` через `getent hosts host.docker.internal`. |
| `alembic` в контейнере не видит миграции | Проверить volume `./alembic:/app/alembic`. |

---

## Лицензия

Проект распространяется «как есть» для личного использования. Соблюдайте Terms of Service маркетплейсов при парсинге.