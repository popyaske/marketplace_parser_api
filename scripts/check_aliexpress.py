"""Проверка аффилиатности конкретных товаров AliExpress."""
import os
from base64 import b64encode
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

CLIENT_ID = os.getenv("ADMITAD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("ADMITAD_CLIENT_SECRET", "")

# === Настройки ===
W_ID = 3006033              # ваша площадка
C_ID = 6115                 # WW (пока)
TEST_URLS = [
    # ссылки на товары, которые будете проверять
    "https://aliexpress.ru/item/1005008751191540.html?sku_id=12000046517138034",
    "https://aliexpress.ru/item/1005008543893186.html",
    "https://aliexpress.ru/item/32961877911.html",
]


# 1. Токен
auth = b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
r = requests.post(
    "https://api.admitad.com/token/",
    headers={"Authorization": f"Basic {auth}"},
    data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "scope": "deeplink_generator aliexpress_commission short_link",
    },
)
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 2. Генерим партнёрские ссылки
print(f"Генерирую ссылки для {len(TEST_URLS)} товаров (w_id={W_ID}, c_id={C_ID})...")
r = requests.get(
    f"https://api.admitad.com/deeplink/{W_ID}/advcampaign/{C_ID}/",
    headers=headers,
    params=[("ulp", url) for url in TEST_URLS],
)
if r.status_code != 200:
    print(f"❌ deeplink: {r.status_code} — {r.text}")
    exit(1)

links = r.json()
print(f"Получено ссылок: {len(links)}\n")

# 3. Показываем результат
for i, item in enumerate(links):
    src = TEST_URLS[i] if i < len(TEST_URLS) else "?"
    print(f"─ Товар {i+1}: {src[:70]}...")
    print(f"  Ссылка:  {item.get('link', '')[:80]}...")
    print(f"  Аффилиат: {item.get('is_affiliate_product')}")
    if item.get("error_message"):
        print(f"  Ошибка: {item.get('error_message')}")
    print()

# 4. Проверка комиссии
print("Проверяю комиссию...")
r = requests.post(
    "https://api.admitad.com/ru/aliexpress/commission_rates/",
    headers={**headers, "Content-Type": "application/json"},
    json={"urls": TEST_URLS},
)
if r.ok:
    for item in r.json().get("commission_rates", []):
        print(f"  {item.get('product_name', '?')[:50]}")
        print(f"    обычная: {item.get('commission_rate')}%")
        print(f"    hot:     {item.get('hot_commission_rate')}%")
        print(f"    is_hot:  {item.get('is_hot')}")