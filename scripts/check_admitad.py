"""
Проверка токена Admitad и его прав.

Запуск:
    python scripts/check_admitad.py
    python scripts/check_admitad.py <access_token>
"""
import sys
import os
from base64 import b64encode

from pathlib import Path

# Подгружаем .env из корня проекта
try:
    from dotenv import load_dotenv
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


import requests


CLIENT_ID = os.getenv("ADMITAD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("ADMITAD_CLIENT_SECRET", "")


def get_token() -> tuple[str, str]:
    """Возвращает (access_token, scope_str)."""
    if len(sys.argv) > 1:
        # Токен передан аргументом
        return sys.argv[1], ""

    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ Нет CLIENT_ID / CLIENT_SECRET.")
        print("   Задай env-переменные или передай токен аргументом:")
        print("   python scripts/check_admitad.py <access_token>")
        sys.exit(1)

    auth = b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        "https://api.admitad.com/token/",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "scope": (
                "deeplink_generator "
                "aliexpress_commission "
                "short_link "
                "advcampaigns "
                "websites"
            ),
        },
    )
    if r.status_code != 200:
        print(f"❌ Не удалось получить токен: HTTP {r.status_code}")
        print(r.text)
        sys.exit(1)

    data = r.json()
    print(f"✅ Токен получен, истекает через {data.get('expires_in', '?')} сек")
    return data["access_token"], data.get("scope", "")


def check(token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}

    # === 1. Кто я ===
    print("\n1. Информация о пользователе:")
    r = requests.get("https://api.admitad.com/me/", headers=headers)
    print(f"   HTTP {r.status_code}")
    if r.ok:
        me = r.json()
        print(f"   username: {me.get('username')}")
        print(f"   id: {me.get('id')}")
        print(f"   email: {me.get('email')}")
    else:
        print(f"   ❌ {r.text[:300]}")

    # === 2. Список площадок ===
    print("\n2. Доступные площадки (websites):")
    r = requests.get("https://api.admitad.com/websites/", headers=headers)
    print(f"   HTTP {r.status_code}")
    if r.ok:
        sites = r.json().get("results") or []
        for s in sites:
            print(f"   id={s.get('id')}  name={s.get('name')!r}  "
                  f"status={s.get('status')}")
    else:
        print(f"   ❌ {r.text[:300]}")

    # === 3. Партнёрские программы ===
    print("\n3. Ваши партнёрские программы:")
    r = requests.get(
        "https://api.admitad.com/advcampaigns/",
        headers=headers,
        params={"limit": 100},
    )
    print(f"   HTTP {r.status_code}")
    if r.ok:
        camps = r.json().get("results") or []
        for c in camps:
            name = c.get("name", "")
            cid = c.get("id")
            status = c.get("status")
            site = c.get("site")
            if "aliexpress" in name.lower() or cid == 6115:
                print(f"   ⭐ id={cid}  name={name!r}  "
                      f"status={status}  site={site}")
            else:
                print(f"      id={cid}  name={name!r}  status={status}")
    else:
        print(f"   ❌ {r.text[:300]}")

    # === 4. Deeplink generator — тест ===
    print("\n4. Проверка deeplink_generator (тестовый товар):")
    # Нужны w_id и c_id. w_id — id площадки из шага 2, c_id — 6115.
    # Берём первую площадку.
    w_id = None
    if r.ok and camps:
        # сайт из любой кампании
        for c in camps:
            if c.get("site"):
                w_id = c.get("site")
                break

    if not w_id:
        r2 = requests.get("https://api.admitad.com/websites/", headers=headers)
        if r2.ok:
            sites = r2.json().get("results") or []
            if sites:
                w_id = sites[0].get("id")

    w_id = 3006033  # твоя площадка
    c_id = 6115  # AliExpress WW
    print(f"   w_id={w_id}, c_id={c_id}")
    r = requests.get(
        f"https://api.admitad.com/deeplink/{w_id}/advcampaign/{c_id}/",
            headers=headers,
            params={
                "ulp": "https://aliexpress.ru/item/1005008751191540.html"
                       "?sku_id=12000046517138034",
            },
        )
    print(f"   HTTP {r.status_code}")
    if r.ok:
        print(f"   ✅ {r.text[:400]}")
    else:
        print(f"   ❌ {r.text[:400]}")

    # === 5. Aliexpress commission rates ===
    print("\n5. Проверка aliexpress_commission:")
    r = requests.post(
        "https://api.admitad.com/ru/aliexpress/commission_rates/",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "urls": [
                "https://aliexpress.ru/item/1005008751191540.html",
            ],
        },
    )
    print(f"   HTTP {r.status_code}")
    if r.ok:
        print(f"   ✅ {r.text[:400]}")
    else:
        print(f"   ❌ {r.text[:400]}")

    # === 6. Short link ===
    print("\n6. Проверка short_link (сокращение):")
    # Возьмём любую уже сгенерированную ссылку, если была в шаге 4
    sample = None
    if "r" in dir() and r.ok:
        try:
            data = r.json()
            if isinstance(data, list) and data:
                sample = data[0].get("link")
            elif isinstance(data, dict):
                sample = data.get("link")
        except Exception:
            pass

    if sample:
        r = requests.post(
            "https://api.admitad.com/shortlink/modify/",
            headers=headers,
            data={"link": sample},
        )
        print(f"   HTTP {r.status_code}")
        if r.ok:
            print(f"   ✅ {r.text[:300]}")
        else:
            print(f"   ❌ {r.text[:300]}")
    else:
        print("   ⚠️ Нет ссылки для сокращения — пропускаю")

    print("\n" + "=" * 60)
    print("ГОТОВО. Если выше есть ❌ — это разрешения, которых не хватает.")


if __name__ == "__main__":
    tok, scope = get_token()
    if scope:
        print(f"Scope: {scope}")
    check(tok)