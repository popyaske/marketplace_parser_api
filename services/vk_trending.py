"""
Скрапер трендовых хештегов VK для Клипов.
Источник: https://vk.ru/trending

ВАЖНО: реальный контент живёт в IFRAME:
  https://clip_trends.production.vklanding.com/?...
Родительская страница vk.ru/trending — только обёртка с пустым body.
Поэтому работаем с iframe как с отдельным target через CDP.

Стратегия:
  1. Находим iframe с vklanding.com в /json/list.
  2. Подключаемся к его webSocketDebuggerUrl.
  3. Никакой навигации не делаем — iframe уже на нужной странице.
  4. Ждём появления ссылок /hashtag/ (React рендерит их асинхронно).
  5. Извлекаем хештеги.
  6. Для стр.2 — клик по кнопке пагинации внутри iframe.
"""
import json
import logging
import time
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

VK_TRENDING_URL = "https://vk.ru/trending"
VKLANDING_DOMAIN = "vklanding.com"


# === JS-скрипты ===

_JS_EXTRACT = r"""
(() => {
    const out = new Set();
    document.querySelectorAll('a[href*="/hashtag/"]').forEach(a => {
        const h = a.getAttribute('href') || '';
        const m = h.match(/hashtag\/([^\/?#]+)/);
        if (!m) return;
        try {
            const tag = decodeURIComponent(m[1]).trim();
            if (tag && tag.length <= 50) out.add('#' + tag);
        } catch (e) {}
    });
    document.querySelectorAll('span, div').forEach(el => {
        if (el.children.length > 0) return;
        const t = (el.textContent || '').trim();
        if (t.startsWith('#') && t.length > 1 && t.length <= 50 && !t.includes(' ')) {
            out.add(t);
        }
    });
    return JSON.stringify([...out]);
})()
"""

_JS_DIAG = r"""
(() => {
    return JSON.stringify({
        href: location.href,
        title: document.title,
        ready: document.readyState,
        body_len: document.body ? document.body.innerText.length : -1,
        links_total: document.querySelectorAll('a').length,
        hashtag_slash: document.querySelectorAll('a[href*="/hashtag/"]').length,
        data_pages: [...document.querySelectorAll('[data-page]')]
                        .map(e => e.getAttribute('data-page')),
        has_login: !!document.querySelector('input[type="password"]'),
        body_preview: document.body ? document.body.innerText.slice(0, 300) : null
    });
})()
"""


