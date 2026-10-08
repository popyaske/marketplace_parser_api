@echo off
echo Starting Chrome with CDP on port 9222...

set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" (
    echo ERROR: Chrome not found.
    exit /b 1
)

set PROFILE_DIR=C:\Users\simon\Desktop\channel_promo\chrome-scraping
if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%"

start "" "%CHROME%" ^
    --remote-debugging-port=9222 ^
    --remote-debugging-address=0.0.0.0 ^
    --remote-allow-origins=* ^
    --user-data-dir="%PROFILE_DIR%" ^
    --no-first-run ^
    --no-default-browser-check

echo Waiting for CDP...
timeout /t 5 /nobreak >nul

curl -s http://127.0.0.1:9222/json/version
echo.

REM === Открываем 5 вкладок через тот же профиль ===
echo Opening marketplace tabs...

start "" "%CHROME%" --user-data-dir="%PROFILE_DIR%" "https://www.ozon.ru/"
timeout /t 2 /nobreak >nul

start "" "%CHROME%" --user-data-dir="%PROFILE_DIR%" "https://market.yandex.ru/"
timeout /t 2 /nobreak >nul

start "" "%CHROME%" --user-data-dir="%PROFILE_DIR%" "https://www.wildberries.ru/"
timeout /t 2 /nobreak >nul

start "" "%CHROME%" --user-data-dir="%PROFILE_DIR%" "https://aliexpress.ru/"
timeout /t 2 /nobreak >nul

start "" "%CHROME%" --user-data-dir="%PROFILE_DIR%" "https://vk.ru/trending"
timeout /t 3 /nobreak >nul

echo.
echo ====================================================
echo Chrome CDP is ready on port 9222.
echo Opened tabs:
echo   1. Ozon        - https://www.ozon.ru/
echo   2. Yandex      - https://market.yandex.ru/
echo   3. Wildberries - https://www.wildberries.ru/
echo   4. AliExpress  - https://aliexpress.ru/
echo   5. VK Trending - https://vk.ru/trending
echo ====================================================
echo.
echo ВАЖНО: войдите во все 5 сервисов и пройдите капчи вручную.
echo Оставьте вкладки ОТКРЫТЫМИ — воркер использует их.
echo.
pause