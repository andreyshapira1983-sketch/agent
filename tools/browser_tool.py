# Browser Tool — инструмент Tool Layer (Слой 5)
# Архитектура автономного AI-агента
# Управление браузером через Playwright: навигация, клик, заполнение форм, скриншоты.
# pylint: disable=broad-except,unused-import

from __future__ import annotations

import os
import time
import base64
import random
import functools
from pathlib import Path
from typing import Any

# ── Современные User-Agent строки (Chrome 130–136, 2025) ─────────────────────
_USER_AGENTS: list[str] = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
]

# ── Stealth JS — патчи для обхода антибот-детекта ─────────────────────────────
_STEALTH_JS = """
// navigator.webdriver → false
Object.defineProperty(navigator, 'webdriver', { get: () => false });

// chrome runtime — имитация настоящего Chrome
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};

// permissions — Notification permission 'default'
if (navigator.permissions) {
    const _query = navigator.permissions.query;
    navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : _query.call(navigator.permissions, params);
}

// plugins — имитация реальных плагинов
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});

// languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en', 'ru'],
});

// platform
Object.defineProperty(navigator, 'platform', {
    get: () => 'Win32',
});
"""


def _retry(attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """Декоратор retry с экспоненциальным отступом для нестабильных операций."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            wait = delay
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < attempts - 1:
                        time.sleep(wait)
                        wait *= backoff
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


class BrowserPage:
    """Результат загрузки страницы."""

    def __init__(self, url: str, title: str, text: str, html: str,
                 screenshot_b64: str | None = None, links: list[str] | None = None,
                 status: int = 200, success: bool = True, error: str | None = None):
        self.url = url
        self.title = title
        self.text = text
        self.html = html
        self.screenshot_b64 = screenshot_b64
        self.links = links or []
        self.status = status
        self.success = success
        self.error = error

    def to_dict(self) -> dict:
        return {
            'url': self.url,
            'title': self.title,
            'text_preview': self.text[:500],
            'links_count': len(self.links),
            'status': self.status,
            'success': self.success,
            'error': self.error,
        }


class BrowserTool:
    """
    Web Browser Tool — Слой 5.

    Управляет headless-браузером через Playwright для полноценного
    взаимодействия с веб-страницами (JavaScript, динамический контент).

    Установка: pip install playwright && playwright install chromium

    Методы:
        navigate(url)               — открыть страницу
        click(selector)             — кликнуть элемент
        fill(selector, text)        — заполнить поле
        submit(selector)            — нажать кнопку
        screenshot(path)            — сделать скриншот
        extract_text()              — извлечь текст страницы
        get_links()                 — список ссылок
        wait_for(selector, timeout) — дождаться элемента
        close()                     — закрыть браузер

    Fallback: если Playwright не установлен — делегирует в WebCrawler (requests).
    """

    def __init__(self, headless: bool = True, timeout: int = 30000,
                 monitoring=None, web_crawler=None,
                 proxy: str | None = None,
                 storage_state_path: str | None = None):
        """
        Args:
            headless           — запуск без GUI (True для серверов)
            timeout            — таймаут операций в мс
            web_crawler        — fallback если Playwright недоступен
            proxy              — прокси-сервер, формат 'http://user:pass@host:port'
            storage_state_path — путь к файлу cookie/storage state для persistent-сессий
        """
        self.name = 'browser'
        self.description = 'Headless-браузер (Playwright) для взаимодействия с веб-страницами'
        self.headless = headless
        self.timeout = timeout
        self.monitoring = monitoring
        self.web_crawler = web_crawler
        self.proxy = proxy
        self.storage_state_path = storage_state_path

        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._playwright: Any = None
        self._pages: list[Any] = []           # все открытые вкладки
        self._backend = 'none'
        self._current_url = ''
        self._dialog_messages: list[str] = [] # перехваченные alert/confirm/prompt

    # ── Инициализация ─────────────────────────────────────────────────────────

    def _ensure_browser(self) -> bool:
        """Запускает браузер если ещё не запущен. Stealth + proxy + cookies."""
        if self._page is not None:
            return True

        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()

            launch_kw: dict[str, Any] = {'headless': self.headless}
            if self.proxy:
                launch_kw['proxy'] = {'server': self.proxy}

            self._browser = self._playwright.chromium.launch(**launch_kw)

            # ── Контекст: UA rotation + viewport + storage state ──
            ctx_kw: dict[str, Any] = {
                'user_agent': random.choice(_USER_AGENTS),
                'viewport': {'width': 1280, 'height': 720},
                'locale': 'en-US',
                'timezone_id': 'Europe/Moscow',
                'color_scheme': 'light',
            }
            # Загрузка сохранённых cookie/storage
            if self.storage_state_path and Path(self.storage_state_path).exists():
                ctx_kw['storage_state'] = self.storage_state_path
                self._log(f'Загружен storage_state: {self.storage_state_path}')

            self._context = self._browser.new_context(**ctx_kw)

            # ── Stealth-init скрипт (до любой навигации) ──
            self._context.add_init_script(_STEALTH_JS)

            self._page = self._context.new_page()
            self._page.set_default_timeout(self.timeout)
            self._pages = [self._page]

            # ── Dialog handler (alert/confirm/prompt) ──
            def _on_dialog(dialog):
                self._dialog_messages.append(
                    f'{dialog.type}: {dialog.message}'
                )
                self._log(f'dialog intercepted: {dialog.type} — {dialog.message[:80]}')
                dialog.dismiss()
            self._page.on('dialog', _on_dialog)

            self._backend = 'playwright'
            self._log('Playwright браузер запущен (stealth mode)')
            return True
        except ImportError:
            self._backend = 'crawler'
            self._log(
                'Playwright не установлен. '
                'Установи: pip install playwright && playwright install chromium',
                level='warning',
            )
            return False
        except Exception as e:
            self._backend = 'crawler'
            self._log(f'Ошибка запуска браузера: {e}', level='error')
            return False

    # ── Навигация ─────────────────────────────────────────────────────────────

    def navigate(self, url: str, wait_until: str = 'domcontentloaded') -> BrowserPage:
        """
        Открывает URL и возвращает содержимое страницы.
        Retry до 3 раз при сетевых ошибках.

        Args:
            url        — адрес страницы
            wait_until — 'load' / 'domcontentloaded' / 'networkidle'
        """
        if not self._ensure_browser() or self._backend == 'crawler':
            return self._fallback_fetch(url)

        last_err = None
        for attempt in range(3):
            try:
                response = self._page.goto(url, wait_until=wait_until,
                                            timeout=self.timeout)
                self._current_url = self._page.url
                status = response.status if response else 200

                title = self._page.title()
                text = self._extract_text_pw()
                html = self._page.content()
                links = self._extract_links_pw()

                self._log(f'navigate: {url} -> {status}')
                return BrowserPage(
                    url=self._current_url,
                    title=title,
                    text=text,
                    html=html,
                    links=links,
                    status=status,
                    success=200 <= status < 400,
                )
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    self._log(f'navigate retry {attempt + 1}: {e}', level='warning')

        self._log(f'navigate error (3 attempts): {last_err}', level='error')
        return BrowserPage(url=url, title='', text='', html='',
                           success=False, error=str(last_err))

    # ── Взаимодействие ────────────────────────────────────────────────────────

    def click(self, selector: str) -> bool:
        """Кликает элемент по CSS/XPath селектору."""
        if not self._page:
            return False
        try:
            self._page.click(selector, timeout=self.timeout)
            return True
        except Exception as e:
            self._log(f'click({selector}): {e}', level='error')
            return False

    def fill(self, selector: str, text: str) -> bool:
        """Заполняет текстовое поле."""
        if not self._page:
            return False
        try:
            self._page.fill(selector, text)
            return True
        except Exception as e:
            self._log(f'fill({selector}): {e}', level='error')
            return False

    def press(self, selector: str, key: str) -> bool:
        """Нажимает клавишу в элементе (например Enter)."""
        if not self._page:
            return False
        try:
            self._page.press(selector, key)
            return True
        except Exception as e:
            self._log(f'press({selector}, {key}): {e}', level='error')
            return False

    def wait_for(self, selector: str, timeout: int | None = None) -> bool:
        """Ожидает появления элемента."""
        if not self._page:
            return False
        try:
            self._page.wait_for_selector(
                selector, timeout=timeout or self.timeout
            )
            return True
        except Exception:
            return False

    def select_option(self, selector: str, value: str | None = None,
                      label: str | None = None, index: int | None = None) -> bool:
        """Выбирает опцию в <select> по value, label или index."""
        if not self._page:
            return False
        try:
            kw: dict[str, Any] = {}
            if value is not None:
                kw['value'] = value
            elif label is not None:
                kw['label'] = label
            elif index is not None:
                kw['index'] = index
            self._page.select_option(selector, **kw)
            return True
        except Exception as e:
            self._log(f'select_option({selector}): {e}', level='error')
            return False

    def hover(self, selector: str) -> bool:
        """Наводит курсор на элемент (для выпадающих меню, тултипов)."""
        if not self._page:
            return False
        try:
            self._page.hover(selector, timeout=self.timeout)
            return True
        except Exception as e:
            self._log(f'hover({selector}): {e}', level='error')
            return False

    def set_input_files(self, selector: str, files: str | list[str]) -> bool:
        """Загружает файл(ы) в <input type='file'>."""
        if not self._page:
            return False
        try:
            self._page.set_input_files(selector, files)
            return True
        except Exception as e:
            self._log(f'set_input_files({selector}): {e}', level='error')
            return False

    def evaluate(self, js: str):
        """Выполняет JavaScript на странице."""
        if not self._page:
            return None
        try:
            return self._page.evaluate(js)
        except Exception as e:
            self._log(f'evaluate: {e}', level='error')
            return None

    def scroll(self, x: int = 0, y: int = 500):
        """Прокручивает страницу."""
        if self._page:
            self._page.evaluate(f'window.scrollBy({x}, {y})')

    # ── Извлечение контента ───────────────────────────────────────────────────

    def get_text(self) -> str:
        """Возвращает текстовое содержимое текущей страницы."""
        if not self._page:
            return ''
        return self._extract_text_pw()

    def get_links(self) -> list[str]:
        """Возвращает все ссылки текущей страницы."""
        if not self._page:
            return []
        return self._extract_links_pw()

    def get_element_text(self, selector: str) -> str:
        """Возвращает текст элемента."""
        if not self._page:
            return ''
        try:
            return self._page.locator(selector).first.inner_text()
        except Exception:
            return ''

    def get_attribute(self, selector: str, attribute: str) -> str:
        """Возвращает атрибут элемента."""
        if not self._page:
            return ''
        try:
            return self._page.get_attribute(selector, attribute) or ''
        except Exception:
            return ''

    # ── Скриншоты и PDF ─────────────────────────────────────────────────────────

    def screenshot(self, path: str | None = None, full_page: bool = True) -> str | None:
        """
        Делает скриншот. Если path указан — сохраняет файл.
        Возвращает base64-строку PNG.
        """
        if not self._page:
            return None
        try:
            kwargs: dict = {'full_page': full_page}
            if path:
                os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
                kwargs['path'] = path
            data = self._page.screenshot(**kwargs)
            return base64.b64encode(data).decode('utf-8')
        except Exception as e:
            self._log(f'screenshot: {e}', level='error')
            return None

    def pdf(self, path: str | None = None) -> bytes | None:
        """Сохраняет страницу в PDF (только headless Chromium)."""
        if not self._page:
            return None
        try:
            kwargs: dict = {}
            if path:
                os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
                kwargs['path'] = path
            return self._page.pdf(**kwargs)
        except Exception as e:
            self._log(f'pdf: {e}', level='error')
            return None

    # ── Управление вкладками ────────────────────────────────────────────────────

    def new_tab(self) -> bool:
        """Открывает новую вкладку и переключается на неё."""
        if not self._context:
            return False
        try:
            page = self._context.new_page()
            page.set_default_timeout(self.timeout)
            # dialog handler и для новой вкладки
            def _on_dialog(dialog):
                self._dialog_messages.append(f'{dialog.type}: {dialog.message}')
                dialog.dismiss()
            page.on('dialog', _on_dialog)
            self._pages.append(page)
            self._page = page
            return True
        except Exception:
            return False

    def switch_tab(self, index: int) -> bool:
        """Переключается на вкладку по индексу (0-based)."""
        if 0 <= index < len(self._pages):
            self._page = self._pages[index]
            return True
        return False

    def list_tabs(self) -> list[dict]:
        """Возвращает список открытых вкладок [{index, url, title}]."""
        tabs = []
        for i, p in enumerate(self._pages):
            try:
                tabs.append({'index': i, 'url': p.url, 'title': p.title()})
            except Exception:
                tabs.append({'index': i, 'url': '(closed)', 'title': ''})
        return tabs

    def close_tab(self, index: int | None = None) -> bool:
        """Закрывает вкладку по индексу. Без аргумента — текущую."""
        try:
            idx = index if index is not None else self._pages.index(self._page)
            if 0 <= idx < len(self._pages):
                self._pages[idx].close()
                self._pages.pop(idx)
                if self._pages:
                    self._page = self._pages[min(idx, len(self._pages) - 1)]
                else:
                    self._page = None
                return True
        except Exception:
            pass
        return False

    # ── Cookie / Storage State ────────────────────────────────────────────────

    def save_storage_state(self, path: str | None = None) -> str | None:
        """Сохраняет cookies + localStorage в файл. Возвращает путь."""
        if not self._context:
            return None
        save_path = path or self.storage_state_path
        if not save_path:
            save_path = 'state/browser_storage.json'
        try:
            os.makedirs(os.path.dirname(save_path) or '.', exist_ok=True)
            self._context.storage_state(path=save_path)
            self._log(f'Storage state saved → {save_path}')
            return save_path
        except Exception as e:
            self._log(f'save_storage_state: {e}', level='error')
            return None

    def get_cookies(self) -> list[dict]:
        """Возвращает все cookies текущего контекста."""
        if not self._context:
            return []
        try:
            return self._context.cookies()
        except Exception:
            return []

    def set_cookies(self, cookies: list[dict]) -> bool:
        """Устанавливает cookies. Каждый cookie: {name, value, url или domain+path}."""
        if not self._context:
            return False
        try:
            self._context.add_cookies(cookies)
            return True
        except Exception as e:
            self._log(f'set_cookies: {e}', level='error')
            return False

    # ── CAPTCHA Detection ─────────────────────────────────────────────────────

    def detect_captcha(self) -> dict:
        """
        Проверяет наличие CAPTCHA/challenge на странице.
        Возвращает {'detected': bool, 'type': str, 'details': str}.
        """
        if not self._page:
            return {'detected': False, 'type': '', 'details': ''}

        markers = self._page.evaluate('''() => {
            const html = document.documentElement.innerHTML;
            const text = (document.body && document.body.innerText) || '';
            const results = [];

            // reCAPTCHA
            if (document.querySelector('iframe[src*="recaptcha"]') ||
                document.querySelector('.g-recaptcha'))
                results.push('recaptcha');

            // hCaptcha
            if (document.querySelector('iframe[src*="hcaptcha"]') ||
                document.querySelector('.h-captcha'))
                results.push('hcaptcha');

            // Cloudflare challenge
            if (document.querySelector('#challenge-form') ||
                document.querySelector('#cf-challenge-running') ||
                text.includes('Checking your browser') ||
                text.includes('Verify you are human'))
                results.push('cloudflare');

            // Amazon CAPTCHA
            if (html.includes('captcha') && html.includes('amzn'))
                results.push('amazon');

            // Generic
            if (document.querySelector('input[name*="captcha"]') ||
                (text.toLowerCase().includes('captcha') && !results.length))
                results.push('generic');

            return results;
        }''') or []

        if markers:
            captcha_type = markers[0]
            return {
                'detected': True,
                'type': captcha_type,
                'details': f'CAPTCHA detected: {", ".join(markers)} on {self._page.url}',
            }
        return {'detected': False, 'type': '', 'details': ''}

    # ── Network Interception ──────────────────────────────────────────────────

    def block_resources(self, resource_types: list[str] | None = None) -> bool:
        """
        Блокирует загрузку ресурсов (изображения, шрифты, реклама).
        resource_types: ['image', 'font', 'media', 'stylesheet'] и т.д.
        """
        if not self._page:
            return False
        blocked = set(resource_types or ['image', 'font', 'media'])
        try:
            def _route_handler(route):
                if route.request.resource_type in blocked:
                    route.abort()
                else:
                    route.continue_()
            self._page.route('**/*', _route_handler)
            self._log(f'Blocked resource types: {blocked}')
            return True
        except Exception as e:
            self._log(f'block_resources: {e}', level='error')
            return False

    # ── iframe support ────────────────────────────────────────────────────────

    def iframe_locator(self, frame_selector: str):
        """
        Возвращает frame_locator для работы с iframe.
        Использование: bt.iframe_locator('#my-iframe').locator('button').click()
        """
        if not self._page:
            return None
        try:
            return self._page.frame_locator(frame_selector)
        except Exception as e:
            self._log(f'iframe_locator({frame_selector}): {e}', level='error')
            return None

    # ── Structured Extraction ─────────────────────────────────────────────────

    def extract_table(self, selector: str = 'table') -> list[list[str]]:
        """Извлекает таблицу как список строк [[cell, cell, ...], ...]."""
        if not self._page:
            return []
        try:
            return self._page.evaluate('''(sel) => {
                const table = document.querySelector(sel);
                if (!table) return [];
                return [...table.querySelectorAll("tr")].map(row =>
                    [...row.querySelectorAll("th,td")].map(c => c.innerText.trim())
                );
            }''', selector) or []
        except Exception:
            return []

    def extract_structured(self, selector: str) -> list[dict]:
        """Извлекает список элементов как [{tag, text, href, src}]."""
        if not self._page:
            return []
        try:
            return self._page.evaluate('''(sel) => {
                return [...document.querySelectorAll(sel)].slice(0, 100).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || '').substring(0, 200),
                    href: el.href || el.closest('a')?.href || '',
                    src: el.src || '',
                }));
            }''', selector) or []
        except Exception:
            return []

    # ── Close ─────────────────────────────────────────────────────────────────

    def close(self):
        """Закрывает браузер. Автосохранение cookie если путь задан."""
        try:
            # Автосохранение storage state перед закрытием
            if self.storage_state_path and self._context:
                self.save_storage_state(self.storage_state_path)
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._browser = None
            self._context = None
            self._page = None
            self._pages = []
            self._playwright = None
            self._log('Браузер закрыт')

    @property
    def current_url(self) -> str:
        if self._page:
            try:
                return self._page.url
            except Exception:
                pass
        return self._current_url

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_text_pw(self) -> str:
        """Извлекает чистый текст через Playwright (включая Shadow DOM)."""
        try:
            return self._page.evaluate(
                '''() => {
                    // Рекурсивно извлекаем текст из Shadow DOM
                    function getText(node) {
                        let text = '';
                        if (node.nodeType === Node.TEXT_NODE) {
                            return node.textContent || '';
                        }
                        // Пропускаем script/style/nav/footer/aside
                        const tag = (node.tagName || '').toLowerCase();
                        if (['script','style','nav','footer','aside','noscript'].includes(tag)) {
                            return '';
                        }
                        // Заходим в Shadow Root
                        if (node.shadowRoot) {
                            for (const child of node.shadowRoot.childNodes) {
                                text += getText(child);
                            }
                        }
                        for (const child of node.childNodes) {
                            text += getText(child);
                        }
                        // Добавляем переводы строк для блочных элементов
                        if (['div','p','h1','h2','h3','h4','h5','h6','li','tr','br','section','article'].includes(tag)) {
                            text += '\\n';
                        }
                        return text;
                    }
                    const body = document.body || document.documentElement;
                    return getText(body).replace(/\\n{3,}/g, '\\n\\n').trim();
                }'''
            ) or ''
        except Exception:
            try:
                return self._page.inner_text('body') or ''
            except Exception:
                return ''

    def _extract_links_pw(self) -> list[str]:
        """Извлекает все ссылки."""
        try:
            hrefs = self._page.evaluate(
                '() => Array.from(document.querySelectorAll("a[href]"))'
                '.map(a => a.href).filter(h => h.startsWith("http"))'
            )
            return hrefs[:100]
        except Exception:
            return []

    def _fallback_fetch(self, url: str) -> BrowserPage:
        """Fallback через WebCrawler если Playwright недоступен."""
        if self.web_crawler:
            try:
                result = self.web_crawler.fetch(url)
                return BrowserPage(
                    url=result.get('url', url),
                    title=result.get('title', ''),
                    text=result.get('text', ''),
                    html='',
                    links=result.get('links', []),
                    status=result.get('status', 200),
                    success=result.get('success', True),
                )
            except Exception as e:
                return BrowserPage(url=url, title='', text='', html='',
                                   success=False, error=str(e))
        return BrowserPage(
            url=url, title='', text='', html='',
            success=False,
            error='Playwright не установлен. pip install playwright && playwright install chromium',
        )

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source='browser_tool'
            )
        else:
            print(f'[BrowserTool] {message}')

    def __del__(self):
        """Автоматически закрываем браузер при удалении объекта."""
        try:
            self.close()
        except Exception:
            pass
