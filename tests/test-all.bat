@echo off
chcp 65001 >nul
setlocal

echo ==================================================
echo   ТЕСТ ВСЕХ МАРКЕТПЛЕЙСОВ
echo ==================================================
echo.

echo [1/5] Очищаю таблицу products...
docker compose exec -T postgres psql -U parser -d parser -c "TRUNCATE products, price_history RESTART IDENTITY;" >nul
echo     OK
echo.

echo [2/5] Отправляю URL на парсинг...
echo.

echo === Wildberries ===
curl.exe -s -X POST http://localhost:8000/api/v1/queue/enqueue -H "Content-Type: application/json" -d "{\"url\": \"https://www.wildberries.ru/catalog/1175879469/detail.aspx\"}"
echo.
timeout /t 2 /nobreak >nul

echo === Ozon ===
curl.exe -s -X POST http://localhost:8000/api/v1/queue/enqueue -H "Content-Type: application/json" -d "{\"url\": \"https://www.ozon.ru/product/kedy-t-taccardi-klassicheskie-demisezonnye-2390287667/\"}"
echo.
timeout /t 2 /nobreak >nul

echo === Яндекс.Маркет ===
curl.exe -s -X POST http://localhost:8000/api/v1/queue/enqueue -H "Content-Type: application/json" -d "{\"url\": \"https://market.yandex.ru/card/dzhemper-omwt00009zo02-p6-zelenyy-l/6030405494\"}"
echo.
timeout /t 2 /nobreak >nul

echo === AliExpress ===
curl.exe -s -X POST http://localhost:8000/api/v1/queue/enqueue -H "Content-Type: application/json" -d "{\"url\": \"https://aliexpress.ru/item/1005009012370705.html\"}"
echo.
echo.

echo [3/5] Жду 60 секунд, пока воркер обработает очередь...
timeout /t 60 /nobreak >nul
echo.

echo [4/5] РЕЗУЛЬТАТЫ В БАЗЕ ДАННЫХ:
echo --------------------------------------------------
docker compose exec -T postgres psql -U parser -d parser -c "SELECT id, marketplace, LEFT(title, 45) AS title, price_rub, in_stock, success, tier_used, LEFT(error, 60) AS error FROM products ORDER BY id;"
echo.

echo [5/5] СОСТОЯНИЕ ОЧЕРЕДЕЙ RABBITMQ:
echo --------------------------------------------------
docker compose exec -T rabbitmq rabbitmqctl list_queues name messages
echo.

echo ==================================================
echo   ГОТОВО
echo ==================================================
pause