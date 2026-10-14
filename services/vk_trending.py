"""
Скрапер трендовых хештегов и треков VK для Клипов.
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
  5. Извлекаем хештеги ТОЛЬКО из секции #хештеги.
  6. Кликаем «вперёд» ТОЛЬКО внутри #хештеги (там своя пагинация)
     и ждём фактической смены активной страницы.
"""
import json
import logging
import time
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

VK_TRENDING_URL = "https://vk.ru/trending"
VKLANDING_DOMAIN = "vklanding.com"

# Сколько ждать смены активной страницы после клика «вперёд»
PAGE_CHANGE_TIMEOUT = 6.0
PAGE_CHANGE_POLL = 0.25


# ============================================================
# JS-скрипты
# ============================================================

# Извлекает хештеги ТОЛЬКО из секции #хештеги
_JS_EXTRACT = r"""
(() => {
    const section = document.getElementById('хештеги');
    if (!section) {
        // fallback — весь документ, если секции нет
        var scope = document;
    } else {
        var scope = section;
    }
    const out = new Set();
    scope.querySelectorAll('a[href*="/hashtag/"]').forEach(a => {
        const h = a.getAttribute('href') || '';
        const m = h.match(/hashtag\/([^\/?#]+)/);
        if (!m) return;
        try {
            const tag = decodeURIComponent(m[1]).trim();
            if (tag && tag.length <= 50) out.add('#' + tag);
        } catch (e) {}
    });
    return JSON.stringify([...out]);
})()
"""

# Текущая активная страница внутри секции #хештеги
_JS_GET_CURRENT_PAGE = r"""
(() => {
    const section = document.getElementById('хештеги');
    if (!section) return -1;
    const nav = section.querySelector('nav[role="navigation"]');
    if (!nav) return -1;
    const active = nav.querySelector('[data-page][aria-current="true"]');
    if (!active) return -1;
    const n = parseInt(active.getAttribute('data-page'), 10);
    return isNaN(n) ? -1 : n;
})()
"""

# Клик по «вперёд» — ТОЛЬКО внутри секции #хештеги
_JS_CLICK_NEXT = r"""
(() => {
    const section = document.getElementById('хештеги');
    if (!section) return 'no_section';
    const nav = section.querySelector('nav[role="navigation"]');
    if (!nav) return 'no_nav';

    // 1. Приоритет — кнопка «вперёд» (nextButtonContainer)
    const nextContainer = nav.querySelector('.vkuiPagination__nextButtonContainer');
    if (nextContainer) {
        const btn = nextContainer.querySelector('button');
        if (btn) {
            const disabled = btn.disabled
                || btn.classList.contains('vkuiButton__disabled');
            if (disabled) return 'last_page';
            btn.scrollIntoView({block: 'center'});
            btn.click();
            return 'clicked_next';
        }
    }

    // 2. Fallback — клик по следующему data-page
    const pages = [...nav.querySelectorAll('[data-page]')];
    let current = 0;
    for (const p of pages) {
        if (p.getAttribute('aria-current') === 'true') {
            current = parseInt(p.getAttribute('data-page'), 10) || 0;
            break;
        }
    }
    const targetPage = current + 1;
    for (const p of pages) {
        if (parseInt(p.getAttribute('data-page'), 10) === targetPage) {
            p.scrollIntoView({block: 'center'});
            p.click();
            return 'clicked_page_' + targetPage;
        }
    }
    return 'no_next_button';
})()
"""

# Клик по «назад» / переход на первую страницу — ТОЛЬКО внутри #хештеги
_JS_GO_TO_FIRST_PAGE = r"""
(() => {
    const section = document.getElementById('хештеги');
    if (!section) return 'no_section';
    const nav = section.querySelector('nav[role="navigation"]');
    if (!nav) return 'no_nav';

    // 1. Уже на 1-й?
    const active = nav.querySelector('[data-page][aria-current="true"]');
    if (active && active.getAttribute('data-page') === '1') {
        return 'already_first';
    }

    // 2. Клик по кнопке «назад» (prevButtonContainer)
    const prevContainer = nav.querySelector('.vkuiPagination__prevButtonContainer');
    if (prevContainer) {
        const btn = prevContainer.querySelector('button');
        if (btn) {
            const disabled = btn.disabled
                || btn.classList.contains('vkuiButton__disabled');
            if (!disabled) {
                btn.scrollIntoView({block: 'center'});
                btn.click();
                return 'clicked_prev';
            }
        }
    }

    // 3. Fallback — клик по самой первой странице data-page="1"
    for (const p of nav.querySelectorAll('[data-page]')) {
        if (p.getAttribute('data-page') === '1') {
            p.scrollIntoView({block: 'center'});
            p.click();
            return 'clicked_page_1';
        }
    }
    return 'no_first_button';
})()
"""

