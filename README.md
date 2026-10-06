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
- [Разработка и тесты](#разработка-и-тесты)
- [Особенности эксплуатации](#особенности-эксплуатации)

---

## Возможности

- 🔍 **Парсинг 4 маркетплейсов**: Ozon, Wildberries, AliExpress, Яндекс.Маркет
- 🐇 **Асинхронная обработка** через RabbitMQ (`incoming` / `results.storage` / `results.telegram` / `errors`)
- 🗄 **PostgreSQL** + история цен (`PriceHistory`)
- 📢 **Три Telegram-канала публикации**:
  - результаты парсинга URL из очереди (`telegram_consumer`);
  - ежедневная публикация 1 товара из каталога Такпродам (`daily_publisher`);
  - публикация каждые 10 минут из реферальной программы Яндекс.Маркета (`yandex_referral_publisher`).
- 🎬 **VK Digest** — раз в сутки собирает 5 последних фото из Telegram-публикаций в вертикальный клип 9:16 с музыкой и трендовыми хештегами VK, публикует в личный профиль.
- 🎨 **Стилизация карточек** товаров через Pillow (`overlay_maker`): «НИЖЕ РЫНКА», цена с иконкой Пэй, бренд, юр. текст.
- 🔗 **Автополучение реферальной ссылки** Яндекс.Маркета через CDP — клик «Поделиться» → перехват `clipboard.writeText` → ссылка `/cc/xxxxx`.
- 🎵 **Трендовые хештеги VK** — скрапинг `vk.ru/trending` через CDP с обновлением раз в 6 часов.
- 🚫 **Защита от дублей**: `published_products`, `published_referral_products`.
- 📊 **React-дашборд**: фильтры, поиск, статистика, SSE-обновления, превью постов.
- 🌐 **REST API** на FastAPI: парсинг, очередь, результаты, сравнение цен, экспорт CSV.
- 🧩 **Chrome CDP** для обхода антибот-защиты (x5sec, SmartCaptcha, реферальные попапы).
- 🎟 **Поддержка промокодов** в постах (поле `promo_code` / `promo_percent`).

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
        ┌────────────────────────┬───────────────┼──────────────────────────┐
        │                        │               │                          │
        ▼                        ▼               ▼                          ▼
 parser.results.storage   parser.results.telegram     parser.errors     VK / media
        │                        │                                        │
        ▼                        ▼                                        ▼
 ┌─────────────┐          ┌──────────────┐                        ┌────────────────┐
 │ PostgreSQL  │          │  Telegram    │                        │  media/photos  │
 │  products   │          │   (channel)  │                        │  media/videos  │
 │  media      │          │   (group)    │                        │  media/music   │
 │  videos     │          └──────────────┘                        └────────┬───────┘
 └──────┬──────┘                  ▲                                       │
        │                         │                                       ▼
        │                  daily_publisher                       vk_digest_publisher
        │                  yandex_referral_publisher                    │
        │                         │                                       ▼
 ┌──────┴───────┐          ┌──────┴───────┐                       ┌──────────────┐
 │  frontend    │          │ Такпродам    │                       │  VK API      │
 │  (React)     │          │ Яндекс CDP   │                       │  video.save  │
 └──────────────┘          └──────────────┘                       └──────────────┘
```

**Процессы:**
- **parser** — FastAPI, принимает URL, публикует в `parser.incoming`.
- **worker** — 4 консьюмера + 3 планировщика:
  - `consumer` — `incoming` → parse → fanout;
  - `storage_consumer` — `results.storage` → PostgreSQL;
  - `telegram_consumer` — `results.telegram` → Telegram + сохранение фото;
  - `daily_publisher` — раз в сутки Такпродам → Telegram (в `worker.py` **закомментирован**);
  - `yandex_referral_publisher` — каждые 10 минут Яндекс.рефералка → Telegram;
  - `vk_digest_publisher` — раз в сутки сборка видео из накопленных фото → VK;
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
| VK | VK API 5.199 (`video.save`, `wall.post`) |
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
│   ├── connection.py              # Broker (aio-pika) + очереди
│   ├── producer.py                # send_url / send_urls
│   ├── consumer.py                # incoming → parse → results
│   ├── storage_consumer.py        # results → PostgreSQL
│   ├── telegram_consumer.py       # results → Telegram + save_photo
│   ├── daily_publisher.py         # Такпродам → Telegram (раз в сутки)
│   ├── yandex_referral_publisher.py  # Яндекс.Маркет → Telegram (каждые 10 мин)
│   └── vk_digest_publisher.py     # Фото → видео 9:16 → VK (раз в сутки)
│
├── parsers/
│   ├── base.py                    # BaseParser + TTLCache
│   ├── ozon.py                    # Ozon (composer-api + CDP + DOM)
│   ├── wildberries.py             # WB (__internal v4 + CDP/Playwright)
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

### worker — Telegram

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен бота |
| `TELEGRAM_CHANNEL_ID` | ID канала для обычных результатов |
| `TELEGRAM_GROUP_ID` | ID группы для daily_publisher и yandex_referral (fallback = CHANNEL_ID) |
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

| Переменная | Описание |
|------------|----------|
| `YANDEX_REFERRAL_INTERVAL` | Интервал публикации рефералки в секундах (по умолчанию `600`) |
| `YANDEX_REFERRAL_PAGES` | Сколько страниц рефералки обходить (по умолчанию `3`) |

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
YANDEX_REFERRAL_INTERVAL=600
YANDEX_REFERRAL_PAGES=3

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

В проекте работают **три Telegram-канала** публикации и **один VK-канал**.

### 1. Обычный парсинг (`telegram_consumer.py`)

- **Триггер:** URL в очереди `parser.incoming`.
- **Логика:** парсинг → результат в fanout → storage + telegram.
- **Фильтры:** `success`, `price_rub`/`price_with_plus`, `in_stock`.
- **Целевой чат:** `TELEGRAM_CHANNEL_ID`.
- **Бонус:** каждое фото сохраняется в `media/photos/` для будущего VK-клипа.

### 2. Такпродам (`daily_publisher.py`)

- **Триггер:** раз в сутки в `PUBLISH_HOUR:00` (МСК).
- **Источник:** `GET /v2/publisher/product/?source_id=...` из API Такпродам.
- **Логика:** фильтр по `published_products` → случайный товар → публикация → отметка в БД.
- **Текст:** `TextGenerator.generate()` + `legal_text` из ответа.
- **Целевой чат:** `TELEGRAM_GROUP_ID`.
- **Статус:** в `worker.py` **закомментирован** (раскомментируйте `run_daily_publisher()` при необходимости).

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
  7. `_send_to_telegram()` → пост с реферальной ссылкой и **стилизованной карточкой** (`render_product_card`).
  8. `mark_referral_published()` → защита от повторной публикации.
- **Целевой чат:** `TELEGRAM_GROUP_ID`.

**Особенности текста поста:** цена Яндекс Пэй показывается как основная, обычная (или зачёркнутая) — как «Было».

---

## VK Digest (видео)

Планировщик `vk_digest_publisher.py` раз в `VK_DIGEST_INTERVAL` (по умолчанию сутки):

1. **Обновляет трендовые хештеги** раз в `VK_TRENDING_REFRESH_SEC` (6 ч) через CDP:
   - Находит iframe `vklanding.com` внутри `vk.ru/trending`.
   - Извлекает `#хештеги` из ссылок `/hashtag/`.
   - Фолбэк — `VK_HASHTAGS` из `.env`.
2. **Берёт 5 последних неиспользованных фото** из `published_media` (у которых `video_id IS NULL`).
3. **Стилизует каждое фото** через `overlay_maker.render_and_save()`:
   - формат `9:16` (1080×1920);
   - бейдж «НИЖЕ РЫНКА» (если есть зачёркнутая цена);
   - бренд, зелёная цена + иконка Пэй;
   - юр. текст.
4. **Собирает слайдшоу** через `ffmpeg` (`services/video_maker.py`):
   - эффект Ken Burns (медленный зум);
   - переходы `xfade` между кадрами;
   - случайный mp3/m4a из `media/music/`;
   - длительность ≈ 5 × 3 сек = 15 сек.
5. **Публикует в VK** (`VKClient.upload_video`):
   - `video.save` → получает `upload_url`;
   - POST файла;
   - `wallpost=1` → автоматический пост на стене;
   - текст поста — 3 случайных хештега + ссылка на Telegram.
6. **Удаляет исходные фото** с диска и записи `published_media` после успешной публикации.
7. **Записывает видео** в `published_videos`.

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

### Wildberries (`parsers/wildberries.py`)

- **Tier 1 (CDP):** `Bearer`-токен + `deviceid` + `xinfo` из localStorage → `fetch` на `__internal/card/cards/v4/detail`.
- **Tier 2 (Playwright):** `page.request.get` с теми же заголовками.
- **Кэш credentials:** 5 минут (`_creds_ttl`).
- **Изображение:** regex по HTML карточки → DOM fallback.

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

Exchange `parser.results.fanout` — публикует в обе очереди одновременно.

---

## Схема БД

### `products`
Результат парсинга: `url`, `marketplace`, `title`, `price_rub`, `price_with_plus`, `price_original_rub`, `seller`, `brand`, `rating`, `in_stock`, `image_url`, `tier_used`, `post_text`, `success`, `error`, `created_at`.

UNIQUE(`url`, `marketplace`). При повторном парсинге — upsert.

### `price_history`
Каждое сохранение товара → новая запись. Поля: `product_id`, `price_rub`, `price_original_rub`, `price_with_plus`, `in_stock`, `created_at`.

### `published_products`
Дедупликация Такпродама: `takprodam_id` (UNIQUE), `product_id`, `marketplace`, `title`, `tracking_link`, `published_at`.

### `published_referral_products`
Дедупликация Яндекс.рефералки: `product_id` (UNIQUE), `title`, `referral_link`, `published_at`. Автоочистка через 7 дней (`cleanup_old_referral_publications`).

### `published_media`
Фото, сохранённые для VK-клипов: `source`, `product_id`, `title`, `brand`, `price_rub`, `price_with_plus`, `price_original_rub`, `rating`, `rating_count`, `legal_text`, `promo_code`, `promo_percent`, `post_text`, `image_path`, `image_url`, `video_id` (FK на `published_videos`), `created_at`.

### `published_videos`
Собранные клипы: `file_path`, `photo_count`, `duration_sec`, `vk_owner_id`, `vk_video_id`, `vk_post_id`, `status` (`created`/`uploaded`/`failed`), `error`, `created_at`.

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
- **Очистка медиа:** раз в 30 дней `cleanup_old_media()` удаляет старые фото, использованные в видео.
- **VK-видео:** `MIN_PHOTOS = 3` — если накоплено меньше, публикация откладывается до следующего цикла.
- **VK-хештеги:** если `vk.ru/trending` недоступен (нет вкладки в CDP), используется `.env`-фолбэк.

---

## Лицензия

Проект распространяется «как есть» для личного использования. При парсинге маркетплейсов соблюдайте их Terms of Service.