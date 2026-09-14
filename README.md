# Marketplace Parser API

FastAPI-сервис для парсинга товаров с российских и китайских маркетплейсов.

**Поддерживается 5 маркетплейсов:**

| Маркетплейс | Метод | Что доступно |
|-------------|-------|--------------|
| 🟦 **Ozon** | Chrome CDP + composer-api.bx | Поиск, карточки, цены, рейтинги |
| 🟪 **Wildberries** | Chrome CDP + card.wb.ru v4 | Карточки, цены, рейтинги |
| 🟧 **AliExpress** | Chrome CDP + чтение DOM | Карточки, цены, рейтинги |
| 🟨 **Яндекс Маркет** | SSR (widget state) | Карточки, цены |
| 🟨 **Яндекс Маркет поиск** | SSR + fallback на карточку | Первый товар из выдачи |

Сервис возвращает цены (обычная + подписочная + зачёркнутая), рейтинги, продавцов, бренды, картинки и **готовый пост для Telegram** — в одном JSON-ответе.

---

## 📋 Требования

- **Python 3.12+**
- **Google Chrome** или **Microsoft Edge** (для CDP)
- **Windows / Linux / macOS**

---

## ⚡ Быстрый старт

### 1. Установка зависимостей

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
pip install websocket-client
```

### 2. Запуск Chrome с CDP

Chrome с CDP нужен для обхода Cloudflare на Ozon, Wildberries и AliExpress.

**Windows** — создайте `start-chrome-cdp.bat`:

```batch
@echo off
set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if not exist "C:\chrome-scraping" mkdir C:\chrome-scraping

start "" "%CHROME%" ^
    --remote-debugging-port=9222 ^
    --remote-allow-origins=* ^
    --user-data-dir=C:\chrome-scraping ^
    --no-first-run ^
    --no-default-browser-check

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
| `--remote-allow-origins=*` | **Обязательно** — без него WebSocket вернёт 403 |
| `--user-data-dir=C:\chrome-scraping` | Изолированный профиль (не трогает личный Chrome) |
| `--no-first-run` | Пропустить приветствие |

### 3. Прогреть сессии в Chrome

В **открывшемся** Chrome (отдельный профиль, не ваш личный) откройте по очереди:

```
https://www.ozon.ru/          — подождать 5–10 сек
https://www.wildberries.ru/   — подождать 5–10 сек
https://aliexpress.ru/        — пройти x5sec, если появится
```

Если увидите капчу — **пройдите вручную**. Chrome **не закрывайте** — он нужен для работы парсера.

### 4. Запуск сервера

```bash
python main.py
```

Открыть: http://localhost:8000/docs

---

## 📡 API Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/` | Информация о сервисе |
| `GET` | `/api/v1/health` | Проверка работоспособности |
| `GET` | `/api/v1/cdp-check` | Проверка доступности Chrome CDP |
| `POST` | `/api/v1/parse` | Спарсить товар по URL |

---

## 🧪 Примеры запросов

### Ozon

```bash
curl -X POST http://localhost:8000/api/v1/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.ozon.ru/product/142701653/"}'
```

### Wildberries

```bash
curl -X POST http://localhost:8000/api/v1/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.wildberries.ru/catalog/219498821/detail.aspx"}'
```

### AliExpress

```bash
curl -X POST http://localhost:8000/api/v1/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://aliexpress.ru/item/1005008955014882.html"}'
```

### Яндекс карточка

```bash
curl -X POST http://localhost:8000/api/v1/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://market.yandex.ru/product/6113276549"}'
```

### Яндекс поиск

```bash
curl -X POST http://localhost:8000/api/v1/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://market.yandex.ru/search?text=наполнитель+кошачий"}'
```

### Windows CMD

В CMD JSON передаётся через `\"`:

```cmd
curl -X POST http://localhost:8000/api/v1/parse -H "Content-Type: application/json" -d "{\"url\": \"https://www.ozon.ru/product/142701653/\"}"
```

Если URL содержит `&` — экранируйте через `^&`:

