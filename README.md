# Marketplace Parser API

Система для парсинга товаров с российских маркетплейсов (**Ozon**, **Wildberries**, **AliExpress**, **Яндекс.Маркет**) с сохранением результатов в PostgreSQL, публикацией в **Telegram** и **VK**, веб-дашбордом и автоматической сборкой вертикальных видео для VK Клипов.

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
- [VK Digest (видео)](#vk-digest-видео)
- [API](#api)
- [Парсеры](#парсеры)
- [Очереди RabbitMQ](#очереди-rabbitmq)
- [Схема БД](#схема-бд)
- [Скрипты](#скрипты)
- [Разработка и тесты](#разработка-и-тесты)
- [Особенности эксплуатации](#особенности-эксплуатации)

---

## Возможности

- 🔍 **Парсинг 4 маркетплейсов**: Ozon, Wildberries, AliExpress, Яндекс.Маркет
- 🐇 **Асинхронная обработка** через RabbitMQ (`incoming` / `results.storage` / `results.telegram` / `referral.manual` / `errors`)
- 🗄 **PostgreSQL** + история цен (`PriceHistory`)
- 📢 **Три Telegram-канала публикации**:
  - обычные результаты парсинга URL из очереди (`telegram_consumer.py`);
  - ежедневная публикация товара из каталога Такпродам (`daily_publisher.py`, в `worker.py` закомментирован);
  - публикация из реферальной программы Яндекс.Маркета (`yandex_referral_publisher.py`).
- 🔁 **Автороутинг Яндекс-ссылок**: `consumer.py` при получении любой ссылки `market.yandex.ru` (кроме `/search`) автоматически перекладывает её в очередь `parser.referral.manual` — публикация происходит через реферальный конвейер с получением `/cc/`-ссылки.
- 📨 **Учёт опубликованных сообщений**: каждая успешная отправка в Telegram сохраняется в `published_messages` со ссылкой вида `https://t.me/c/.../<id>` — дашборд показывает их на вкладке «Сообщения» с кнопкой «Копировать».
- 🎬 **VK Digest** — раз в сутки собирает 5 последних фото из Telegram-публикаций в вертикальный клип 9:16 с музыкой и трендовыми хештегами VK, публикует в личный профиль.
- 🎨 **Стилизация карточек** товаров через Pillow (`overlay_maker` + `scripts/card_tuner.py`): «НИЖЕ РЫНКА», цена с иконкой Пэй, бренд, юр. текст.
- 🔗 **Автополучение реферальной ссылки** Яндекс.Маркета через CDP — клик «Поделиться» → перехват `clipboard.writeText` → ссылка `/cc/xxxxx`.
- 🎵 **Трендовые хештеги VK** — скрапинг `vk.ru/trending` через CDP с обновлением раз в 6 часов.
- 🚫 **Защита от дублей**: `published_products`, `published_referral_products`.
- 📊 **React-дашборд**: вкладки «Товары» и «Сообщения», фильтры, поиск, статистика, SSE-обновления, превью постов.
- 🌐 **REST API** на FastAPI: парсинг, очередь, результаты, сравнение цен, экспорт CSV, публикация рефералок, список сообщений.
- 🧩 **Chrome CDP** для обхода антибот-защиты (x5sec, SmartCaptcha, реферальные попапы).
- 🎟 **Поддержка промокодов** в постах (поле `promo_code` / `promo_percent`).
- 🎬 **VK Stories** (опционально) — публикация сторис в группу/профиль с CTA-ссылкой.

---

## Архитектура

```
                 ┌────────────┐         ┌─────────────────────┐
   HTTP ──────▶ │  parser    │ ──────▶ │  RabbitMQ           │
  (FastAPI)      │  (main.py) │         │  parser.incoming    │
                 └────────────┘         └──────────┬──────────┘
                                                   │
                                                   ▼
                                         ┌──────────────────┐
                                         │  worker.py       │
                                         │  consumer.py     │
                                         └────────┬─────────┘
                                                  │
       ┌──────────────────────┬───────────────────┼────────────────────┬───────────────┐
       │                      │                   │                    │               │
       ▼                      ▼                   ▼                    ▼               ▼
parser.results.storage  parser.results.telegram  parser.errors  referral.manual   VK / media
       │                      │                                     │               │
       ▼                      ▼                                     ▼               ▼
┌─────────────┐       ┌──────────────┐                       ┌──────────────┐  ┌──────────────┐
│ PostgreSQL  │       │  Telegram    │                       │  Yandex      │  │  media/      │
│  products   │       │  (group)     │                       │  referral    │  │  photos/     │
│  media      │       │  messages    │                       │  publisher   │  │  videos/     │
│  messages   │       │  (links)     │                       └──────────────┘  │  music/      │
└──────┬──────┘       └──────────────┘                                         └──────┬───────┘
       │                                                                            │
       ▼                                                                            ▼
┌──────────────┐                                                          ┌──────────────────┐
│  frontend    │                                                          │  VK API          │
│  (React)     │                                                          │  video.save      │
│  Товары+Сообщения│                                                      │  wall.post       │
└──────────────┘                                                          │  stories         │
                                                                          └──────────────────┘
```

**Процессы:**
- **parser** — FastAPI, принимает URL, публикует в `parser.incoming`. Эндпоинты `/parse`, `/queue/*`, `/results/*`, `/messages/*`, `/referral/publish`, `/compare`, `/health`, `/cdp-check`.
- **worker** — 5 консьюмеров + 3 планировщика + health:
  - `consumer` — `incoming` → parse → fanout `results` (или роутинг Яндекс-ссылок в `referral.manual`);
  - `storage_consumer` — `results.storage` → PostgreSQL;
  - `telegram_consumer` — `results.telegram` → Telegram + сохранение фото и ссылки на сообщение;
  - `run_referral_manual_consumer` — `referral.manual` → CDP-получение `/cc/`-ссылки → публикация;
  - `yandex_referral_publisher` — каждые N секунд обходит `/page/referral_products` → публикует свежие;
  - `daily_publisher` — раз в сутки Такпродам → Telegram (в `worker.py` **закомментирован**);
  - `vk_digest_publisher` — раз в сутки сборка видео из накопленных фото → VK Клипы (+ опционально Stories);
  - `worker_health` — HTTP `/health` на :8080.

---

## Стек технологий

| Слой | Технологии |
|------|-----------|
| Backend | Python 3.12, FastAPI, uvicorn, Pydantic |
| Брокер | RabbitMQ 3.13, `aio-pika` |
| БД | PostgreSQL 16, SQLAlchemy 2 (async), asyncpg |
| Парсинг | httpx, Playwright, `websocket-client` (Chrome CDP) |
| Telegram | aiogram 3, aiohttp (SOCKS5/HTTP-прокси) |
| VK | VK API 5.199 (`video.save`, `wall.post`, `stories`) |
| Медиа | Pillow (карточки), ffmpeg (видео 9:16) |
| Внешние API | Такпродам (`api.takprodam.ru`), Яндекс.Маркет (SSR + CDP) |
| Frontend | React 18, TypeScript, Vite 5, nginx |
| Инфра | Docker, Docker Compose |

---

## Структура проекта

```
.
├── main.py                        # FastAPI приложение (parser)
├── worker.py                      # Воркер: консьюмеры + планировщики
├── worker_health.py               # /health на :8080 для воркера
├── db.py                          # SQLAlchemy модели + репозиторий
├── Dockerfile                     # Универсальный образ (parser + worker)
├── docker-compose.yml
├── requirements.txt
├── requirements-test.txt
├── start-chrome-cdp.bat           # Запуск Chrome с CDP на Windows
│
├── broker/
│   ├── connection.py              # Broker (aio-pika) + очереди + EXCHANGE_RESULTS
│   ├── producer.py                # send_url / send_urls
│   ├── consumer.py                # incoming → parse → results / referral.manual
│   ├── storage_consumer.py        # results.storage → PostgreSQL
│   ├── telegram_consumer.py       # results.telegram → Telegram + save_media + save_published_message
│   ├── daily_publisher.py         # Такпродам → Telegram (раз в сутки, выключен в worker.py)
│   ├── yandex_referral_publisher.py # Яндекс.Маркет → Telegram (цикл + manual consumer)
│   └── vk_digest_publisher.py     # Фото → видео 9:16 → VK (+ Stories)
│
├── parsers/
│   ├── base.py                    # BaseParser + TTLCache
│   ├── ozon.py                    # Ozon (composer-api + CDP + DOM)
│   ├── wildberries.py             # WB (__internal v4 + CDP)
│   ├── aliexpress.py              # AliExpress (CDP + DOM)
│   └── yandex.py                  # Яндекс.Маркет (SSR + CDP + реф. ссылки)
│
├── services/
│   ├── marketplace.py             # MarketplaceFactory (URL → парсер)
│   ├── takprodam.py               # Клиент API Такпродам
│   ├── media_storage.py           # Сохранение/удаление фото
│   ├── overlay_maker.py           # Стилизация карточек (Pillow)
│   ├── video_maker.py             # Сборка слайдшоу 9:16 (ffmpeg)
│   ├── vk_client.py               # VK API (video.save, wall.post)
│   └── vk_trending.py             # Скрапер трендовых хештегов VK
│
├── routers/
│   ├── parse.py                   # POST /parse, GET /health, /cdp-check
│   ├── queue.py                   # POST /queue/enqueue(-batch), GET /queue/status
│   ├── results.py                 # GET /results, /stats, /stream, /export/csv
│   ├── messages.py                # GET/DELETE /messages — учёт опубликованных сообщений
│   ├── referral.py                # POST /referral/publish — ручная публикация рефералки
│   └── compare.py                 # POST /compare
│
├── core/
│   ├── models.py                  # ProductData, MarketplaceType
│   ├── formatters.py              # PriceFormatter, TextGenerator
│   └── telegram_links.py          # build_message_link()
│
├── config/settings.py             # CDP_HOST, CDP_PORT, LOG_LEVEL
│
├── scripts/
│   ├── card_tuner.py              # GUI-тюнер карточки (Ozon/YM/WB/Ali)
│   ├── card_tuner_old.py          # Предыдущая версия тюнера
│   ├── check_admitad.py
│   ├── check_aliexpress.py
│   ├── read_result.py             # Быстрый просмотр результата парсинга
│   └── test_card.py
│
└── frontend/                      # React + Vite + nginx
    ├── Dockerfile
    ├── nginx.conf
    └── src/
        ├── App.tsx                # Вкладки «Товары» / «Сообщения»
        ├── MessagesPage.tsx       # Таблица опубликованных сообщений
        ├── api.ts
        └── styles.css
```

---

## Быстрый старт

### 1. Chrome с CDP (обязательно для 4 парсеров + VK trending)

**Windows** — `start-chrome-cdp.bat`.
**Вручную:**
```bash
chrome --remote-debugging-port=9222 \
       --remote-debugging-address=0.0.0.0 \
       --remote-allow-origins=* \
       --user-data-dir=/path/to/chrome-scraping
```

В этом Chrome:
1. Войти в `ozon.ru`, `wildberries.ru`, `market.yandex.ru`, `aliexpress.ru`, `vk.ru`.
2. Пройти капчу/x5sec вручную, если появится.
3. Оставить вкладки `market.yandex.ru` и `wildberries.ru` открытыми — нужны для реферальных ссылок и WB-токенов.
4. Открыть `vk.ru/trending` — нужен для скрапинга трендовых хештегов.

### 2. Музыка для VK-видео

Положите 3–10 mp3/m4a треков в `./music/` (монтируется в `/app/media/music`). Треки выбираются случайно для каждого клипа. Если папка пуста — видео собирается без звука.

### 3. Запуск стека

```bash
docker compose up -d --build
```

| Сервис | URL |
|--------|-----|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000/api/v1/health |
| RabbitMQ UI | http://localhost:15672 (guest/guest) |
| PostgreSQL | `localhost:5432` (parser/parser) |

### 4. Проверка

```bash
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/cdp-check
```

### 5. Отправить URL на парсинг

```bash
curl -X POST http://localhost:8000/api/v1/queue/enqueue \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.ozon.ru/product/1234567890/"}'
```

Результат появится в дашборде через ~10 сек. Если ссылка — на `market.yandex.ru` (не `/search`), она автоматически уйдёт в реферальный конвейер.

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

### worker — Telegram

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен бота |
| `TELEGRAM_CHANNEL_ID` | ID канала для обычных результатов |
| `TELEGRAM_GROUP_ID` | ID группы для daily_publisher / yandex_referral / referral.manual (fallback = CHANNEL_ID) |
| `TELEGRAM_PROXY_URL` | SOCKS5/HTTP-прокси (`socks5://user:pass@host:1080`) |

### worker — Такпродам

| Переменная | Описание |
|------------|----------|
| `TAKPRODAM_API_TOKEN` | Токен из ЛК Такпродам |
| `TAKPRODAM_SOURCE_ID` | id подтверждённой площадки (`status=approved`) |
| `TAKPRODAM_MARKETPLACE` | Опциональный фильтр: `Ozon`/`Wildberries`/`Avito`/`Aliexpress` |
| `TAKPRODAM_SUBID` | Метка трафика (по умолчанию `tg_daily`) |
| `PUBLISH_HOUR` | Час публикации Такпродам по локали (по умолчанию `20`) |
| `PUBLISH_TZ_OFFSET` | Смещение таймзоны, МСК = `3` |

### worker — Яндекс.рефералка

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `YANDEX_AUTO_REFERRAL` | `1` | Автороутинг Яндекс-ссылок из `incoming` в `referral.manual` |
| `YANDEX_REFERRAL_INTERVAL` | `3600` | Интервал публикации рефералки в секундах |
| `YANDEX_REFERRAL_MAX_PAGES` | `100` | Макс. страниц пагинации рефералки за цикл |
| `YANDEX_REFERRAL_EMPTY_STREAK` | `3` | Сколько пустых страниц подряд = конец пагинации |
| `YANDEX_REFERRAL_PUBLISH_DELAY` | `12.0` | Пауза между постами в секундах |

### worker — VK Digest

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `VK_ACCESS_TOKEN` | — | Токен VK (нужны права `video`, `wall`, `offline`) |
| `VK_USER_ID` | `0` | Числовой ID пользователя VK |
| `VK_DIGEST_INTERVAL` | `86400` | Интервал сборки видео (сек) |
| `VK_PHOTOS_PER_VIDEO` | `5` | Сколько фото в одном клипе |
| `VK_PHOTO_DURATION` | `3.0` | Длительность одного кадра (сек) |
| `VK_VIDEO_WIDTH` | `1080` | Ширина видео |
| `VK_VIDEO_HEIGHT` | `1920` | Высота видео (9:16) |
| `VK_HASHTAGS` | `#скидки,#маркетплейсы,...` | Env-фолбэк хештегов |
| `VK_HASHTAGS_COUNT` | `3` | Сколько хештегов в посте |
| `VK_HASHTAGS_SOURCE` | `trending` | `trending` (vk.ru/trending) или `env` |
| `VK_TRENDING_PAGES` | `2` | Сколько страниц трендов обходить |
| `VK_TRENDING_REFRESH_SEC` | `21600` | Частота обновления трендов (6 ч) |
| `VK_CHANNEL_LINK` | `https://t.me/PromoMP1` | Ссылка на Telegram-канал в VK-посте |

### worker — VK Stories (опционально)

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `VK_PUBLISH_STORY` | `0` | `1` — публиковать сторис вместе с видео |
| `VK_STORY_GROUP_ID` | `0` | ID группы для сторис (`0` — от себя) |
| `VK_STORY_LINK_TEXT` | `more` | Текст кнопки-ссылки в сторис |
| `VK_STORY_LINK_URL` | `VK_CHANNEL_LINK` | URL кнопки-ссылки в сторис |

### worker — Медиа

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `MEDIA_ROOT` | `/app/media` | Корень хранилища фото/видео/музыки |

### Пример `.env`

```env
# Telegram
TELEGRAM_BOT_TOKEN=123456:AAA...
TELEGRAM_CHANNEL_ID=@my_channel
TELEGRAM_GROUP_ID=-1001234567890
TELEGRAM_PROXY_URL=socks5://user:pass@host:1080

# Такпродам
TAKPRODAM_API_TOKEN=xxxx-xxxx-xxxx
TAKPRODAM_SOURCE_ID=12345
TAKPRODAM_MARKETPLACE=Ozon
TAKPRODAM_SUBID=tg_daily
PUBLISH_HOUR=20
PUBLISH_TZ_OFFSET=3

# Яндекс.рефералка
YANDEX_AUTO_REFERRAL=1
YANDEX_REFERRAL_INTERVAL=3600
YANDEX_REFERRAL_MAX_PAGES=100
YANDEX_REFERRAL_EMPTY_STREAK=3
YANDEX_REFERRAL_PUBLISH_DELAY=12.0

# VK
VK_ACCESS_TOKEN=vk1.a.xxxxx
VK_USER_ID=1234567
VK_DIGEST_INTERVAL=86400
VK_PHOTOS_PER_VIDEO=5
VK_PHOTO_DURATION=3.0
VK_VIDEO_WIDTH=1080
VK_VIDEO_HEIGHT=1920
VK_HASHTAGS=#скидки,#маркетплейсы,#промокоды
VK_HASHTAGS_COUNT=3
VK_HASHTAGS_SOURCE=trending
VK_TRENDING_PAGES=2
VK_TRENDING_REFRESH_SEC=21600
VK_CHANNEL_LINK=https://t.me/PromoMP1

# VK Stories
VK_PUBLISH_STORY=0
VK_STORY_GROUP_ID=0
VK_STORY_LINK_TEXT=more
VK_STORY_LINK_URL=
```

---

## Chrome CDP

Все четыре парсера используют Chrome DevTools Protocol:

| Парсер | Основной источник | Роль CDP |
|--------|-------------------|----------|
| **Ozon** | composer-api.bx (httpx) | Tier 2: fetch из залогиненной вкладки + чтение DOM (`.pdp_b2h`, `.pdp_b2i`) |
| **Wildberries** | `__internal/card/cards/v4/detail` | Tier 1: Bearer-токен + deviceid + xinfo из localStorage + fetch |
| **AliExpress** | DOM открытой вкладки | Единственный источник данных |
| **Яндекс.Маркет** | SSR `data-apiary="patch"` | Фолбэк + получение реферальной ссылки |
| **VK Trending** | `vk.ru/trending` → iframe `vklanding.com` | Извлечение хештегов из iframe |

### Получение реферальной ссылки Яндекса

Ключевая логика (`YandexParser._cdp_get_referral_link`):

1. Навигация на карточку товара.
2. Установка перехватчиков `navigator.clipboard.writeText`, `fetch`, `XMLHttpRequest`.
3. Поиск и клик по кнопке «Поделиться» (`data-zone-name`, `aria-label`, текст).
4. Клик по «Копировать ссылку» (`data-zone-name="copyLinkButton"`).
5. Возврат ссылки `/cc/xxxxx` из перехваченного буфера + HTML карточки за тот же рейс.

**Требования:** залогиненный Яндекс ID в Chrome + открытая вкладка `market.yandex.ru`.

### Получение трендовых хештегов VK

Логика (`services/vk_trending.py`):

1. Находим iframe с `vklanding.com` в `/json/list` (родительская страница `vk.ru/trending` — пустая обёртка).
2. Подключаемся к его `webSocketDebuggerUrl`.
3. Ждём появления ссылок `/hashtag/` (React рендерит их асинхронно).
4. Извлекаем хештеги.
5. Для следующей страницы — клик по кнопке пагинации внутри iframe.

**В Docker** для доступа к Chrome на хосте задано `extra_hosts: host.docker.internal:host-gateway`. На Linux замените `CDP_HOST` на IP хоста в вашей сети.

---

## Источники публикаций

В проекте работают **три Telegram-канала** публикации (группы) и **один VK-канал**.

### 1. Обычный парсинг (`telegram_consumer.py`)

- **Триггер:** URL в очереди `parser.incoming` → результат в fanout `parser.results.fanout`.
- **Логика:** парсинг → результат в fanout → storage + telegram.
- **Фильтры:** `success`, `price_rub`/`price_with_plus`, `in_stock`, `already_exists`.
- **Целевой чат:** `TELEGRAM_GROUP_ID` (fallback `TELEGRAM_CHANNEL_ID`).
- **Рендер:** Яндекс / WB / AliExpress — карточка через `overlay_maker`; для Ozon — оригинал.
- **Бонус:**
  - фото (уже отрендеренное) сохраняется в `media/photos/` для VK-клипа и в `published_media`;
  - ссылка на пост сохраняется в `published_messages`.

### 2. Такпродам (`daily_publisher.py`)

- **Триггер:** раз в сутки в `PUBLISH_HOUR:00` (МСК).
- **Источник:** `GET /v2/publisher/product/?source_id=...` из API Такпродам.
- **Логика:** фильтр по `published_products` → случайный товар → публикация → отметка в БД.
- **Текст:** `TextGenerator.generate()` + `legal_text` из ответа.
- **Целевой чат:** `TELEGRAM_GROUP_ID`.
- **Статус:** в `worker.py` **закомментирован** (раскомментируйте `run_daily_publisher()` при необходимости).

### 3. Рефералка Яндекс.Маркета (`yandex_referral_publisher.py`)

Работает в двух режимах:

**A. Планировщик** (`run_yandex_referral_publisher`):
- **Триггер:** раз в `YANDEX_REFERRAL_INTERVAL` секунд.
- **Источник:** `https://market.yandex.ru/page/referral_products?tabId=TOP_REWARD`.
- **Логика:**
  1. CDP → загрузка страницы → `parse_search()` (SSR).
  2. Фильтр по `published_referral_products` + наличию картинки.
  3. Для каждого свежего — `_publish_candidate()`: `_cdp_get_referral_link()` → `/cc/xxx` + HTML карточки.
  4. `_card_from_html()` парсит HTML → цены (Pay / обычная / зачёркнутая).
  5. `save_product()` → товар в дашборде; `save_media()` → фото для VK.
  6. `_send_to_telegram()` → пост с реферальной ссылкой и **стилизованной карточкой** (`render_product_card`).
  7. `save_published_message()` → ссылка на пост в `published_messages`.
  8. `mark_referral_published()` → защита от повтора.
- **TTL-очистка:** раз в сутки удаляются записи старше 30 дней (`cleanup_old_referral_publications`).

**B. Ручной консьюмер** (`run_referral_manual_consumer`):
- Слушает `parser.referral.manual`.
- Туда попадают:
  - URL от пользователя через `POST /api/v1/referral/publish`;
  - автоматически — любые Яндекс-ссылки из `parser.incoming` при `YANDEX_AUTO_REFERRAL=1`.
- Использует `_cdp_lock` (общий с планировщиком), чтобы не ломать вкладку `market.yandex.ru`.
- Если на входе `/cc/XXXX` — разворачивает через `_resolve_yandex_cc` (без следования редиректам, чтобы не попасть на SmartCaptcha), затем получает новую реферальную ссылку.

---

## VK Digest (видео)

Планировщик `vk_digest_publisher.py` раз в `VK_DIGEST_INTERVAL` (по умолчанию сутки):

1. **Обновляет трендовые хештеги** раз в `VK_TRENDING_REFRESH_SEC` (6 ч) через CDP.
2. **Берёт 5 последних неиспользованных фото** из `published_media` (у которых `video_id IS NULL`).
3. **Вписывает каждое фото** в 9:16 с размытой подложкой (`_fit_to_9_16`).
4. **Собирает слайдшоу** через `ffmpeg` (`services/video_maker.py`):
   - эффект Ken Burns, переходы `xfade`;
   - случайный mp3/m4a из `media/music/`;
   - длительность ≈ 5 × 3 сек = 15 сек.
5. **Публикует в VK** (`VKClient.upload_video`):
   - `video.save` → `upload_url`;
   - POST файла;
   - `wallpost=1` → авто-пост на стене;
   - текст поста — 3 случайных хештега + топ-5 товаров + ссылка на Telegram;
   - **опционально Stories** при `VK_PUBLISH_STORY=1`.
6. **Удаляет** исходные фото + промежуточные `_9x16.jpg` с диска и записи `published_media`.
7. **Записывает видео** в `published_videos`, после публикации удаляет `mp4` с диска.

Хештеги обновляются раз в 6 часов и берутся из `vk.ru/trending` (реальные тренды VK Клипов). Если скрапинг недоступен — используются значения из `VK_HASHTAGS`.

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

### Сообщения

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/messages` | Список опубликованных сообщений (`limit`, `offset`, `marketplace`, `only_ozon_short`) |
| DELETE | `/messages/{id}` | Удалить запись (опционально `delete_from_telegram=true` — удалит и в TG) |

### Рефералка

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| POST | `/referral/publish` | Поставить URL `market.yandex.ru/...` в очередь `parser.referral.manual` |

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
- **Tier 2:** CDP fetch из залогиненной вкладки `ozon.ru`.
- **Fallback:** чтение цен из DOM (`.pdp_b2h`, `.pdp_b2i`, `.pdp_b2d`).
- **Изображения:** `webGallery.coverImage` → `images[0].src`.
- **Логика цен:** если `cardPrice < price` — основная зелёная (Ozon Card), «было» = зачёркнутая или обычная.

### Wildberries (`parsers/wildberries.py`)

- **Tier 1 (CDP):** `Bearer`-токен + `deviceid` + `xinfo` из localStorage → `fetch` на `__internal/card/cards/v4/detail`.
- **Кэш credentials:** 5 минут (`_creds_ttl`).
- **Изображение:** regex по HTML карточки → DOM fallback (навигация → `querySelector`).

### AliExpress (`parsers/aliexpress.py`)

- Чтение DOM уже открытой вкладки `/item/<id>.html`.
- Селекторы `HazeProductPrice_SnowPrice__mainM/S`, апгрейд картинок до 960×960.
- Автоматически открывает новую вкладку, если карточки нет.

### Яндекс.Маркет (`parsers/yandex.py`)

- **SSR:** `<noframes data-apiary="patch">` → `parse_search()` / `parse_card()`.
- **CDP-фолбэк:** hard reload → ожидание `allPrices` → `outerHTML` → `parse_card()`.
- **DOM-фолбэк:** если SSR-карточки нет, цены вытягиваются JS-ом и встраиваются как `<script id="__cdp_prices__">`.
- **Реферальные ссылки:** `_cdp_get_referral_link()` — клик + перехват `clipboard`.
- **Промокоды:** `extract_promo_from_card()` (отключён, но интерфейс есть).

---

## Очереди RabbitMQ

| Очередь | Тип | Назначение |
|---------|-----|------------|
| `parser.incoming` | direct | Входящие URL |
| `parser.errors` | direct | Ошибки парсинга |
| `parser.results.storage` | fanout | Результаты → PostgreSQL |
| `parser.results.telegram` | fanout | Результаты → Telegram |
| `parser.referral.manual` | direct | Ручные Яндекс-ссылки в реферальный конвейер |

Exchange `parser.results.fanout` — публикует в обе очереди результатов одновременно.
Exchange `parser` (direct) — роутинг `incoming` / `errors` / `referral.manual`.

---

## Схема БД

### `products`
Результат парсинга: `url`, `marketplace`, `sku`, `title`, `price_rub`, `price_with_plus`, `price_original_rub`, `seller`, `brand`, `rating`, `rating_count`, `in_stock`, `image_url`, `tier_used`, `post_text`, `success`, `error`, `created_at`.

UNIQUE(`sku`, `marketplace`). При повторном парсинге — upsert + запись в `price_history`.

### `price_history`
Каждое сохранение товара → новая запись. Поля: `product_id`, `price_rub`, `price_original_rub`, `price_with_plus`, `in_stock`, `created_at`.

### `published_products`
Дедупликация Такпродама: `takprodam_id` (UNIQUE), `product_id`, `marketplace`, `title`, `tracking_link`, `published_at`.

### `published_referral_products`
Дедупликация Яндекс.рефералки: `product_id` (UNIQUE), `title`, `referral_link`, `published_at`. Автоочистка через 30 дней (`cleanup_old_referral_publications`).

### `published_media`
Фото, сохранённые для VK-клипов: `source`, `marketplace`, `product_id`, `title`, `brand`, `price_rub`, `price_with_plus`, `price_original_rub`, `rating`, `rating_count`, `legal_text`, `promo_code`, `promo_percent`, `post_text`, `image_path`, `image_url`, `video_id` (FK на `published_videos`), `created_at`.

### `published_videos`
Собранные клипы: `file_path`, `photo_count`, `duration_sec`, `vk_owner_id`, `vk_video_id`, `vk_post_id`, `status` (`created`/`uploaded`/`failed`), `error`, `created_at`.

### `published_messages`
Ссылки на опубликованные в Telegram сообщения: `product_id`, `chat_id`, `message_id`, `message_link`, `source_url`, `marketplace`, `title`, `created_at`.

Используется для вкладки «Сообщения» в дашборде.

---

## Скрипты

Каталог `scripts/` содержит утилиты для разработки и отладки:

| Файл | Назначение |
|------|-----------|
| `card_tuner.py` | GUI-тюнер карточки товара. Сверху — выбор маркетплейса (Ozon / Yandex / WB / AliExpress), слева — превью, справа — ползунки параметров. Позволяет подобрать размеры шрифтов, отступы, цвета «на глаз», затем перенести значения в `DEFAULTS` внутри `overlay_maker.py`. Запуск: `python scripts/card_tuner.py scripts/test.jpg`. |
| `card_tuner_old.py` | Предыдущая версия тюнера (для сравнения). |
| `check_admitad.py` | Проверка парсинга Admitad-ссылок. |
| `check_aliexpress.py` | Быстрая проверка AliExpress-парсера. |
| `read_result.py` | Вывод последнего результата парсинга из БД. |
| `test_card.py` | Прогон рендера карточки на тестовом изображении. |

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

# Ручная публикация конкретного URL в реферальный конвейер
docker compose exec worker python -c "
import asyncio
from broker.yandex_referral_publisher import _publish_one_url
asyncio.run(_publish_one_url('https://market.yandex.ru/card/.../1234567890'))
"

# VK Digest — собрать и загрузить клип сейчас
docker compose exec worker python -c "
import asyncio
from broker.vk_digest_publisher import _make_and_upload_one
asyncio.run(_make_and_upload_one())
"

# Получить реферальную ссылку без публикации
docker compose exec worker python -c "
from parsers.yandex import YandexParser
p = YandexParser()
print(p._cdp_get_referral_link('https://market.yandex.ru/card/.../1234567890'))
"

# Обновить трендовые хештеги VK
docker compose exec worker python -c "
import asyncio
from broker.vk_digest_publisher import _refresh_trending_hashtags
asyncio.run(_refresh_trending_hashtags())
"

# Проверить CDP
curl http://localhost:8000/api/v1/cdp-check
```

---

## Особенности эксплуатации

- **CDP-вкладки должны быть залогинены.** Без этого Яндекс не покажет `copyLinkButton`, WB не отдаст данные, AliExpress покажет x5sec.
- **Реферальная ссылка не у всех товаров.** Некоторые товары на `referral_products` не участвуют в программе — `_publish_candidate` один раз пробует, при неудаче помечает товар опубликованным (referral_link=None), чтобы не пытаться повторно.
- **Общий `_cdp_lock`** для ручного и автоматического реферального пайплайна — параллельные запросы к CDP не сломают вкладку.
- **Сессии живут ограниченно.** Токены WB кэшируются 5 минут (`_creds_ttl`).
- **TTL-кэш результатов:** `fetch_cached` кэширует успешный парсинг на 5 минут.
- **Rate-limit** на `/queue/enqueue` — 30/мин с IP (slowapi).
- **Auto-reconnect:** воркер перезапускается через 5 секунд при падении.
- **История цен:** каждое сохранение пишет в `price_history`, даже для существующих товаров (upsert по `sku + marketplace`).
- **Автороутинг Яндекса:** при `YANDEX_AUTO_REFERRAL=1` любые ссылки `market.yandex.ru/...` (кроме `/search`) из `incoming` автоматически публикуются через реферальный конвейер.
- **Дедупликация публикаций:**
  - Такпродам: `UNIQUE(published_products.takprodam_id)`.
  - Яндекс: `UNIQUE(published_referral_products.product_id)`.
- **Очистка медиа:** раз в 30 дней `cleanup_old_media()` удаляет старые фото, использованные в видео.
- **VK-видео:** `MIN_PHOTOS = 3` — если накоплено меньше, публикация откладывается до следующего цикла. Реально ждёт `VK_PHOTOS_PER_VIDEO` (по умолчанию 5).
- **VK-хештеги:** если `vk.ru/trending` недоступен (нет вкладки в CDP), используется `.env`-фолбэк.
- **VK Stories:** выключены по умолчанию (`VK_PUBLISH_STORY=0`). При включении требуют расширенных прав токена.

---

## Лицензия

Проект распространяется «как есть» для личного использования. При парсинге маркетплейсов соблюдайте их Terms of Service.