_JS_DIAG = r"""
(() => {
    const section = document.getElementById('хештеги');
    const nav = section ? section.querySelector('nav[role="navigation"]') : null;
    return JSON.stringify({
        href: location.href,
        title: document.title,
        ready: document.readyState,
        body_len: document.body ? document.body.innerText.length : -1,
        links_total: document.querySelectorAll('a').length,
        hashtag_slash: document.querySelectorAll('a[href*="/hashtag/"]').length,
        hashtag_in_section: section
            ? section.querySelectorAll('a[href*="/hashtag/"]').length
            : -1,
        section_present: !!section,
        nav_present: !!nav,
        data_pages: nav
            ? [...nav.querySelectorAll('[data-page]')]
                .map(e => e.getAttribute('data-page'))
            : [],
        has_login: !!document.querySelector('input[type="password"]'),
        body_preview: document.body
            ? document.body.innerText.slice(0, 300)
            : null,
    });
})()
"""

# Извлекает трендовые треки из блока «музыка в тренде»
_JS_EXTRACT_MUSIC = r"""
(() => {
    const section = document.getElementById('музыка') || document;
    const result = [];
    const seen = new Set();

    section.querySelectorAll('a[href*="/clips/music/"]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/clips\/music\/(-?\d+_\d+)/);
        if (!m) return;
        const audio_id = m[1];
        if (seen.has(audio_id)) return;
        seen.add(audio_id);

        let container = a.closest('div, li, article');
        for (let i = 0; i < 6 && container; i++) {
            const leaves = [...container.querySelectorAll('span, div, a')]
                .filter(el => el.children.length === 0)
                .map(el => (el.textContent || '').trim())
                .filter(t => t && t.length > 1 && t.length < 100 &&
                             !/^(смотреть|клипы|музыка|в тренде|страниц)/i.test(t));

            const clean = leaves.filter(t =>
                !t.includes('Перейти') &&
                !t.includes('следующ') &&
                !t.includes('назад') &&
                !/^\d+$/.test(t)
            );

            if (clean.length >= 2) {
                result.push({
                    audio_id,
                    title: clean[0],
                    artist: clean[1],
                    href,
                    raw: clean.slice(0, 2).join(' — '),
                });
                return;
            } else if (clean.length === 1) {
                result.push({
                    audio_id,
                    title: clean[0],
                    artist: '',
                    href,
                    raw: clean[0],
                });
                return;
            }
            container = container.parentElement;
        }

        result.push({ audio_id, artist: '', title: '', href, raw: '' });
    });

    // Дедупликация с приоритетом заполненных метаданных
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


# ============================================================
# CDP helpers
# ============================================================

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
    """Находит iframe vklanding.com — там живут хештеги."""
    for t in targets:
        if t.get("type") != "iframe":
            continue
        url = (t.get("url") or "").lower()
        if VKLANDING_DOMAIN in url and t.get("webSocketDebuggerUrl"):
            return t
    return None


def _pick_vk_page_fallback(targets: List[dict]) -> Optional[dict]:
    """Fallback — обычная вкладка vk.ru."""
    for t in targets:
        if t.get("type") != "page":
            continue
        url = (t.get("url") or "").lower()
        if ("vk.ru" in url or "vk.com" in url) and t.get("webSocketDebuggerUrl"):
            return t
    return None


# ============================================================
# Хештеги
# ============================================================

def fetch_vk_trending_hashtags_sync(
    cdp_host: str = "127.0.0.1",
    cdp_port: int = 9222,
    pages: int = 3,
    wait_sec: float = 30.0,
    per_page_wait: float = 3.0,
) -> List[str]:
    """
    Возвращает список трендовых хештегов VK.

    Args:
        cdp_host, cdp_port: адрес Chrome CDP.
        pages: сколько страниц собрать.
        wait_sec: сколько ждать появления ссылок.
        per_page_wait: пауза после клика «следующая страница» (страховка).
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
    is_iframe = target is not None

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
        "vk_trending: target id=%s type=%s url=%s",
        target.get("id"), target.get("type"), (target.get("url") or "")[:140],
    )

    ws = None
    result: List[str] = []
    seen: set[str] = set()

    try:
        ws = create_connection(target["webSocketDebuggerUrl"], timeout=30.0)
        msg_id = [0]

        def send(method: str, params: Optional[dict] = None,
                 timeout_s: float = 20.0) -> dict:
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
            logger.info("vk_trending: hard reload → %s", VK_TRENDING_URL)
            send("Page.navigate", {"url": "about:blank"}, timeout_s=5.0)
            time.sleep(1.0)
            send("Page.navigate", {"url": VK_TRENDING_URL}, timeout_s=10.0)
        else:
            logger.info("vk_trending: работаю внутри iframe, жду ссылки")

        # Ждём появления ссылок на хештеги
        start = time.monotonic()
        found = False
        while time.monotonic() - start < wait_sec:
            cnt = evaluate(
                "document.querySelectorAll('a[href*=\"/hashtag/\"]').length",
                timeout_s=3.0,
            )
            if isinstance(cnt, int) and cnt > 0:
                logger.info(
                    "vk_trending: ссылки появились (%d) за %.1f сек",
                    cnt, time.monotonic() - start,
                )
                found = True
                break
            time.sleep(0.5)

        if not found:
            logger.warning("vk_trending: ссылки не появились за %.0f сек", wait_sec)

        time.sleep(1.0)

        # === Принудительно возвращаемся на 1-ю страницу ===
        current_page_before = evaluate(_JS_GET_CURRENT_PAGE, timeout_s=5.0)
        logger.info("vk_trending: активная стр. перед стартом — %s", current_page_before)

        # Кликаем «назад», пока не окажемся на стр.1
        # (виджет мог остаться на любой странице от прошлого прогона)
        max_back_clicks = max(pages + 5, 10)
        for i in range(max_back_clicks):
            cur = evaluate(_JS_GET_CURRENT_PAGE, timeout_s=3.0)
            if cur == 1:
                if i > 0:
                    logger.info(
                        "vk_trending: вернулся на стр.1 после %d кликов", i,
                    )
                break

            status = evaluate(_JS_GO_TO_FIRST_PAGE, timeout_s=5.0)
            if status in ("already_first",):
                logger.info("vk_trending: уже на стр.1")
                break
            if status in ("no_section", "no_nav", "no_first_button"):
                logger.warning(
                    "vk_trending: «назад» недоступен (status=%s, cur=%s), "
                    "собираю с текущей страницы", status, cur,
                )
                break

            # Клик был — ждём смены активной страницы
            deadline = time.monotonic() + PAGE_CHANGE_TIMEOUT
            changed = False
            while time.monotonic() < deadline:
                time.sleep(PAGE_CHANGE_POLL)
                cur_now = evaluate(_JS_GET_CURRENT_PAGE, timeout_s=3.0)
                if cur_now != cur and cur_now != -1:
                    changed = True
                    break
            if not changed:
                logger.warning(
                    "vk_trending: страница не сменилась за %.1f сек, стоп сброс",
                    PAGE_CHANGE_TIMEOUT,
                )
                break

        # Даём React перерисовать список
        time.sleep(0.7)

        start_page = evaluate(_JS_GET_CURRENT_PAGE, timeout_s=3.0)
        logger.info("vk_trending: старт сбора со стр.%s", start_page)

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

            # Кликаем «вперёд» и ждём смены активной страницы
            cur_before = evaluate(_JS_GET_CURRENT_PAGE, timeout_s=5.0)
            status = evaluate(_JS_CLICK_NEXT, timeout_s=5.0)
            logger.info(
                "vk_trending: клик «вперёд» (стр.%s) → %s",
                cur_before, status,
            )

            if status in ("last_page", "no_next_button", "no_section", "no_nav"):
                logger.info("vk_trending: дошли до конца пагинации, стоп")
                break

            # Ждём фактической смены активной страницы
            changed = False
            deadline = time.monotonic() + PAGE_CHANGE_TIMEOUT
            while time.monotonic() < deadline:
                time.sleep(PAGE_CHANGE_POLL)
                cur_now = evaluate(_JS_GET_CURRENT_PAGE, timeout_s=3.0)
                if cur_now != cur_before and cur_now != -1:
                    changed = True
                    logger.info(
                        "vk_trending: страница сменилась %s → %s",
                        cur_before, cur_now,
                    )
                    break

            if not changed:
                logger.warning(
                    "vk_trending: страница не сменилась за %.1f сек, "
                    "жду %.1f сек как fallback",
                    PAGE_CHANGE_TIMEOUT, per_page_wait,
                )
                time.sleep(per_page_wait)

            # Даём React перерисовать список
            time.sleep(0.7)

        if not result:
            try:
                diag_raw = evaluate(_JS_DIAG, timeout_s=10.0)
                if diag_raw:
                    try:
                        diag = json.loads(diag_raw)
                        logger.warning(
                            "vk_trending: 0 хештегов. Диагностика: %s",
                            json.dumps(diag, ensure_ascii=False)[:1200],
                        )
                    except Exception:
                        logger.warning(
                            "vk_trending: диагностика (raw): %s",
                            str(diag_raw)[:1200],
                        )
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

    logger.info("vk_trending: итог %d хештегов: %s", len(result), result[:20])
    return result


# ============================================================
# Треки
# ============================================================

def fetch_vk_trending_tracks_sync(
    cdp_host: str = "127.0.0.1",
    cdp_port: int = 9222,
) -> List[dict]:
    """
    Возвращает список трендовых треков VK с https://vk.ru/trending.
    Каждый элемент: {artist, title, raw, href, audio_id}.
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

        def send(method: str, params: Optional[dict] = None,
                 timeout_s: float = 20.0) -> dict:
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

        # Скроллим к блоку «музыка»
        evaluate(
            "(() => { const s = document.getElementById('музыка'); "
            "if (s) s.scrollIntoView({block: 'start'}); "
            "else window.scrollTo(0, document.body.scrollHeight); })()",
            timeout_s=5.0,
        )
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