```cmd
-d "{\"url\": \"https://www.ozon.ru/product/123/?at=xxx^&sh=yyy\"}"
```

Или используйте файл:

```cmd
echo {"url": "https://..."} > request.json
curl -X POST http://localhost:8000/api/v1/parse -H "Content-Type: application/json" -d @request.json
```

---

## 📤 Формат ответа

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

### Поля ответа

| Поле | Описание |
|------|----------|
| `success` | `true` если товар получен, `false` при ошибке |
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
| `tier_used` | Как получены данные: `cdp` / `cdp_dom` / `ssr` / `ssr_card` |
| `post_text` | Готовый пост для Telegram |
| `error` | Текст ошибки при `success: false` |

---

## 🛠️ Архитектура

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
- Chrome должен быть запущен с `--remote-allow-origins=*`.
- Вкладка маркетплейса должна быть **прогрета** (открыта вручную).
- Нельзя закрывать Chrome во время работы парсера.

---

## 📁 Структура проекта

```
marketplace_parser/
├── main.py                      # Точка входа FastAPI
├── requirements.txt
├── requirements-test.txt
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
│   └── parse.py                 # POST /parse, GET /cdp-check
├── config/
│   ├── __init__.py
│   └── settings.py
└── tests/
    ├── conftest.py              # Pytest fixtures
    ├── test_models.py           # Unit: модели
    ├── test_formatters.py       # Unit: форматтеры
    ├── test_parsers.py          # Unit: extract_sku
    ├── test_api.py              # Unit: FastAPI
    ├── test_integration.py      # Integration: реальные запросы
    └── run_all.bat              # Запуск всех тестов
```

---

## ⚙️ Конфигурация

Создайте `.env` в корне:

```bash
# Chrome CDP
CDP_HOST=127.0.0.1
CDP_PORT=9222

# Таймауты (секунды)
YANDEX_TIMEOUT=40
OZON_TIMEOUT=30
WB_TIMEOUT=15
ALI_TIMEOUT=30

# Паузы между запросами (rate limiting)
YANDEX_MIN_GAP=1.5
OZON_MIN_GAP=2.5
WB_MIN_GAP=1.5
ALI_MIN_GAP=3.0

# Прокси (опционально)
# OZON_PROXY=http://user:pass@residential-proxy:port
# YANDEX_PROXY=http://user:pass@residential-proxy:port
# WB_PROXY=http://user:pass@residential-proxy:port
# ALI_PROXY=http://user:pass@residential-proxy:port
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

**Шаг 3.** Запустить сервер:

```cmd
python main.py
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

Прогоняет по одному запросу к каждому маркетплейсу и проверяет `"success": true`.

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
- Запустить Chrome с `--remote-debugging-port=9222`.
- Прогреть `ozon.ru` в этом Chrome.

---

## 🐛 Отладка

### Проверить, что CDP работает

```bash
curl http://127.0.0.1:9222/json/version
```

Должен вернуться JSON с `Browser` и `webSocketDebuggerUrl`.

### Проверить список вкладок

```bash
curl http://127.0.0.1:9222/json/list
```

Найдите вкладки с URL маркетплейсов.

### Проверить через эндпоинт

```bash
curl http://localhost:8000/api/v1/cdp-check
```

**Ожидаемый ответ:**

```json
{
  "available": true,
  "browser": "Chrome/153.0.8010.37",
  "ws_url": "ws://127.0.0.1:9222/devtools/browser/..."
}
```

### Логи

Сервер пишет логи в консоль (уровень `INFO`). При запросе к маркетплейсам видны строки:

```
ozon.cdp | INFO | Ищу вкладку ozon.ru через /json/list...
ozon.cdp | INFO | Использую вкладку: id=73A74633...
ozon.cdp | INFO | WebSocket подключён
ozon.cdp | INFO | fetch вернул: status=200, body_len=151997
```

---

## 🔧 Устранение неполадок

### `Handshake status 403` при подключении к WebSocket

**Причина:** Chrome запущен без `--remote-allow-origins=*`.

**Решение:**

