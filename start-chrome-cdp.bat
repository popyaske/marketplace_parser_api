@echo off
echo Starting Chrome with CDP on port 9222...

set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" (
    echo ERROR: Chrome not found.
    exit /b 1
)

if not exist "C:\Users\simon\Desktop\channel_promo\chrome-scraping" mkdir C:\Users\simon\Desktop\channel_promo\chrome-scraping

start "" "%CHROME%" ^
    --remote-debugging-port=9222 ^
    --remote-debugging-address=0.0.0.0 ^
    --remote-allow-origins=* ^
    --user-data-dir=C:\Users\simon\Desktop\channel_promo\chrome-scraping ^
    --disable-web-security ^
    --no-first-run ^
    --no-default-browser-check

echo Waiting for CDP...
timeout /t 5 /nobreak >nul

curl -s http://127.0.0.1:9222/json/version
echo.
echo Chrome CDP is ready on port 9222.
pause