# Извлекает трендовые треки из блока "музыка в тренде" внутри iframe.
# Возвращает [{artist, title, href, audio_id}].
_JS_EXTRACT_MUSIC = r"""
(() => {
    const items = [];
    const seen = new Set();

    // Для каждой ссылки /clips/music/<owner>_<audio> находим контейнер с метаданными
    document.querySelectorAll('a[href*="/clips/music/"]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/clips\/music\/(-?\d+_\d+)/);
        if (!m) return;
        const audio_id = m[1];
        if (seen.has(audio_id)) return;
        seen.add(audio_id);

        // Идём вверх по DOM, ищем контейнер, в котором есть текст артиста и названия
        let container = a.closest('[class*="Music"], [class*="Track"], li, article, div');
        // Пробуем подняться ещё раз, если первый контейнер — просто обёртка ссылки
        for (let i = 0; i < 5 && container; i++) {
            const texts = [...container.querySelectorAll('span, div, a')]
                .filter(el => el.children.length === 0)
                .map(el => (el.textContent || '').trim())
                .filter(t => t && t.length > 0 && t.length < 100 &&
                             !t.includes('смотреть') && !t.includes('клип'));
            if (texts.length >= 1) {
                // Обычно: [title] или [artist, title] — берём первые два
                return texts;
            }
            container = container.parentElement;
        }

        // Fallback — просто сохраним ID
        items.push({ audio_id, artist: '', title: '', href, raw: '' });
    });

    // Пересобираем результат с метаданными (второй проход)
    const result = [];
    document.querySelectorAll('a[href*="/clips/music/"]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/clips\/music\/(-?\d+_\d+)/);
        if (!m) return;
        const audio_id = m[1];

        let container = a.closest('div, li, article');
        for (let i = 0; i < 6 && container; i++) {
            const leaves = [...container.querySelectorAll('span, div, a')]
                .filter(el => el.children.length === 0)
                .map(el => (el.textContent || '').trim())
                .filter(t => t && t.length > 1 && t.length < 100 &&
                             !/^(смотреть|клипы|музыка|в тренде|страниц)/i.test(t));

            // Отсеиваем служебные
            const clean = leaves.filter(t =>
                !t.includes('Перейти') &&
                !t.includes('следующ') &&
                !t.includes('назад') &&
                !/^\d+$/.test(t)
            );

            if (clean.length >= 2) {
                result.push({
                    audio_id,
                    artist: clean[1],
                    title: clean[0],
                    href,
                    raw: clean.slice(0, 2).join(' — '),
                });
                return;
            } else if (clean.length === 1) {
                result.push({
                    audio_id,
                    artist: '',
                    title: clean[0],
                    href,
                    raw: clean[0],
                });
                return;
            }
            container = container.parentElement;
        }

        // Совсем fallback
        result.push({ audio_id, artist: '', title: '', href, raw: '' });
    });

    // Дедупликация по audio_id с приоритетом заполненных метаданных
    const byId = {};
    result.forEach(r => {
        const cur = byId[r.audio_id];
        if (!cur || (r.artist && !cur.artist) || (r.title && !cur.title)) {
            byId[r.audio_id] = r;
        }
    });
    return JSON.stringify(Object.values(byId));
})()
"""

# Клик по кнопке «→» (next page) внутри iframe
_JS_CLICK_NEXT = r"""
(() => {
    // Ищем ссылку/кнопку с aria-label "следующая страница"
    const byAria = document.querySelector('button[aria-label*="следующую"], button[aria-label*="следующ"], [aria-label*="Следующая"]');
    if (byAria) { byAria.scrollIntoView({block:'center'}); byAria.click(); return 'clicked_aria'; }

    // Ищем по data-page = текущая + 1
    const pages = [...document.querySelectorAll('[data-page]')]
                    .map(e => parseInt(e.getAttribute('data-page'), 10))
                    .filter(n => !isNaN(n));
    if (pages.length) {
        const next = Math.max(...pages);
        const btn = document.querySelector('[data-page="' + next + '"]');
        if (btn) { btn.scrollIntoView({block:'center'}); btn.click(); return 'clicked_page_' + next; }
    }

    // Fallback — скролл вниз
    window.scrollTo(0, document.body.scrollHeight);
    return 'scrolled';
})()
"""


# === CDP helpers ===