```cmd
taskkill /F /IM chrome.exe
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
    --remote-debugging-port=9222 ^
    --remote-allow-origins=* ^
    --user-data-dir=C:\chrome-scraping
```

### `HTTP 403 — Ozon блокирует IP`

**Причина:** Chrome CDP не запущен или вкладка `ozon.ru` не открыта.

**Решение:**
1. Запустить Chrome с CDP.
2. Открыть `https://www.ozon.ru/` в этом Chrome.
3. Подождать 10 секунд.
4. Повторить запрос.

### `CDP fetch failed — не удалось выполнить запрос через Chrome`

**Причина:** `websocket-client` не установлен или вкладка не найдена.

**Решение:**

```bash
pip install websocket-client
```

Проверить вкладки:

```bash
curl http://127.0.0.1:9222/json/list
```

### `WebSocket recv timeout`

**Причина:** страница в Chrome долго грузится или зависла.

**Решение:**
1. Открыть вкладку маркетплейса вручную.
2. Обновить (F5).
3. Подождать 10 секунд.
4. Повторить запрос.

### `empty_product_shell` от Яндекса

**Причина:** Яндекс отдаёт пустой каркас страницы (degraded serving).

**Решение:** использовать карточку вместо поиска, или добавить резидентный прокси.

### `Internal Server Error`

**Причина:** ошибка в Python-коде. Смотрите traceback в консоли `python main.py`.

**Частые причины:**
- Не установлен `websocket-client`.
- Отступ в Python-коде неверный.
- В `services/marketplace.py` передаются параметры, которых нет в `__init__` парсера.

---

## 📊 Сравнение с другими решениями

| Решение | Плюсы | Минусы |
|---------|-------|--------|
| **Этот проект** | 5 маркетплейсов, без Selenium, готовый `post_text` | Нужен Chrome CDP для Ozon/WB/AliExpress |
| **Selenium** | Универсально | Медленно, хрупко, нужен WebDriver |
| **Playwright** | Мощно | Тяжёлые зависимости, `playwright install` |
| **scrapy** | Асинхронно | Не работает с JS-сайтами |
| **Apify / готовые API** | Не надо поддерживать | Платно, зависимость от вендора |

---

## 🎁 Бонус: сохранение результатов

Сохранить ответ в файл (чтобы увидеть эмодзи без проблем с CMD):

```bash
curl -s -X POST http://localhost:8000/api/v1/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.ozon.ru/product/142701653/"}' \
  > result.json
```

Открыть в VS Code — увидите правильные 🔥 и 🙈.

---

## 📌 Roadmap

Возможные улучшения:

- [ ] **`/api/v1/compare`** — сравнение цен по всем маркетплейсам одним запросом
- [ ] **`/api/v1/parse-batch`** — пакетная обработка списка URL
- [ ] **Отзывы** — Ozon, WB, Yandex
- [ ] **Dockerfile + docker-compose** — для деплоя
- [ ] **TTL-кэш** — для повторных запросов
- [ ] **Rate limiting** — защита API
- [ ] **GitHub Actions** — CI для тестов

---

## 🤝 Вклад

Проект использует идеи и подходы из открытых источников:

- **ru-marketplace-mcp** ([github.com/Vladimir-Human](https://github.com/Vladimir-Human/ru-marketplace-mcp)) — архитектура SSR-парсинга Yandex и composer-api Ozon
- **Chrome DevTools Protocol** — [chromedevtools.github.io/devtools-protocol](https://chromedevtools.github.io/devtools-protocol/)
- **FastAPI** — [fastapi.tiangolo.com](https://fastapi.tiangolo.com/)

---

## 📄 Лицензия

MIT

---

## ⚖️ Дисклеймер

Проект предназначен для **личного использования и исследований**. Условия маркетплейсов, как правило, запрещают неофициальный парсинг.

Используйте на своё усмотрение, соблюдайте вежливый темп запросов (паузы между вызовами — часть конструкции, не убирайте их ради скорости). Данные не предназначены для перепродажи или массового сбора.