def _cdp_list_targets(cdp_host: str, cdp_port: int) -> List[dict]:
    """Возвращает список всех targets (вкладок, iframe, воркеров)."""
    try:
        r = httpx.get(f"http://{cdp_host}:{cdp_port}/json/list", timeout=5.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("vk_trending: CDP недоступен (%s:%s): %s", cdp_host, cdp_port, e)
        return []


def _pick_vklanding_iframe(targets: List[dict]) -> Optional[dict]:
    """
    Находит iframe vklanding.com — там живут хештеги.
    Если iframe нет — возвращает None.
    """
    for t in targets:
        if t.get("type") != "iframe":
            continue
        url = (t.get("url") or "").lower()
        if VKLANDING_DOMAIN in url and t.get("webSocketDebuggerUrl"):
            return t
    return None


def _pick_vk_page_fallback(targets: List[dict]) -> Optional[dict]:
    """Fallback — обычная вкладка vk.ru, если iframe не найден."""
    for t in targets:
        if t.get("type") != "page":
            continue
        url = (t.get("url") or "").lower()
        if ("vk.ru" in url or "vk.com" in url) and t.get("webSocketDebuggerUrl"):
            return t
    return None


# === Основная функция ===

def fetch_vk_trending_hashtags_sync(
    cdp_host: str = "127.0.0.1",
    cdp_port: int = 9222,
    pages: int = 2,
    wait_sec: float = 30.0,
    per_page_wait: float = 3.0,
) -> List[str]:
    """
    Возвращает список трендовых хештегов VK.

    Args:
        cdp_host, cdp_port: адрес Chrome CDP.
        pages: сколько страниц собрать (1 = только первая, 2 = первая + вторая).
        wait_sec: сколько ждать появления ссылок.
        per_page_wait: пауза после клика «следующая страница».
    """
    try:
        from websocket import create_connection
    except ImportError:
        logger.error("vk_trending: websocket-client не установлен")
        return []

    targets = _cdp_list_targets(cdp_host, cdp_port)
    if not targets:
        return []

    # Приоритет 1 — iframe vklanding.com
    target = _pick_vklanding_iframe(targets)
    is_iframe = target is not None

    # Приоритет 2 — обычная вкладка vk.ru (fallback, работает плохо)
    if target is None:
        target = _pick_vk_page_fallback(targets)
        if target is not None:
            logger.warning(
                "vk_trending: iframe vklanding не найден, падаю на вкладку vk.ru. "
                "Убедитесь, что страница vk.ru/trending открыта в Chrome."
            )

    if target is None:
        logger.warning(
            "vk_trending: ни iframe vklanding, ни вкладка vk.ru не найдены. "
            "Откройте vk.ru/trending в Chrome, где запущен CDP."
        )
        return []

    logger.info(
        "vk_trending: использую target id=%s type=%s url=%s",
        target.get("id"), target.get("type"), (target.get("url") or "")[:140],
    )

    ws = None
    result: List[str] = []
    seen: set[str] = set()

    try:
        ws = create_connection(target["webSocketDebuggerUrl"], timeout=30.0)
        msg_id = [0]

        def send(method: str, params: Optional[dict] = None, timeout_s: float = 20.0) -> dict:
            msg_id[0] += 1
            cur = msg_id[0]
            payload = {"id": cur, "method": method}
            if params:
                payload["params"] = params
            ws.send(json.dumps(payload))
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                ws.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    raw = ws.recv()
                except Exception:
                    return {}
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("id") == cur:
                    return data
            return {}

        def evaluate(expr: str, timeout_s: float = 10.0):
            res = send(
                "Runtime.evaluate",
                {"expression": expr, "returnByValue": True},
                timeout_s=timeout_s,
            )
            return (res.get("result") or {}).get("result", {}).get("value")

        send("Runtime.enable")
        send("Page.enable")

        if not is_iframe:
            # Только для fallback-вкладки vk.ru — принудительная навигация
            logger.info("vk_trending: hard reload → %s", VK_TRENDING_URL)
            send("Page.navigate", {"url": "about:blank"}, timeout_s=5.0)
            time.sleep(1.0)
            send("Page.navigate", {"url": VK_TRENDING_URL}, timeout_s=10.0)
        else:
            # Для iframe НЕ делаем навигацию — React уже загружен
            logger.info("vk_trending: работаю внутри iframe, жду ссылки")

        # Polling: ждём ссылок на хештеги
        start = time.monotonic()
        found = False
        while time.monotonic() - start < wait_sec:
            cnt = evaluate(
                "document.querySelectorAll('a[href*=\"/hashtag/\"]').length",
                timeout_s=3.0,
            )
            if isinstance(cnt, int) and cnt > 0:
                logger.info("vk_trending: ссылки появились (%d) за %.1f сек",
                            cnt, time.monotonic() - start)
                found = True
                break
            time.sleep(0.5)

        if not found:
            logger.warning("vk_trending: ссылки не появились за %.0f сек", wait_sec)

        time.sleep(1.0)

        # Собираем хештеги со страниц
        for page_num in range(1, pages + 1):
            raw = evaluate(_JS_EXTRACT, timeout_s=10.0)
            try:
                tags = json.loads(raw) if raw else []
            except Exception:
                tags = []

            added = 0
            for t in tags:
                if t and t not in seen:
                    seen.add(t)
                    result.append(t)
                    added += 1

            logger.info(
                "vk_trending: страница %d → +%d новых (всего %d)",
                page_num, added, len(result),
            )

            if page_num >= pages:
                break

            status = evaluate(_JS_CLICK_NEXT, timeout_s=5.0)
            logger.info("vk_trending: переход на стр.%d → %s", page_num + 1, status)

            if status in (None, "None"):
                logger.warning("vk_trending: не удалось перейти на стр.%d", page_num + 1)
                break

            # Ждём обновления DOM (счётчик ссылок меняется)
            time.sleep(per_page_wait)

        # Если хештегов 0 — снимаем диагностику
        if not result:
            try:
                diag_raw = evaluate(_JS_DIAG, timeout_s=10.0)
                if diag_raw:
                    try:
                        diag = json.loads(diag_raw)
                        logger.warning(
                            "vk_trending: 0 хештегов. Диагностика: %s",
                            json.dumps(diag, ensure_ascii=False)[:1000],
                        )
                    except Exception:
                        logger.warning("vk_trending: диагностика (raw): %s", str(diag_raw)[:1000])
            except Exception as e:
                logger.warning("vk_trending: диагностика упала: %s", e)

    except Exception as e:
        logger.exception("vk_trending: ошибка: %s", e)
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    logger.info("vk_trending: итог %d хештегов: %s", len(result), result[:15])
    return result


def fetch_vk_trending_tracks_sync(
    cdp_host: str = "127.0.0.1",
    cdp_port: int = 9222,
) -> List[dict]:
    """
    Возвращает список трендовых треков VK с https://vk.ru/trending.
    Каждый элемент: {artist, title, raw, href, audio_id}.
    Работает через CDP-подключение к iframe vklanding.com.
    """
    try:
        from websocket import create_connection
    except ImportError:
        logger.error("vk_trending: websocket-client не установлен")
        return []

    targets = _cdp_list_targets(cdp_host, cdp_port)
    if not targets:
        return []

    target = _pick_vklanding_iframe(targets)
    if target is None:
        logger.warning("vk_trending: iframe vklanding не найден (для музыки)")
        return []

    ws = None
    try:
        ws = create_connection(target["webSocketDebuggerUrl"], timeout=30.0)
        msg_id = [0]

        def send(method: str, params: Optional[dict] = None, timeout_s: float = 20.0) -> dict:
            msg_id[0] += 1
            cur = msg_id[0]
            payload = {"id": cur, "method": method}
            if params:
                payload["params"] = params
            ws.send(json.dumps(payload))
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                ws.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    raw = ws.recv()
                except Exception:
                    return {}
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("id") == cur:
                    return data
            return {}

        def evaluate(expr: str, timeout_s: float = 10.0):
            res = send(
                "Runtime.evaluate",
                {"expression": expr, "returnByValue": True},
                timeout_s=timeout_s,
            )
            return (res.get("result") or {}).get("result", {}).get("value")

        send("Runtime.enable")

        # Скроллим вниз, чтобы блок "музыка в тренде" точно был в DOM
        evaluate("window.scrollTo(0, document.body.scrollHeight)", timeout_s=5.0)
        time.sleep(2.0)

        raw = evaluate(_JS_EXTRACT_MUSIC, timeout_s=10.0)
        try:
            items = json.loads(raw) if raw else []
        except Exception:
            items = []

        logger.info("vk_trending: найдено %d треков", len(items))
        return items

    except Exception as e:
        logger.exception("vk_trending: ошибка извлечения треков: %s", e)
        return []
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass